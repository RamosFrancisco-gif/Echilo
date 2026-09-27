"""Testes das regras do ciclo de vida do imóvel (§2.8) e da documentação (§2.7)."""

from __future__ import annotations

import json
import math
import os
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.formsets import BaseFormSet
from django.forms.models import inlineformset_factory as admin_formset_factory
from django.http import HttpResponse
from django.templatetags.static import static
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.geo import haversine_metres
from apps.core.templatetags.echilo_format import coordinate
from apps.core.testing import (
    ids_dentro_de,
    input_names_inside,
    jpeg_bytes,
    make_image,
    make_owner,
    make_property,
    make_submission,
    make_user,
    make_verified_documents,
)
from apps.core.validators import MAX_SEARCH_RADIUS_M
from apps.properties.forms import (
    PropertyCuratorForm,
    PropertyImageUploadForm,
    PropertyQuickEditForm,
)
from apps.properties.management.commands.build_admin_boundaries import (
    FICHEIRO_MUNICIPIOS,
    FICHEIRO_PROVINCIAS,
    TOLERANCIA,
    aneis_de,
    chave,
    dentro,
    limites,
    sem_acentos,
    variantes,
)
from apps.properties.models import (
    DocumentAccessLog,
    Property,
    PropertyDeletion,
    PropertyDocument,
    PropertyImage,
    PropertyStatusEvent,
)
from apps.properties.reference import (
    ALL_MUNICIPALITIES,
    ANGOLA_PROVINCES,
    MUNICIPALITIES_BY_PROVINCE,
    municipalities_for,
    provinces_without_boundary,
)
from apps.properties.selectors import PropertyFilters, PropertyQueryService
from apps.properties.admin import PropertyImageInlineFormSet
from apps.properties.services import add_images, build_map_payload
from apps.properties.validators import MAX_FOTOS, MIN_FOTOS


class PropertyPublicationTests(TestCase):
    """Só imóveis com documentação verificada e coordenadas confirmadas são publicados."""

    def setUp(self) -> None:
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)

    def _property(self, **kwargs: object) -> Property:
        return make_property(curator=self.curator, owner=self.owner, **kwargs)

    def test_public_queryset_hides_every_state_except_published(self) -> None:
        """Visitantes só veem imóveis publicados."""
        published = self._property()
        for status in (
            Property.Status.DRAFT,
            Property.Status.IN_REVIEW,
            Property.Status.UNDER_VALIDATION,
            Property.Status.CHANGES_REQUESTED,
            Property.Status.ARCHIVED,
        ):
            self._property(title=f"Imóvel {status}", status=status)

        visible = Property.objects.published().values_list("pk", flat=True)
        self.assertEqual(list(visible), [published.pk])

    def test_transition_records_actor_and_reason(self) -> None:
        """Cada transição deixa rasto em `PropertyStatusEvent` (§2.8)."""
        prop = self._property(status=Property.Status.DRAFT)
        prop.transition_to(
            Property.Status.IN_REVIEW,
            actor=self.curator,
            reason="Triagem concluída",
        )

        event = PropertyStatusEvent.objects.get(property=prop)
        self.assertEqual(event.from_status, Property.Status.DRAFT)
        self.assertEqual(event.to_status, Property.Status.IN_REVIEW)
        self.assertEqual(event.actor, self.curator)
        self.assertEqual(event.reason, "Triagem concluída")

    def test_publication_requires_verified_documents(self) -> None:
        """Sem escritura e BI verificados o imóvel não é publicado."""
        prop = self._property(status=Property.Status.UNDER_VALIDATION)
        self.assertTrue(prop.missing_verified_documents())

        with self.assertRaises(ValidationError):
            prop.transition_to(
                Property.Status.PUBLISHED,
                actor=self.curator,
                reason="Publicação a partir do painel interno",
            )

        make_verified_documents(prop)
        self.assertEqual(prop.missing_verified_documents(), [])

    def test_publication_requires_reason(self) -> None:
        """`PUBLISHED` e `ARCHIVED` exigem justificação (§2.8)."""
        prop = self._property(status=Property.Status.DRAFT)
        with self.assertRaises(ValidationError):
            prop.transition_to(Property.Status.PUBLISHED, actor=self.curator, reason="")

    def test_illegal_transition_is_refused(self) -> None:
        """O fluxo de estados não permite saltos."""
        prop = self._property(status=Property.Status.DRAFT)
        self.assertFalse(prop.transition_allowed(Property.Status.PUBLISHED))
        with self.assertRaises(ValidationError):
            prop.transition_to(Property.Status.PUBLISHED, actor=self.curator, reason="Directo")

    def test_land_requires_land_area(self) -> None:
        """Terrenos exigem área de terreno, não área construída."""
        with self.assertRaises(ValidationError) as ctx:
            self._property(
                type=Property.Type.LAND,
                purpose=Property.Purpose.SALE,
                land_area_m2=None,
            )
        self.assertIn("land_area_m2", ctx.exception.message_dict)

    def test_location_must_be_confirmed_by_satellite(self) -> None:
        """Publicar exige `location_verified_at` (§2.3)."""
        prop = self._property(
            status=Property.Status.UNDER_VALIDATION,
            location_verified_at=None,
        )
        make_verified_documents(prop)

        with self.assertRaises(ValidationError):
            prop.transition_to(
                Property.Status.PUBLISHED,
                actor=self.curator,
                reason="Localização ainda não confirmada",
            )


class PropertySubmissionTests(TestCase):
    """A triagem da etapa 2 tem de estar completa antes de avançar."""

    def setUp(self) -> None:
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
        )

    def test_pending_items_list_what_blocks_triage(self) -> None:
        """Sem dossiê preenchido a triagem não fecha."""
        submission = make_submission(self.prop, triaged_by=self.curator, ready=False)
        items = submission.pending_items()

        self.assertFalse(submission.is_ready_for_review())
        self.assertTrue(items)
        self.assertTrue(all(isinstance(item, str) for item in items))

    def test_ready_submission_reports_nothing_missing(self) -> None:
        """Com o dossié completo o imóvel pode avançar."""
        submission = make_submission(self.prop, triaged_by=self.curator, ready=True)
        for index in range(5):
            make_image(self.prop, caption=f"Foto {index}")

        self.assertEqual(submission.pending_items(), [])
        self.assertTrue(submission.is_ready_for_review())

    def test_ready_submission_requires_photo_count(self) -> None:
        """Menos de cinco fotografias bloqueiam a triagem (§2.1)."""
        submission = make_submission(self.prop, triaged_by=self.curator, ready=True)
        self.assertFalse(submission.is_ready_for_review())
        self.assertIn("Fotografias (mínimo de 5)", submission.pending_items())

        for index in range(5):
            make_image(self.prop, caption=f"Foto {index}")
        self.assertEqual(self.prop.images.count(), 5)
        self.assertTrue(submission.is_ready_for_review())


@override_settings(MANAGER_EMAILS=["gestor@echilo.ao"])
class PropertyAdminTests(TestCase):
    """§2.8: o admin não é uma porta lateral à máquina de estados."""

    def setUp(self) -> None:
        self.manager = make_user(email="gestor@echilo.ao")
        self.curator = make_user(role="CURATOR", email="curador2@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.IN_REVIEW,
        )
        self.client.force_login(self.manager)

    def test_manager_email_is_promoted_on_login(self) -> None:
        """§3: quem está em MANAGER_EMAILS ganha acesso ao painel interno."""
        self.manager.refresh_from_db()

        self.assertTrue(self.manager.is_staff)
        self.assertTrue(self.manager.is_superuser)
        self.assertEqual(self.manager.role, "ADMIN")

    def test_plain_client_never_becomes_staff(self) -> None:
        """Um cliente fora da lista não é promovido nem entra no admin."""
        client_user = make_user(email="cliente@echilo.ao")
        self.client.force_login(client_user)
        client_user.refresh_from_db()

        self.assertFalse(client_user.is_staff)
        response = self.client.get("/admin/")
        self.assertIn(response.status_code, (302, 403))

    def test_status_is_not_editable_in_the_change_form(self) -> None:
        """O estado é mostrado na ficha, mas não entra no formulário."""
        response = self.client.get(
            f"/admin/properties/property/{self.prop.pk}/change/"
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assertNotIn("status", form.fields)
        self.assertContains(response, "Em revis")

    def test_forced_status_post_does_not_change_the_state(self) -> None:
        """Mesmo com um POST forçado, o estado guardado não muda."""
        payload = {
            "title": self.prop.title,
            "description": self.prop.description,
            "type": self.prop.type,
            "purpose": self.prop.purpose,
            "owner": self.owner.pk,
            "curated_by": self.curator.pk,
            "price": "450000.00",
            "currency": "AOA",
            "lease_term_months": 12,
            "province_ref": self.prop.province_ref,
            "municipality": self.prop.municipality,
            "locality": self.prop.locality,
            "latitude": str(self.prop.latitude),
            "longitude": str(self.prop.longitude),
            "status": Property.Status.PUBLISHED,
        }

        response = self.client.post(
            f"/admin/properties/property/{self.prop.pk}/change/", payload
        )

        self.assertIn(response.status_code, (302, 200))
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.IN_REVIEW)

    def test_publish_action_records_a_status_event(self) -> None:
        """A acção de publicar passa por `transition_to()` e fica registada."""
        prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.UNDER_VALIDATION,
            title="Imóvel pronto para publicação",
        )
        for index in range(5):
            make_image(prop, caption=f"Foto {index}")
        make_verified_documents(prop)

        response = self.client.post(
            "/admin/properties/property/",
            {
                "action": "publish_selected",
                "_selected_action": [str(prop.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.PUBLISHED)
        self.assertIsNotNone(prop.published_at)
        self.assertTrue(
            PropertyStatusEvent.objects.filter(
                property=prop, to_status=Property.Status.PUBLISHED
            ).exists()
        )

    def test_publish_action_refuses_incomplete_property(self) -> None:
        """Sem documentação verificada, a publicação é recusada com aviso."""
        prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.UNDER_VALIDATION,
            title="Imóvel sem documentação",
        )

        self.client.post(
            "/admin/properties/property/",
            {"action": "publish_selected", "_selected_action": [str(prop.pk)]},
            follow=True,
        )

        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.UNDER_VALIDATION)

    def test_publish_action_refuses_unverified_location(self) -> None:
        """Sem pin confirmado por satélite não há publicação (§2.3)."""
        prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.UNDER_VALIDATION,
            title="Imóvel sem pin confirmado",
            location_verified_at=None,
        )
        for index in range(5):
            make_image(prop, caption=f"Foto {index}")
        make_verified_documents(prop)

        self.client.post(
            "/admin/properties/property/",
            {"action": "publish_selected", "_selected_action": [str(prop.pk)]},
            follow=True,
        )

        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.UNDER_VALIDATION)


class CuratorActionTests(TestCase):
    """Transições e carregamento de fotos accionados pela ficha interna."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.UNDER_VALIDATION,
        )
        self.detail_url = reverse(
            "properties:curator_detail", args=[self.prop.reference]
        )
        self.transition_url = reverse(
            "properties:curator_transition", args=[self.prop.reference]
        )
        self.images_url = reverse("properties:curator_images", args=[self.prop.reference])

    def test_client_cannot_transition(self) -> None:
        """Um CLIENT não mexe no estado de um imóvel."""
        client_user = make_user(email="cliente@echilo.ao")
        self.client.force_login(client_user)

        response = self.client.post(
            self.transition_url,
            {"to_status": Property.Status.PUBLISHED, "reason": "Teste"},
        )

        self.assertEqual(response.status_code, 403)
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.UNDER_VALIDATION)

    def test_publishing_requires_a_reason(self) -> None:
        """Publicar sem justificação é recusado (§2.8)."""
        for index in range(5):
            make_image(self.prop, caption=f"Foto {index}")
        make_verified_documents(self.prop)
        self.client.force_login(self.curator)

        self.client.post(
            self.transition_url, {"to_status": Property.Status.PUBLISHED, "reason": "  "}
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.UNDER_VALIDATION)

    def test_a_transicao_recusada_diz_o_que_e_que_falta(self) -> None:
        """«Não foi aplicada» sem mais não é resposta, é uma porta fechada.

        Publicar ou arquivar sem justificação é a recusa mais fácil de tropeçar: o
        campo da justificação não é obrigatório no HTML, o aviso vive num
        `placeholder` e o estado escolhido é muitas vezes o único oferecido. A
        vista escrevia a recusa genérica e deitava fora o erro do formulário, que
        era onde a resposta estava. Quem lia isso sabia que tinha havido um erro e
        não sabia o que fazer — e a justificação escrita perdia-se no
        redireccionamento, à mesma.
        """
        self.client.force_login(self.curator)

        response = self.client.post(
            self.transition_url, {"to_status": Property.Status.PUBLISHED, "reason": ""}
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.UNDER_VALIDATION)
        mensagens = self._mensagens(response)
        with self.subTest(mensagens=mensagens):
            self.assertTrue(
                any("justifica" in mensagem.lower() for mensagem in mensagens), mensagens
            )

    def test_a_justificacao_escreve_se_antes_de_o_browser_a_pedir(self) -> None:
        """O `required` no HTML tem de ser verdade para todos os estados oferecidos.

        A regra dos dois lados: o browser não pode recusar um pedido que o
        servidor aceitaria, e o servidor não pode recusar um pedido que o browser
        deixou passar. Publicar e arquivar exigem justificação e os restantes
        estados não, por isso o atributo só entra quando não há nenhum estado
        oferecido que dispensasse — que é o caso de um imóvel publicado, onde a
        única saída é o arquivo, e o erro que daí vinha não dizia o que faltava.
        """
        self.client.force_login(self.curator)
        for estado, exigido in (
            (Property.Status.PUBLISHED, True),
            (Property.Status.UNDER_VALIDATION, False),
        ):
            with self.subTest(estado=estado):
                imovel = make_property(
                    curator=self.curator,
                    owner=self.owner,
                    status=estado,
                    title="Casa para %s" % estado,
                )
                html = self.client.get(
                    reverse("properties:curator_detail", args=[imovel.reference])
                ).content.decode("utf-8")
                linha = next(
                    (candidate for candidate in html.splitlines() if 'name="reason"' in candidate),
                    "",
                )
                self.assertTrue(linha, "a ficha não desenha o campo da justificação")
                self.assertEqual(exigido, "required" in linha, linha)

    def test_valid_transition_is_recorded(self) -> None:
        """Uma transição válida muda o estado e deixa rasto no histórico."""
        for index in range(5):
            make_image(self.prop, caption=f"Foto {index}")
        make_verified_documents(self.prop)
        self.client.force_login(self.curator)

        self.client.post(
            self.transition_url,
            {
                "to_status": Property.Status.PUBLISHED,
                "reason": "Documentação verificada pela equipa",
            },
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.PUBLISHED)
        self.assertIsNotNone(self.prop.published_at)
        self.assertTrue(
            PropertyStatusEvent.objects.filter(
                property=self.prop, to_status=Property.Status.PUBLISHED
            ).exists()
        )

    def test_transition_rejects_a_state_outside_the_flow(self) -> None:
        """Um estado que não é alcançável a partir do actual é recusado."""
        self.client.force_login(self.curator)

        self.client.post(
            self.transition_url,
            {"to_status": Property.Status.DRAFT, "reason": "Voltar ao rascunho"},
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.UNDER_VALIDATION)
        self.assertFalse(
            PropertyStatusEvent.objects.filter(
                property=self.prop, to_status=Property.Status.DRAFT
            ).exists()
        )

    def test_transition_to_requested_changes_is_allowed(self) -> None:
        """Pedido de alterações é uma saída válida da validação."""
        self.client.force_login(self.curator)

        self.client.post(
            self.transition_url,
            {
                "to_status": Property.Status.CHANGES_REQUESTED,
                "reason": "Faltam cinco fotografias",
            },
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.CHANGES_REQUESTED)
        self.assertTrue(
            PropertyStatusEvent.objects.filter(
                property=self.prop, to_status=Property.Status.CHANGES_REQUESTED
            ).exists()
        )

    def test_get_on_transition_redirects_back(self) -> None:
        """A rota de transição não responde a GET."""
        self.client.force_login(self.curator)

        response = self.client.get(self.transition_url)

        self.assertRedirects(response, self.detail_url)

    def _carrega(self, *ficheiros: SimpleUploadedFile, follow: bool = False) -> HttpResponse:
        """Carrega um lote pela mesma rota que a ficha usa."""
        return self.client.post(
            self.images_url, {"images": list(ficheiros)}, follow=follow
        )

    def _mensagens(self, response: HttpResponse) -> list[str]:
        """Lê o que a ficha disse à equipa.

        Vem de `response.wsgi_request` e não de `self.client`: as mensagens são
        postas no pedido pelo `MessageMiddleware`, e o cliente de testes não é um
        pedido. Nenhum teste do projecto lia mensagens antes destes, e é
        por isso que um formulário que respondia "campo obrigatório" para
        sempre passou durante meses: o que a pessoa lia não era testado.
        """
        return [str(mensagem) for mensagem in get_messages(response.wsgi_request)]

    def _jpg(self, nome: str) -> SimpleUploadedFile:
        return SimpleUploadedFile(nome, jpeg_bytes(), content_type="image/jpeg")

    def test_image_upload_becomes_the_cover(self) -> None:
        """A primeira fotografia carregada é a capa (§2.1)."""
        self.client.force_login(self.curator)

        response = self._carrega(self._jpg("capa.jpg"))

        self.assertEqual(response.status_code, 302)
        image = self.prop.images.get()
        self.assertEqual(image.sort_order, 0)

    def test_o_lote_entra_na_ordem_escolhida(self) -> None:
        """Várias de uma vez, e pela ordem que a pessoa viu no ecrã.

        A ordem de escolha é a ordem de fotografar, e é a que decide a capa. O
        nome guardado não é o enviado: o storage acrescenta um sufixo para
        duas fotografias com o mesmo nome não se sobrescrevessem, e comparar
        o nome inteiro seria testar o storage em vez da ordem.
        """
        self.client.force_login(self.curator)

        self._carrega(self._jpg("a.jpg"), self._jpg("b.jpg"), self._jpg("c.jpg"))

        guardados = [
            Path(image.image.name).stem
            for image in self.prop.images.order_by("sort_order")
        ]
        for esperado, guardado in zip(["a", "b", "c"], guardados):
            self.assertTrue(
                guardado.startswith(esperado),
                f"esperava {esperado}* em {guardados}, veio {guardado}",
            )

    def test_o_lote_continua_depois_das_que_ja_existiam(self) -> None:
        """As fotografias do lote entram a seguir às que lá estavam, sem cobrir nada."""
        self.client.force_login(self.curator)
        make_image(self.prop, caption="Ja existente")

        self._carrega(self._jpg("nova.jpg"))

        self.assertEqual(
            sorted(image.sort_order for image in self.prop.images.all()), [0, 1]
        )

    def test_second_upload_keeps_the_cover(self) -> None:
        """Uma segunda fotografia não rouba a capa."""
        self.client.force_login(self.curator)
        self._carrega(self._jpg("capa.jpg"))
        self._carrega(self._jpg("segunda.jpg"))

        images = list(self.prop.images.order_by("sort_order"))
        self.assertEqual([image.sort_order for image in images], [0, 1])

    def test_upload_refuses_a_non_image(self) -> None:
        """Um ficheiro que não é imagem não entra na galeria."""
        self.client.force_login(self.curator)

        self._carrega(
            SimpleUploadedFile("nota.pdf", b"%PDF-1.4", content_type="application/pdf")
        )

        self.assertEqual(self.prop.images.count(), 0)

    def test_um_lote_com_uma_ficheiro_mau_guarda_o_resto(self) -> None:
        """Um PDF no meio de doze fotografias não leva as onze boas com ele.

        A equipa fotografa o imóvel inteiro e escolhe tudo de uma vez. Se um
        ficheiro estiver mal, o que ela espera é gravar os outros e saber qual
        foi recusado — não um "guardadas" que não é verdade.
        """
        self.client.force_login(self.curator)

        self._carrega(
            self._jpg("boa-1.jpg"),
            SimpleUploadedFile("nota.pdf", b"%PDF-1.4", content_type="application/pdf"),
            self._jpg("boa-2.jpg"),
        )

        self.assertEqual(self.prop.images.count(), 2)

    def test_o_lote_diz_qual_ficheiro_foi_recusado(self) -> None:
        """A recusa diz o nome do ficheiro, não só que algo correu mal."""
        self.client.force_login(self.curator)

        response = self._carrega(
            self._jpg("boa.jpg"),
            SimpleUploadedFile("nota.pdf", b"%PDF-1.4", content_type="application/pdf"),
            follow=True,
        )

        recusas = self._mensagens(response)
        self.assertTrue(any("nota.pdf" in recusa for recusa in recusas), recusas)

    def test_upload_is_capped_at_fifteen_photos(self) -> None:
        """Acima de quinze fotografias o imóvel não aceita mais nenhuma (§2.1)."""
        self.client.force_login(self.curator)
        for index in range(MAX_FOTOS):
            make_image(self.prop, caption=f"Foto {index}")

        self._carrega(self._jpg("extra.jpg"))

        self.assertEqual(self.prop.images.count(), MAX_FOTOS)

    def test_o_imovel_ninguem_enche_a_mesma_hora(self) -> None:
        """Dois carregamentos ao mesmo tempo não dão dezasseis fotografias.

        A vista lê a contagem, monta o formulário e grava; entre a leitura e a
        gravação cabe outro pedido. Se o serviço não trancar o imóvel, os dois
        veem catorze livres e escrevem um cada um.
        """
        for index in range(MAX_FOTOS - 1):
            make_image(self.prop, caption=f"Foto {index}")

        primeira, fora_1 = add_images(prop=self.prop, images=[self._jpg("a.jpg")])
        segunda, fora_2 = add_images(prop=self.prop, images=[self._jpg("b.jpg")])

        self.assertEqual(len(primeira), 1)
        self.assertEqual(fora_1, 0)
        self.assertEqual(len(segunda), 0, "o segundo lote entrou por cima do tecto")
        self.assertEqual(fora_2, 1, "o segundo lote não disse que não coube")
        self.assertEqual(self.prop.images.count(), MAX_FOTOS)

    def test_o_servico_diz_o_que_entrou_e_o_que_ficou_de_fora(self) -> None:
        """Quem chama o serviço precisa dos dois números, não só do que entrou."""
        for index in range(MAX_FOTOS - 2):
            make_image(self.prop, caption=f"Foto {index}")

        guardadas, descartadas = add_images(
            prop=self.prop, images=[self._jpg(f"nova-{i}.jpg") for i in range(5)]
        )

        self.assertEqual(len(guardadas), 2)
        self.assertEqual(descartadas, 3)
        self.assertEqual(
            sorted(foto.sort_order for foto in self.prop.images.all()),
            list(range(MAX_FOTOS)),
            "a numeração ficou com um buraco ou uma repetição",
        )

    def test_o_painel_nao_deixa_passar_de_cinco(self) -> None:
        """O painel tem o seu caminho de gravação e também tem de contar.

        `add_images()` tranca o imóvel, mas o inline do admin grava directamente.
        Um tecto que a equipa contorna por dentro não é um tecto.
        """
        for index in range(MAX_FOTOS):
            make_image(self.prop, caption=f"Foto {index}")

        formset = self._formset_de_fotografias()

        self.assertFalse(formset.is_valid())
        self.assertIn("não pode ter mais de", str(formset.non_form_errors()))

    def test_o_painel_aceita_a_fotografia_faltando_uma(self) -> None:
        """A defesa que recusa tudo não é uma defesa, é uma parede.

        O mesmo inline com catorze fotografias tem de aceitar a Décima quinta.
        """
        for index in range(MAX_FOTOS - 1):
            make_image(self.prop, caption=f"Foto {index}")

        self.assertTrue(self._formset_de_fotografias().is_valid())

    def test_o_painel_conta_a_apagada_como_casa_ocupada(self) -> None:
        """Apagar uma e pôr outra no mesmo pedido não pode dar dezasseis.

        O `DELETE` tem de sair da conta: sem isso, um imóvel cheio nunca aceita
        trocar uma fotografia, e a equipa acaba a apagar num pedido e a carregar
        noutro só para conseguir corrigir uma imagem.
        """
        for index in range(MAX_FOTOS):
            make_image(self.prop, caption=f"Foto {index}")

        formset = self._formset_de_fotografias(apagar_a_ultima=True)

        self.assertTrue(formset.is_valid())

    @override_settings(MANAGER_EMAILS=["gestor@echilo.ao"])
    def test_a_ficha_do_painel_usa_a_defesa_do_tecto(self) -> None:
        """Uma defesa que ninguém liga não defendem nada.

        O formset acima é testado isolado, e um formset isolado que ninguém
        atribui a nada é uma classe deitada fora. Este é o teste que diz que a
        ficha do painel passa mesmo por ela.
        """
        gestor = make_user(email="gestor@echilo.ao")
        self.client.force_login(gestor)

        response = self.client.get(
            f"/admin/properties/property/{self.prop.pk}/change/"
        )

        self.assertEqual(response.status_code, 200)
        formsets = response.context["inline_admin_formsets"]
        # O painel não usa a classe tal e qual: embrulha-a numa subclasse gerada
        # que sabe construir os formulários. O que interessa é que a defence
        # esteja na linhagem.
        tipos = [formset.formset.__class__ for formset in formsets]
        self.assertTrue(
            any(issubclass(tipo, PropertyImageInlineFormSet) for tipo in tipos),
            tipos,
        )

    def _formset_de_fotografias(self, *, apagar_a_ultima: bool = False) -> BaseFormSet:
        """Monta o mesmo formset que o inline do admin usa, com uma foto nova.

        O payload é o que o painel mandaria de facto: as fotografias que já lá
        estão como formulários iniciais, mais a nova que a equipa está a juntar.
        """
        formset_class = admin_formset_factory(
            Property,
            PropertyImage,
            formset=PropertyImageInlineFormSet,
            extra=1,
            fields=("image", "caption", "sort_order"),
        )
        prefixo = formset_class.get_default_prefix()
        existentes = list(self.prop.images.order_by("sort_order"))
        data = {
            f"{prefixo}-TOTAL_FORMS": str(len(existentes) + 1),
            f"{prefixo}-INITIAL_FORMS": str(len(existentes)),
            f"{prefixo}-MIN_NUM_FORMS": "0",
            f"{prefixo}-MAX_NUM_FORMS": "1000",
        }
        for indice, foto in enumerate(existentes):
            data[f"{prefixo}-{indice}-id"] = str(foto.pk)
            data[f"{prefixo}-{indice}-caption"] = foto.caption or ""
            data[f"{prefixo}-{indice}-sort_order"] = str(foto.sort_order)
        if apagar_a_ultima:
            data[f"{prefixo}-{len(existentes) - 1}-DELETE"] = "on"
        data[f"{prefixo}-{len(existentes)}-caption"] = "A nova"
        data[f"{prefixo}-{len(existentes)}-sort_order"] = str(MAX_FOTOS)
        return formset_class(
            instance=self.prop,
            data=data,
            files={f"{prefixo}-{len(existentes)}-image": self._jpg("nova.jpg")},
        )

    def test_o_excesso_do_lote_e_dito_e_nao_ignorado(self) -> None:
        """Escolher vinte com cinco lá dentro guarda cinco e diz que não couberam todos.

        Recusar o lote inteiro obriga a pessoa a contar o que já tinha; guardar
        em silêncio faz o mesmo estrago com menos trabalho da parte dela.
        """
        self.client.force_login(self.curator)
        for index in range(12):
            make_image(self.prop, caption=f"Foto {index}")

        response = self._carrega(
            *(self._jpg(f"nova-{i}.jpg") for i in range(8)), follow=True
        )

        self.assertEqual(self.prop.images.count(), MAX_FOTOS)
        avisos = self._mensagens(response)
        self.assertTrue(
            any("5" in aviso and "limite" in aviso for aviso in avisos), avisos
        )

    def test_a_ficha_diz_que_ja_nao_cabe_mais(self) -> None:
        """Com o tecto cheio, a ficha esconde o formulário e diz porquê.

        Um formulário que recusa sem explicar obriga a pessoa a carregar quinze
        fotografias para as ver recusadas.
        """
        self.client.force_login(self.curator)
        for index in range(MAX_FOTOS):
            make_image(self.prop, caption=f"Foto {index}")

        html = self.client.get(self.detail_url).content.decode()

        self.assertNotIn('name="images"', html)
        self.assertIn(f"as {MAX_FOTOS} fotografias", html)

    def test_a_ficha_anuncia_o_espaco_que_resta(self) -> None:
        """A ficha diz quantas cabem antes de a pessoa escolher os ficheiros."""
        self.client.force_login(self.curator)
        for index in range(10):
            make_image(self.prop, caption=f"Foto {index}")

        html = self.client.get(self.detail_url).content.decode()

        self.assertIn(f"até {MAX_FOTOS - 10} de uma vez", html)

    def test_o_input_da_ficha_tem_o_nome_que_o_formulario_espera(self) -> None:
        """O `name` do input é o mesmo que a view lê, senão nada entra.

        Um `name` e um campo de formulário que não coincidem não dão erro: o
        pedido chega, o campo é obrigatório, e a ficha devolve "campo
        obrigatório" para sempre. É o que acontecia com `foto` contra `image`.
        """
        self.client.force_login(self.curator)

        nomes = self._inputs_de_fotografias()

        self.assertIn("images", nomes)
        self.assertNotIn("foto", nomes)
        self.assertEqual(nomes.count("images"), 1)

    def _inputs_de_fotografias(self) -> list[str]:
        """Os `name` dos inputs de ficheiro que a ficha desenha."""
        html = self.client.get(self.detail_url).content.decode()
        return re.findall(
            r'<input[^>]*type="file"[^>]*name="([^"]+)"', html
        )

    def test_o_input_da_ficha_aceita_varias(self) -> None:
        """O input é `multiple`, senão o browser deixa escolher uma só."""
        self.client.force_login(self.curator)

        html = self.client.get(self.detail_url).content.decode()

        self.assertRegex(html, r'<input[^>]*type="file"[^>]*multiple')

    def test_o_input_da_ficha_diz_os_mesmos_formatos_que_o_formulario_aceita(self) -> None:
        """O `accept` do input e o do formulário dizem a mesma coisa.

        Escritos à mão, o input passa a oferecer um formato que o servidor
        recusa, e a pessoa só descobre depois de escolher o ficheiro.
        """
        self.client.force_login(self.curator)

        html = self.client.get(self.detail_url).content.decode()
        no_input = re.search(r'<input[^>]*type="file"[^>]*accept="([^"]+)"', html)

        self.assertIsNotNone(no_input)
        form = PropertyImageUploadForm()
        self.assertEqual(no_input.group(1), form.fields["images"].widget.attrs["accept"])

    def test_a_ficha_oferece_a_apagar_cada_fotografia(self) -> None:
        """Sem botão de apagar, um tecto de quinze é um muro sem volta."""
        self.client.force_login(self.curator)
        fotografia = make_image(self.prop, caption="Errada")

        html = self.client.get(self.detail_url).content.decode()

        self.assertIn(
            reverse(
                "properties:curator_image_delete",
                args=[self.prop.reference, fotografia.id],
            ),
            html,
        )
        self.assertIn("data-confirmar=", html)

    def test_a_confirmacao_diz_se_a_capa_muda_ou_nao(self) -> None:
        """Um texto único para as duas não é um texto, é uma adivinhação.

        Apagar a capa promove a seguinte e apagar uma foto do meio não toca na
        capa. Dizer "a capa passa a ser a seguinte" nas duas é uma das duas
        coisas errada, e a pessoa não tem como saber qual.
        """
        self.client.force_login(self.curator)
        make_image(self.prop, caption="Capa")
        make_image(self.prop, caption="Segunda")

        html = self.client.get(self.detail_url).content.decode()
        confirmar = re.findall(r'data-confirmar="([^"]+)"', html)

        self.assertEqual(len(confirmar), 2, confirmar)
        self.assertIn("passa a ser a capa", confirmar[0])
        self.assertIn("não muda", confirmar[1])

    def test_apagar_a_fotografia_tira_a_linha_da_galeria(self) -> None:
        self.client.force_login(self.curator)
        fotografia = make_image(self.prop, caption="Errada")

        response = self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, fotografia.id])
        )

        self.assertRedirects(
            response, f"{self.detail_url}#fotografias", fetch_redirect_response=False
        )
        self.assertFalse(PropertyImage.objects.filter(pk=fotografia.pk).exists())

    def test_apagar_a_capa_passa_a_capa_a_seguinte(self) -> None:
        """Apagar a primeira tem de promover a segunda, senão a ficha fica sem capa.

        A capa é a fotografia de `sort_order` zero. Sem a renomear, o catálogo
        passava a mostrar a segunda fotografia como se fosse a primeira.
        """
        self.client.force_login(self.curator)
        capa = make_image(self.prop, caption="Capa")
        segunda = make_image(self.prop, caption="Segunda")

        self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, capa.id])
        )

        segunda.refresh_from_db()
        self.assertEqual(segunda.sort_order, 0)

    def test_apagar_uma_fotografia_do_meio_fecha_a_numeracao(self) -> None:
        """O buraco só aparece quando se apaga do meio, e aí é que dói.

        Com a capa, o `sort_order` 0 some e a renumeração é óbvia. Apagando a
        segunda de três, o que fica é 0 e 2: a próxima fotografia carregada
        entra com o 2 que já está ocupado, e a capa passa a ser a imagem
        errada. Só um teste que apaga do meio é que apanha isto.
        """
        self.client.force_login(self.curator)
        make_image(self.prop, caption="Primeira")
        do_meio = make_image(self.prop, caption="Do meio")
        make_image(self.prop, caption="Ultima")

        self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, do_meio.id])
        )

        ordens = list(
            PropertyImage.objects.filter(property=self.prop)
            .order_by("sort_order")
            .values_list("sort_order", flat=True)
        )
        self.assertEqual(ordens, [0, 1])

    def test_a_fotografia_seguinte_da_apagada_nao_pisa_a_numeracao(self) -> None:
        """O ciclo completo: apagar ao meio e carregar outra vez tem de ficar sequencial."""
        self.client.force_login(self.curator)
        make_image(self.prop, caption="Primeira")
        do_meio = make_image(self.prop, caption="Do meio")
        make_image(self.prop, caption="Ultima")

        self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, do_meio.id])
        )
        self.client.post(
            reverse("properties:curator_images", args=[self.prop.reference]),
            {"images": [self._jpg("nova.jpg")]},
        )

        ordens = list(
            PropertyImage.objects.filter(property=self.prop)
            .order_by("sort_order")
            .values_list("sort_order", flat=True)
        )
        self.assertEqual(ordens, [0, 1, 2])

    def test_apagar_a_capa_diz_que_a_capa_mudou(self) -> None:
        """A ficha avisa que a capa deixou de ser a que estava."""
        self.client.force_login(self.curator)
        capa = make_image(self.prop, caption="Capa")
        make_image(self.prop, caption="Segunda")

        response = self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, capa.id]),
            follow=True,
        )

        mensagens = self._mensagens(response)
        self.assertTrue(
            any("capa" in mensagem.lower() for mensagem in mensagens), mensagens
        )

    def test_apagar_a_fotografia_apaga_o_ficheiro_com_ela(self) -> None:
        """O ficheiro vai para o lixo com a linha.

        `Model.delete()` não toca em armazenamento, e uma fotografia órfã na
        Cloudinary continua lá a ser paga todos os meses por alguém que já a
        apagou.
        """
        self.client.force_login(self.curator)
        fotografia = make_image(self.prop, caption="A apagar")
        caminho = fotografia.image.path

        self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, fotografia.id])
        )

        self.assertFalse(os.path.exists(caminho), f"{caminho} ficou no storage")

    def test_nao_se_apaga_a_fotografia_de_outro_imovel(self) -> None:
        """A fotografia tem de ser deste imóvel, ou o `id` apaga o que calha.

        A rota traz a referência do imóvel e o id da fotografia. Ignorar a
        referência dava a quem tenha um id válido a capacidade de apagar a
        fotografia de um imóvel que não está a ver.
        """
        self.client.force_login(self.curator)
        outro = make_property(
            curator=self.curator, owner=self.owner, status=Property.Status.DRAFT
        )
        alheia = make_image(outro, caption="De outro imovel")

        response = self.client.post(
            reverse(
                "properties:curator_image_delete", args=[self.prop.reference, alheia.id]
            )
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(PropertyImage.objects.filter(pk=alheia.pk).exists())

    def test_apagar_fotografia_exige_equipa(self) -> None:
        """Um cliente autenticado não apaga fotografias."""
        cliente = make_user(role="CLIENT", email="cliente@echilo.ao")
        fotografia = make_image(self.prop, caption="Intocavel")
        self.client.force_login(cliente)

        response = self.client.post(
            reverse("properties:curator_image_delete", args=[self.prop.reference, fotografia.id])
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(PropertyImage.objects.filter(pk=fotografia.pk).exists())


class CuratorDetailEditTests(TestCase):
    """A ficha interna altera os campos que mostra, e não os que esconde."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            has_garden=True,
        )
        self.url = reverse("properties:curator_detail", args=[self.prop.reference])

    def _post(self, **extra: object) -> None:
        """Submete a ficha com os mesmos campos que o template desenha."""
        self.client.force_login(self.curator)
        dados: dict[str, object] = {
            "title": "Preço revisto",
            "type": self.prop.type,
            "purpose": self.prop.purpose,
            "price": "500000",
            "lease_term_months": "12",
            "province_ref": self.prop.province_ref,
            "municipality": self.prop.municipality,
            "locality": self.prop.locality,
            "latitude": "-8.918430",
            "longitude": "13.184700",
            "location_accuracy_m": "25",
            "map_reference": "-8.918430,13.184700",
            "bedrooms": "3",
            "bathrooms": "2",
        }
        dados.update(extra)
        self.client.post(self.url, dados)

    def test_o_preco_alterado_fica_no_imovel(self) -> None:
        """O objectivo do formulário: a alteração pretendida acontece."""
        self._post()

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.title, "Preço revisto")
        self.assertEqual(self.prop.price, Decimal("500000.00"))

    def test_o_formulario_nao_tem_os_campos_que_a_pagina_nao_edita(self) -> None:
        """A garantia estrutural: o que a página não mostra, o formulário não tem.

        A ficha reutilizava o formulário de captação inteiro, e daí vinham duas
        coisas erradas de uma vez. Exigia nome, telefone e documento do
        proprietário numa página onde o proprietário não é o que se edita; e
        como o template não os desenhava, o erro aparecia em campo nenhum e a
        gravação nunca acontecia — a ficha devolvia o formulário sem dizer nada
        e sem mudar nada.

        Não é um teste de sintoma, é de causa: um campo ausente do formulário não
        pode ser gravado, aconteça o que acontecer ao `form_valid`.
        """
        campos = set(PropertyQuickEditForm.base_fields)

        for ausente in ("description", "has_garden", "has_pool", "owner_name", "address_hint"):
            with self.subTest(campo=ausente):
                self.assertNotIn(ausente, campos)

    def test_a_pagina_desenha_todos_os_campos_do_formulario(self) -> None:
        """Nenhum campo do formulário pode ficar sem o input que o preenche.

        O inverso do teste anterior, e o que fecha a porta pela outra banda. Se o
        formulário tem um campo que a página não desenha, esse campo volta vazio
        do `POST` e é gravado como vazio: foi assim que a ficha quase apagava a
        descrição e o pin verificado de um imóvel por causa de uma alteração de
        preço.

        Comparar os campos do formulário com os `name` que a página envia apanha
        a divergência na origem, em vez de a provar mais tarde por um valor que
        já não está.
        """
        self.client.force_login(self.curator)

        html = self.client.get(self.url).content.decode("utf-8")
        enviados = set(input_names_inside(html, "ficha-edicao"))

        for campo in PropertyQuickEditForm.base_fields:
            with self.subTest(campo=campo):
                self.assertIn(
                    campo,
                    enviados,
                    f"O campo {campo} está no formulário e a ficha não o desenha.",
                )

    def test_alterar_o_preco_nao_apaga_a_descricao(self) -> None:
        """A descrição sobrevive a uma edição de preço, porque não é campo desta página."""
        self._post()

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.description, "T3 arejado, com quintal e parque fechado.")
        self.assertEqual(self.prop.area_m2, 120)
        self.assertTrue(self.prop.has_water_tank)
        self.assertTrue(self.prop.has_garden)

    def test_alterar_o_preco_nao_apaga_a_localizacao_verificada(self) -> None:
        """O pin confirmado pela equipa sobrevive a uma edição de outro campo (§2.3)."""
        self._post()

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.latitude, Decimal("-8.918430"))
        self.assertEqual(self.prop.location_accuracy_m, 25)
        self.assertEqual(self.prop.map_reference, "-8.918430,13.184700")
        self.assertIsNotNone(self.prop.location_verified_at)

    def test_o_pin_pode_ser_corrigido_na_ficha(self) -> None:
        """A ficha aceita um pin novo, e a confirmação passa a datar de hoje."""
        antes = self.prop.location_verified_at

        self._post(
            latitude="-8.839000",
            longitude="13.289400",
            location_accuracy_m="18",
            map_reference="-8.839000,13.289400",
        )

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.latitude, Decimal("-8.839000"))
        self.assertEqual(self.prop.longitude, Decimal("13.289400"))
        self.assertEqual(self.prop.location_accuracy_m, 18)
        self.assertEqual(self.prop.map_reference, "-8.839000,13.289400")
        self.assertEqual(self.prop.description, "T3 arejado, com quintal e parque fechado.")
        self.assertGreater(self.prop.location_verified_at, antes)

    def test_arrendar_na_ficha_tambem_exige_prazo(self) -> None:
        """A regra do §2.6 vale na edição, e não só na captação."""
        self.client.force_login(self.curator)

        resposta = self.client.post(
            self.url,
            {
                "title": self.prop.title,
                "type": self.prop.type,
                "purpose": Property.Purpose.RENT,
                "price": "450000",
                "lease_term_months": "",
                "province_ref": self.prop.province_ref,
                "municipality": self.prop.municipality,
                "locality": self.prop.locality,
                "latitude": "-8.918430",
                "longitude": "13.184700",
                "bedrooms": "3",
                "bathrooms": "2",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Indique o prazo do contrato em meses.")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.purpose, Property.Purpose.RENT)

    def test_a_localidade_continua_vivel_quando_o_mapa_desaparece(self) -> None:
        """Sem JavaScript, o pin escreve-se à mão e o resto da ficha não se perde.

        O mapa escreve o pin, mas os campos continuam no HTML e continuam a ser
        submeter o formulário numa página sem Leaflet tem de continuar a
        funcionar, com os valores que já lá estavam.
        """
        self.client.force_login(self.curator)

        html = self.client.get(self.url).content.decode("utf-8")

        self.assertIn('name="latitude"', html)
        self.assertIn('name="longitude"', html)
        self.assertIn("-8.918430", html)


class SeletorDePinTests(TestCase):
    """O seletor de pin diz o que faz, e os campos sobrevivem sem ele (§2.3).

    O mapa é melhoria progressiva, e isso não se prova com o mapa a funcionar.
    Prova-se com a página aberta sem ele: os campos de coordenadas continuam ali,
    e o estado do pin é uma frase que o servidor escreveu.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            has_garden=True,
        )
        self.criar = reverse("properties:curator_create")
        self.editar = reverse("properties:curator_detail", args=[self.prop.reference])

    def _html(self, url: str) -> str:
        self.client.force_login(self.curator)
        return self.client.get(url).content.decode("utf-8")

    def test_as_duas_fichas_entregam_a_configuracao_ao_javascript(self) -> None:
        """O pin só existe se o JavaScript souber que tiles há e onde olhar."""
        self.client.force_login(self.curator)
        for url in (self.criar, self.editar):
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 200)
                config = resposta.context["map_config"]
                for placeholder in ("{z}", "{x}", "{y}"):
                    self.assertIn(placeholder, config["tileUrl"])
                self.assertTrue(str(config["attribution"]).strip())
                # O `id` é o contrato com o `echilo.js`; mudá-lo não dá erro,
                # dá um mapa que nunca lê a configuração.
                self.assertContains(resposta, 'id="pin-map-config"')

    def test_a_accao_principal_tem_nome(self) -> None:
        """O botão é uma frase, e não o ícone de 30×30 do plugin.

        No catálogo, um quadrado vazio dentro do mapa foi lido como mapa partido.
        O rótulo é o que separa "isto não faz nada" de "isto faz o que diz".
        """
        html = self._html(self.editar)
        self.assertIn("Usar a minha posição", html)
        self.assertIn('type="button"', html)
        self.assertIn('id="pin-localizar"', html)

    def test_as_caixas_de_mensagem_tem_papeis_diferentes(self) -> None:
        """Tiles são `alert` e geolocalização é `status`, e não ao contrário.

        Um 403 do fornecedor a apagar a recusa da permissão mandava a equipa
        seguir a pista errada. E dois alertas ao mesmo tempo fazem o leitor de
        ecrã ler a mensagem errada.
        """
        html = self._html(self.editar)
        self.assertIn('id="pin-erro" role="alert"', html)
        self.assertIn('id="pin-aviso" role="status"', html)

    def test_o_estado_do_pin_e_escrito_pelo_servidor(self) -> None:
        """Quem abre a ficha sem JavaScript vê o pin actual, ou a falta dele.

        Um mapa vazio sem explicação obriga a equipa a adivinhar se falta o mapa
        ou falta o pin, e o único sintoma dos dois é o mesmo.
        """
        self.prop.latitude = None
        self.prop.longitude = None
        self.prop.location_accuracy_m = None
        self.prop.save(update_fields=["latitude", "longitude", "location_accuracy_m"])
        self.assertIn("Ainda não há pin", self._html(self.editar))

        self.prop.latitude = -8.918430
        self.prop.longitude = 13.234400
        self.prop.location_accuracy_m = 12
        self.prop.save(update_fields=["latitude", "longitude", "location_accuracy_m"])
        html = self._html(self.editar)
        self.assertIn("Pin actual:", html)
        # O mesmo `coordinate` dos cartões: vírgula decimal e sem zeros à
        # direita. O valor com ponto pertence aos campos, e escrevê-lo aqui
        # trocaria leitura por máquina.
        self.assertIn(f"Pin actual: {coordinate(self.prop.latitude)}, "
                      f"{coordinate(self.prop.longitude)}.", html)
        self.assertNotIn("-8.918430, 13.234400.", html)

    def test_as_coordenadas_escrevem_se_a_mao(self) -> None:
        """O ponto decimal e o separador local não podem trancar o campo.

        `pt-ao` escreve `-8,918430` e o `Decimal` quer `-8.918430`; o formulário
        é que traduz. Se o campo saísse `readonly`, a tradução passava a ser a
        única forma de lá chegar.
        """
        html = self._html(self.editar)
        for campo in ("latitude", "longitude", "location_accuracy_m", "map_reference"):
            with self.subTest(campo=campo):
                self.assertIn(f'name="{campo}"', html)
        for atributo in ("readonly", "disabled"):
            with self.subTest(atributo=atributo):
                self.assertNotIn(f'input name="latitude" {atributo}', html)

    def test_a_atribuicao_das_fronteiras_vai_no_mapa(self) -> None:
        """A CC BY 4.0 exige que quem usa os dados diga de onde são."""
        html = self._html(self.editar)
        self.assertIn("geoBoundaries (CC BY 4.0)", html)

    def test_o_cliente_nao_alcanca_as_fichas(self) -> None:
        """O seletor de pin não é uma porta lateral para a curadoria."""
        cliente = make_user(role="CLIENT", email="cliente@echilo.ao")
        self.client.force_login(cliente)
        for url in (self.criar, self.editar):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_divisao_que_o_pin_escreve_tem_de_onde_se_ler(self) -> None:
        """A resposta do mapa é escrita nesta linha, e ela é a mesma sem JavaScript.

        Quem preenche a divisão a partir do pin precisa de uma frase que diga o
        que os campos ficaram a dizer. Um `#pin-divisao` que só existe depois do
        mapa arrancar é uma resposta que ninguém pode ler quando o mapa não
        arranca, e a ficha fica sem explicar o que o pin preencheu.
        """
        html = self._html(self.criar)

        self.assertIn('id="pin-divisao"', html)
        self.assertIn("A província e o município ficam por preencher", html)

    def test_a_ficha_editada_diz_a_divisao_que_ja_tem(self) -> None:
        """Um imóvel guardado mostra a divisão com o nome, e não com a sigla.

        O valor do campo é o código — `LUANDA` — porque é o que o `select` e a
        base de dados usam. Escrever a sigla na frase que a equipa lê seria
        obrigar a traduzir de memória o que a página tem à frente.
        """
        self.prop.province_ref = "LUANDA"
        self.prop.municipality = "Talatona"
        self.prop.save(update_fields=["province_ref", "municipality"])
        html = self._html(self.editar)

        self.assertIn("Divisão actual: Luanda, município de Talatona.", html)
        self.assertNotIn("Divisão actual: LUANDA", html)

    def test_o_mapa_recebe_a_correspondencia_entre_contorno_e_codigo(self) -> None:
        """O JavaScript escreve um código, e o código está na tabela do projecto.

        O contorno chama-se "Cuando Cubango" e o projecto tem `CUANDO` e
        `CUBANGO` para essa divisão. Sem a correspondência no `config`, o pin
        encontraria um nome que nenhum `option` tem, e `select.value = nome`
        deixaria a província vazia sem erro nenhum.
        """
        self.client.force_login(self.curator)
        resposta = self.client.get(self.criar)
        correspondencia = resposta.context["map_config"]["provinciasPorFronteira"]
        self.assertEqual(correspondencia["Luanda"], ["LUANDA"])
        self.assertEqual(correspondencia["Cuando Cubango"], ["CUANDO", "CUBANGO"])

    def test_toda_a_fronteira_desenhada_tem_provincia_que_o_campo_aceita(self) -> None:
        """Uma divisão que o mapa desenha e o formulário não sabe é um beco sem saída.

        As dezoito divisões da fonte e as vinte e uma da lista não são o mesmo
        número, e a diferença tem de ser `Icolo e Bengo` e `Moxico Leste` — as
        duas que a fonte não desenha. Uma terceira, ou uma quarta, seria uma
        pergunta que o mapa responde e a ficha não consegue escrever.
        """
        self.client.force_login(self.curator)
        resposta = self.client.get(self.criar)
        correspondencia = resposta.context["map_config"]["provinciasPorFronteira"]
        fonte = json.loads(
            (
                Path(settings.BASE_DIR)
                / "static"
                / "vendor"
                / "geo"
                / "angola-provincias.json"
            ).read_text(encoding="utf-8")
        )

        nomes = {f["properties"]["nome"] for f in fonte["features"]}
        self.assertEqual(nomes - set(correspondencia), set())
        # E o inverso: cada código da correspondência é uma opção do campo.
        opcoes = {
            opcao for opcao, _ in ANGOLA_PROVINCES
        }
        for codigos in correspondencia.values():
            for codigo in codigos:
                with self.subTest(codigo=codigo):
                    self.assertIn(codigo, opcoes)


class CuratorCreateWithPhotosTests(TestCase):
    """O registo de imóvel com o lote de fotografias no mesmo pedido (§2.1).

    A equipa fotografa o imóvel inteiro antes de se sentar a carregar, e o
    cadastro é onde o imóvel nasce. Exigir as fotografias aqui obrigaria a
    fotografar antes de o imóvel estar registado, que é o passo que a equipa dá
    depois de o registar. Por isso o campo existe, é opcional, e o que ele faz é
    o mesmo que a ficha faz.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.url = reverse("properties:curator_create")
        self.client.force_login(self.curator)

    def _dados(self, **extra: object) -> dict[str, object]:
        """Os campos que o formulário desenha, e nada mais."""
        dados: dict[str, object] = {
            "title": "T3 no Kilamba com quintal",
            "description": "Sala ampla, dois quartos e quintal.",
            "type": Property.Type.APARTMENT,
            "purpose": Property.Purpose.RENT,
            "price": "450000.00",
            "lease_term_months": 12,
            "province_ref": "LUANDA",
            "municipality": "Talatona",
            "locality": "Kilamba",
            "latitude": "-8.918430",
            "longitude": "13.184700",
            "owner_name": "Joaquim Ferreira",
            "owner_phone": "+244 923 456 789",
            "owner_id_number": "004512389LA041",
        }
        dados.update(extra)
        return dados

    def _foto(self, nome: str = "casa.jpg", *, cor: tuple[int, int, int] = (40, 32, 22)) -> SimpleUploadedFile:
        return SimpleUploadedFile(nome, jpeg_bytes(cor), content_type="image/jpeg")

    def test_a_pagina_desenha_o_campo_de_fotografias(self) -> None:
        """O campo no formulário e ausente na página é um campo que volta vazio.

        O inverso do que se mede na ficha: se o formulário tem `images` e a página
        não desenha o input, o `POST` não traz ficheiro nenhum e o registo fica
        sem fotografia sem erro nenhum.
        """
        html = self.client.get(self.url).content.decode("utf-8")

        self.assertIn('name="images"', html)
        self.assertIn('type="file"', html)
        self.assertIn("multipart/form-data", html)

    def test_o_campo_e_opcional_e_nao_bloqueia_o_envio(self) -> None:
        """Um `required` no HTML tranca o browser e o servidor aceitaria vazio.

        O `BaseStyledForm` põe `required` no widget de cada campo obrigatório, e
        o sinal que torna as fotografias opcionais corre depois de `super()`. A
        atributo que ficasse no HTML fazia o browser recusar o envio de quem
        quer registar o imóvel primeiro e fotografar depois.
        """
        html = self.client.get(self.url).content.decode("utf-8")
        campo = re.search(r'<input[^>]*name="images"[^>]*>', html)

        self.assertIsNotNone(campo)
        self.assertNotIn("required", campo.group(0))

    def test_registar_sem_fotografias_e_guardado(self) -> None:
        """O imóvel nasce em rascunho, e um rascunho não tem fotografia nenhuma."""
        resposta = self.client.post(self.url, self._dados())

        self.assertEqual(resposta.status_code, 302)
        immobile = Property.objects.get(title="T3 no Kilamba com quintal")
        self.assertEqual(immobile.status, Property.Status.DRAFT)
        self.assertEqual(immobile.images.count(), 0)
        self.assertNotIn("#fotografias", resposta["Location"])

    def test_o_lote_vai_no_mesmo_pedido_e_a_primeira_e_a_capa(self) -> None:
        """Carregar cinco de uma vez, e a primeira escolhida é a capa."""
        resposta = self.client.post(
            self.url,
            self._dados(
                images=[
                    self._foto("primeira.jpg", cor=(200, 30, 30)),
                    self._foto("segunda.jpg", cor=(30, 200, 30)),
                    self._foto("terceira.jpg", cor=(30, 30, 200)),
                ]
            ),
        )

        self.assertEqual(resposta.status_code, 302)
        immobile = Property.objects.get(title="T3 no Kilamba com quintal")
        self.assertEqual(immobile.images.count(), 3)
        # A numeração segue a ordem de escolha, e a capa é a primeira.
        self.assertEqual(
            [imagem.sort_order for imagem in immobile.images.all()], [0, 1, 2]
        )
        self.assertIn("primeira", immobile.images.first().image.name)
        self.assertTrue(resposta["Location"].endswith("#fotografias"))

    def test_um_ficheiro_mau_nao_leva_o_lote_consigo(self) -> None:
        """As boas entram, a má é recusada, e a página diz qual foi."""
        texto = SimpleUploadedFile("nota.txt", b"isto nao e uma imagem", content_type="text/plain")

        resposta = self.client.post(
            self.url, self._dados(images=[self._foto("boa.jpg"), texto])
        )

        self.assertEqual(resposta.status_code, 302)
        immobile = Property.objects.get(title="T3 no Kilamba com quintal")
        self.assertEqual(immobile.images.count(), 1)
        avisos = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertTrue(any("nota.txt" in aviso for aviso in avisos), avisos)

    def test_o_excesso_diz_se_e_guarda_o_que_cabe(self) -> None:
        """Dezoito escolhidos com três lá dentro guarda três e avisa.

        Recusar o lote inteiro obrigaria a contar o que já lá estava, e o número
        que o formulário anuncia é o número que a pessoa viu no ecrã.
        """
        lote = [self._foto(f"{indice}.jpg", cor=(indice * 10, 0, 0)) for indice in range(MAX_FOTOS + 3)]

        resposta = self.client.post(self.url, self._dados(images=lote))

        immobile = Property.objects.get(title="T3 no Kilamba com quintal")
        self.assertEqual(immobile.images.count(), MAX_FOTOS)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertTrue(any("15" in mensagem for mensagem in mensagens), mensagens)


class DivisaoTrancadaTests(TestCase):
    """O trinco que a posição do dispositivo põe na divisão (§2.3).

    Ler a divisão fora do `POST` é o perigo disto: um campo `disabled` não viaja,
    e o imóvel gravava-se sem província com a página a dizer que estava tudo
    certo. O JavaScript cobre-se com um espelho escondido, e estes testes são o
    chão que fica por baixo dele — o servidor recusa a divisão em falta em vez de
    a aceitar, que é o que torna o estrago visível em vez de silencioso.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.url = reverse("properties:curator_create")
        self.client.force_login(self.curator)

    def _dados(self, **extra: object) -> dict[str, object]:
        dados: dict[str, object] = {
            "title": "T3 no Kilamba com quintal",
            "description": "Sala ampla, dois quartos e quintal.",
            "type": Property.Type.APARTMENT,
            "purpose": Property.Purpose.RENT,
            "price": "450000.00",
            "lease_term_months": 12,
            "province_ref": "LUANDA",
            "municipality": "Talatona",
            "locality": "Kilamba",
            "latitude": "-8.918430",
            "longitude": "13.184700",
            "owner_name": "Joaquim Ferreira",
            "owner_phone": "+244 923 456 789",
            "owner_id_number": "004512389LA041",
        }
        dados.update(extra)
        return dados

    def test_a_divisao_que_o_javascript_tranca_e_obrigatoria(self) -> None:
        """O trinco só pode cair em campos que o servidor já exigia.

        Trancar um campo opcional não estraga nada; trancar um obrigatório sem o
        valor viajar é a divisão a desaparecer. Saber quais são os campos é o que
        impede o `echilo.js` de ir buscar o elemento errado e de o trancar em
        silêncio, sem a página dizer nada.
        """
        html = self.client.get(self.url).content.decode("utf-8")
        for campo in ("province_ref", "municipality"):
            with self.subTest(campo=campo):
                self.assertIn(f'name="{campo}"', html)

        form = PropertyCuratorForm()
        for campo in ("province_ref", "municipality"):
            with self.subTest(campo=campo):
                self.assertTrue(form.fields[campo].required)

    def test_a_divisao_que_o_mapa_nao_sabe_nao_e_trancada_com_valor_vazio(self) -> None:
        """O trinco é por campo, e só quando o mapa preencheu.

        Um campo trancado e vazio é um formulário que não sai e não tem como ser
        corrigido sem recarregar a página. Um município que a fonte não desenha —
        são mais de trinta — tem de continuar a ser escrito à mão, e a província
        continua a poder ser trancada ao lado dele.
        """
        dados = self._dados(province_ref="", municipality="")

        resposta = self.client.post(self.url, dados)

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Property.objects.exists())
        contexto = resposta.context
        self.assertIsNotNone(contexto)
        form = contexto["form"]
        self.assertIn("province_ref", form.errors)
        self.assertIn("municipality", form.errors)

    def test_a_divisao_que_o_mapa_escreve_viaja_no_pedido(self) -> None:
        """O caminho que o espelho tem de servir: um `POST` normal.

        O espelho é um `input type="hidden"` com o `name` do campo, e o Django não
        distingue um `readonly` de um que a pessoa preencheu. Um imóvel registado
        pela posição do aparelho tem de ficar com a mesma divisão que um registado
        a dedo, ou a distinção entre as duas coisas é só uma ilusão de ecrã.
        """
        resposta = self.client.post(self.url, self._dados())

        self.assertEqual(resposta.status_code, 302)
        immobile = Property.objects.get(title="T3 no Kilamba com quintal")
        self.assertEqual(immobile.province_ref, "LUANDA")
        self.assertEqual(immobile.municipality, "Talatona")
        self.assertEqual(Decimal(immobile.latitude), Decimal("-8.918430"))
        self.assertEqual(Decimal(immobile.longitude), Decimal("13.184700"))


class ApagarImovelTests(TestCase):
    """Apagar é a acção que não tem volta, e por isso a regra é testada antes do botão.

    A regra é do produto e não do ficheiro: apaga-se o imóvel que ainda não foi
    transacionado, e o arquivado é do administrador. Cada motivo de recusa tem o
    seu teste, porque um motivo que se cala ao passar do `AGENT` para o `ADMIN` dá
    um botão que aparece para um e desaparece para o outro sem ninguém saber porquê.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.agent = make_user(role="AGENT", email="agente@echilo.ao")
        self.admin = make_user(role="ADMIN", email="chefe@echilo.ao", is_staff=True)
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.UNDER_VALIDATION,
        )
        self.detail_url = reverse("properties:curator_detail", args=[self.prop.reference])
        self.delete_url = reverse("properties:curator_delete", args=[self.prop.reference])
        self.dashboard_url = reverse("properties:curator_dashboard")

    def _dados(self, **overrides: object) -> dict[str, object]:
        dados: dict[str, object] = {
            "reference": self.prop.reference,
            "reason": "Duplicado do ECH-LU-0002, carregado duas vezes.",
        }
        dados.update(overrides)
        return dados

    def _arquiva(self, prop: Property | None = None) -> None:
        alvo = prop or self.prop
        alvo.status = Property.Status.ARCHIVED
        alvo.save(update_fields=["status"])

    def _aceita_oferta(self, prop: Property | None = None) -> None:
        from apps.concierge.models import Offer

        alvo = prop or self.prop
        Offer.objects.create(
            property=alvo,
            submitted_by=make_user(email="comprador@echilo.ao"),
            amount=Decimal("450000.00"),
            status=Offer.Status.ACCEPTED,
        )

    def _confirma_visita(self, prop: Property | None = None) -> None:
        from django.utils import timezone

        from apps.concierge.models import VisitRequest

        alvo = prop or self.prop
        VisitRequest.objects.create(
            property=alvo,
            requested_by=make_user(email="visitante@echilo.ao"),
            scheduled_for=timezone.now() + timezone.timedelta(days=2),
            status=VisitRequest.Status.CONFIRMED,
        )

    def test_o_agente_apaga_o_imovel_e_o_registo_sobrevive(self) -> None:
        """O apagamento tem de ficar escrito mesmo depois de o imóvel deixar de existir."""
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.dashboard_url)
        self.assertFalse(Property.objects.filter(pk=self.prop.pk).exists())
        registo = PropertyDeletion.objects.get(reference=self.prop.reference)
        self.assertEqual(registo.actor, self.agent)
        self.assertEqual(registo.status_at_deletion, Property.Status.UNDER_VALIDATION)
        self.assertIn("Duplicado", registo.reason)

    def test_o_ficheiro_da_fotografia_vai_com_a_linha(self) -> None:
        """Uma fotografia órfã na Cloudinary é uma factura mensal de ninguém."""
        imagem = make_image(self.prop)
        caminho = imagem.image.name
        self.client.force_login(self.agent)

        self.client.post(self.delete_url, self._dados())

        self.assertFalse(PropertyImage.objects.exists())
        self.assertFalse(os.path.exists(str(Path(settings.MEDIA_ROOT) / caminho)))

    def test_a_prova_de_quem_abriu_a_escritura_sobrevive(self) -> None:
        """O §6 pede o registo de acesso, e o ficheiro pode ir. A prova fica.

        `DocumentAccessLog` apontava para o documento com `PROTECT`, o que tornava
        o apagamento impossível. Passou a retrato: o acesso guarda a referência, o
        tipo e o nome do ficheiro, e é isso que sobrevive à ida do ficheiro.
        """
        documento = PropertyDocument.objects.create(
            property=self.prop,
            document_type=PropertyDocument.DocumentType.OWNERSHIP_TITLE,
            file=SimpleUploadedFile("escritura.pdf", b"%PDF-1.4 stub", content_type="application/pdf"),
        )
        acesso = DocumentAccessLog.objects.create(document=documento, actor=self.agent)
        self.client.force_login(self.agent)

        self.client.post(self.delete_url, self._dados())

        acesso.refresh_from_db()
        self.assertIsNone(acesso.document)
        self.assertEqual(acesso.property_reference, self.prop.reference)
        self.assertEqual(acesso.document_type, PropertyDocument.DocumentType.OWNERSHIP_TITLE)
        self.assertTrue(acesso.file_name.endswith(".pdf"))

    def test_o_motivo_e_obrigatorio(self) -> None:
        """Um imóvel apagado sem explicação é indistinguível de um erro de dedo."""
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados(reason="   "))

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())
        self.assertFalse(PropertyDeletion.objects.exists())

    def test_a_referencia_tem_de_bater_com_a_da_ficha(self) -> None:
        """Ninguém apaga o imóvel errado a partir de uma ficha com o título parecido."""
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados(reference="ECH-LU-9999"))

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())

    def test_a_referencia_aceita_minusculas(self) -> None:
        """Quem copia a referência da ficha escreve-a como a lê; maiúsculas não devem mandar."""
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados(reference=self.prop.reference.lower()))

        self.assertRedirects(resposta, self.dashboard_url)
        self.assertFalse(Property.objects.filter(pk=self.prop.pk).exists())

    def test_um_cliente_nao_apaga(self) -> None:
        cliente = make_user(email="cliente@echilo.ao")
        self.client.force_login(cliente)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())

    def test_um_curador_nao_apaga(self) -> None:
        """Curadoria cria e edita imóveis; não retira fichas públicas."""
        self.client.force_login(self.curator)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())

    def test_um_get_nao_apaga(self) -> None:
        """A rota responde a GET a redireitar, e nunca apaga por GET."""
        self.client.force_login(self.agent)

        resposta = self.client.get(self.delete_url)

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())

    def test_proposta_aceita_impede_o_apagamento(self) -> None:
        """O imóvel vendido tem comprador em papel, e o papel é a prova da venda."""
        self._aceita_oferta()
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertTrue(any("proposta aceite" in m.lower() for m in mensagens), mensagens)

    def test_visita_confirmada_impede_o_apagamento(self) -> None:
        self._confirma_visita()
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())

    def test_visita_recusada_nao_impede_o_apagamento(self) -> None:
        """O que não chegou a acontecer não prende o imóvel."""
        from django.utils import timezone

        from apps.concierge.models import VisitRequest

        VisitRequest.objects.create(
            property=self.prop,
            requested_by=make_user(email="outro@echilo.ao"),
            scheduled_for=timezone.now() + timezone.timedelta(days=2),
            status=VisitRequest.Status.DECLINED,
        )
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.dashboard_url)
        self.assertFalse(Property.objects.filter(pk=self.prop.pk).exists())

    def test_o_agente_nao_apaga_um_arquivado(self) -> None:
        """Arquivar é o caminho que o produto desenhou; contorná-lo é de chefia."""
        self._arquiva()
        self.client.force_login(self.agent)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.detail_url)
        self.assertTrue(Property.objects.filter(pk=self.prop.pk).exists())
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertTrue(any("administrador" in m.lower() for m in mensagens), mensagens)

    def test_o_administrador_apaga_um_arquivado(self) -> None:
        self._arquiva()
        self.client.force_login(self.admin)

        resposta = self.client.post(self.delete_url, self._dados())

        self.assertRedirects(resposta, self.dashboard_url)
        self.assertFalse(Property.objects.filter(pk=self.prop.pk).exists())
        self.assertEqual(
            PropertyDeletion.objects.get(reference=self.prop.reference).status_at_deletion,
            Property.Status.ARCHIVED,
        )

    def test_a_ficha_diz_porque_nao_se_pode_apagar(self) -> None:
        """Um botão que desapareceu sem frase lê-se como uma página partida."""
        self._aceita_oferta()
        self.client.force_login(self.agent)

        resposta = self.client.get(self.detail_url)

        html = resposta.content.decode("utf-8")
        self.assertIn("proposta aceite", html)
        self.assertNotIn("Apagar {{ property.reference }}", html)

    def test_a_ficha_oferece_o_formulario_ao_agente(self) -> None:
        self.client.force_login(self.agent)

        html = self.client.get(self.detail_url).content.decode("utf-8")

        self.assertIn(self.delete_url, html)
        self.assertIn("Motivo do apagamento", html)

    def test_a_ficha_nao_oferece_o_formulario_ao_curador(self) -> None:
        self.client.force_login(self.curator)

        html = self.client.get(self.detail_url).content.decode("utf-8")

        self.assertNotIn(self.delete_url, html)


class MarcasDeTemplateTests(TestCase):
    """Nenhuma página pode entregar marca de template ao browser.

    Um `{%` sem o `%}` da mesma linha não dá erro de sintaxe. O `tag_re` do
    Django procura o fecho no ficheiro todo e engole tudo o que está pelo meio,
    e o token resultante sai como texto: a página aparece com o tag escrito à
    letra e com o conteúdo do meio simplesmente em falta. O `href` de um botão
    passava a ser `/imovel/ECH-LU-0006/{% url 'assistant:chat'`, que é um 404
    quando somebody clica nele, e ninguém vía o erro em lado nenhum.

    A verificação é sobre o HTML servido e não sobre o ficheiro: o que interessa
    é o que chega ao browser, e um tag bem formado que não resolve dá a mesma
    página. Uma linha por marca é o teste, e é mais largo do que o bug — apanha
    o mesmo estrago em qualquer template.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.PUBLISHED,
        )
        make_image(self.prop, caption="Fachada")

    def _paginas(self) -> list[tuple[str, str]]:
        return [
            ("home", reverse("properties:home")),
            ("catálogo", reverse("properties:property_list")),
            (
                "ficha do imóvel",
                reverse("properties:property_detail", args=[self.prop.reference]),
            ),
        ]

    def test_nenhuma_pagina_publica_entrega_marca_de_template(self) -> None:
        for nome, url in self._paginas():
            with self.subTest(pagina=nome):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 200)
                html = resposta.content.decode("utf-8")
                for marca in ("{%", "{{", "{#"):
                    self.assertNotIn(marca, html)

    def test_os_links_da_home_apontam_para_algo(self) -> None:
        """Um `href` que não é um URL é um botão que não vai a lado nenhum."""
        html = self.client.get(reverse("properties:home")).content.decode("utf-8")

        for href in re.findall(r'href="([^"]*)"', html):
            with self.subTest(href=href[:60]):
                self.assertTrue(
                    href.startswith("/") or href.startswith("#") or href.startswith("http"),
                    "href que não é caminho: %s" % href[:60],
                )


class AngolaReferenceTests(SimpleTestCase):
    """As listas fechadas que os formulários oferecem.

    São dados de referência, não regras: o que não se pode é a lista mudar de
    baixo dos pés de quem a escreveu, ou uma província ficar de fora sem que
    alguém se aperceba.
    """

    def test_a_lista_tem_vinte_e_uma_provincias(self) -> None:
        self.assertEqual(len(ANGOLA_PROVINCES), 21)

    def test_toda_provincia_tem_a_sua_lista_de_municipios(self) -> None:
        """Uma província sem lista é uma província em que o campo não oferece nada."""
        sem_lista = [
            rotulo
            for codigo, rotulo in ANGOLA_PROVINCES
            if not municipalities_for(codigo)
        ]
        self.assertEqual(sem_lista, [])

    def test_nenhuma_lista_repete_municipio(self) -> None:
        for codigo, lista in MUNICIPALITIES_BY_PROVINCE.items():
            with self.subTest(provincia=codigo):
                self.assertEqual(len(lista), len(set(lista)))

    def test_a_provincia_vazia_traz_a_uniao_sem_repetir(self) -> None:
        """«Todas as províncias» oferece tudo, e `Calai` está em duas listas.

        Sem o `set`, o formulário de «Todas as províncias» ofereceria Calai duas
        vezes, uma por cada província onde ele está.
        """
        self.assertIn("Calai", ALL_MUNICIPALITIES)
        self.assertEqual(
            ALL_MUNICIPALITIES,
            tuple(sorted({m for lista in MUNICIPALITIES_BY_PROVINCE.values() for m in lista})),
        )

    def test_as_duas_provincias_sem_contorno_sao_declaradas(self) -> None:
        self.assertEqual(
            provinces_without_boundary(), ("Icolo e Bengo", "Moxico Leste")
        )


class PropertySearchTests(TestCase):
    """A pesquisa pública e a ferramenta da IA partilham os mesmos filtros."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.kilamba = make_property(
            curator=self.curator,
            owner=self.owner,
            municipality="Talatona",
            locality="Kilamba",
            title="T3 no Kilamba com quintal",
        )
        self.ingombota = make_property(
            curator=self.curator,
            owner=self.owner,
            municipality="Luanda",
            locality="Ingombota",
            title="T1 no Ingombota",
        )

    def test_search_matches_the_neighbourhood(self) -> None:
        """Procurar por bairro encontra o imóvel, mesmo sendo outro município."""
        found = PropertyQueryService.search(PropertyFilters(municipality="Kilamba"))

        self.assertEqual([prop.reference for prop in found], [self.kilamba.reference])

    def test_search_matches_the_municipality(self) -> None:
        """Procurar por município também funciona."""
        found = PropertyQueryService.search(PropertyFilters(municipality="Luanda"))

        self.assertEqual([prop.reference for prop in found], [self.ingombota.reference])

    def test_search_is_case_insensitive(self) -> None:
        """A pesquisa ignora maiúsculas e minúsculas."""
        found = PropertyQueryService.search(PropertyFilters(municipality="kilamba"))

        self.assertEqual(len(found), 1)

    def test_chips_preserve_the_other_filters(self) -> None:
        """`chip_query` só mexe no filtro indicado."""
        filters = PropertyFilters(province="LUANDA", municipality="Kilamba")

        self.assertEqual(
            filters.chip_query(purpose="RENT", type=""),
            "province=LUANDA&municipality=Kilamba&purpose=RENT",
        )
        self.assertEqual(
            filters.chip_query(purpose="", type="LAND"),
            "province=LUANDA&municipality=Kilamba&type=LAND",
        )
        self.assertEqual(PropertyFilters().chip_query(purpose="", type=""), "")

    def test_unpublished_property_is_never_returned(self) -> None:
        """A pesquisa só devolve imóveis publicados."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.IN_REVIEW,
            municipality="Talatona",
            locality="Kilamba",
            title="Em curadoria no Kilamba",
        )

        found = PropertyQueryService.search(PropertyFilters(municipality="Kilamba"))

        self.assertEqual(len(found), 1)


# 0,02 graus a sul do centro do Kilamba: perto de 2,2 km, o raio que os testes
# de borda vão derivar da distância real em vez de a estimar à mão.
EDGE_LATITUDE = Decimal("-8.938430")
EDGE_LONGITUDE = Decimal("13.184700")


class PropertyAreaSearchTests(TestCase):
    """A pesquisa por área só entra o que o círculo realmente cobre (§2.3)."""

    # O centro do Kilamba, onde `make_property` coloca o imóvel por omissão.
    CENTER = PropertyFilters(center_lat=Decimal("-8.918430"), center_lon=Decimal("13.184700"))

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.inside = make_property(
            curator=self.curator,
            owner=self.owner,
            title="T3 no Kilamba com quintal",
        )
        # Um grau de latitude a sul: cerca de 111 km, fora de qualquer raio útil.
        self.far_away = make_property(
            curator=self.curator,
            owner=self.owner,
            latitude=Decimal("-9.918430"),
            longitude=Decimal("13.184700"),
            municipality="Benguela",
            locality="Lobito",
            title="Moradia em Benguela",
        )

    def area(self, radius_m: int, **extra: object) -> PropertyFilters:
        """Monta os filtros com o raio pedido e o centro deste teste."""
        return PropertyFilters(
            center_lat=self.CENTER.center_lat,
            center_lon=self.CENTER.center_lon,
            radius_m=radius_m,
            **extra,
        )

    @property
    def edge_distance(self) -> float:
        """Distância real, em metros, até ao imóvel plantado na borda."""
        return haversine_metres(
            float(self.CENTER.center_lat),
            float(self.CENTER.center_lon),
            float(EDGE_LATITUDE),
            float(EDGE_LONGITUDE),
        )

    def property_on_the_edge(self, title: str) -> Property:
        """Cria um imóvel exactamente onde a borda do círculo vai cair."""
        return make_property(
            curator=self.curator,
            owner=self.owner,
            latitude=EDGE_LATITUDE,
            longitude=EDGE_LONGITUDE,
            title=title,
        )

    def test_only_properties_inside_the_circle_are_returned(self) -> None:
        """Um raio de 2 km em torno do Kilamba não apanha Benguela."""
        found = PropertyQueryService.search(self.area(2_000))

        self.assertEqual([prop.reference for prop in found], [self.inside.reference])

    def test_results_are_ordered_by_distance(self) -> None:
        """Com área, o mais perto do centro vem primeiro, mesmo tendo sido criado depois."""
        near = make_property(
            curator=self.curator,
            owner=self.owner,
            latitude=Decimal("-8.928430"),
            longitude=Decimal("13.184700"),
            title="T1 a um quilometro",
        )

        found = PropertyQueryService.search(self.area(5_000))

        # `self.inside` está mesmo no centro, logo vai primeiro apesar de ser o
        # mais antigo: sem isto, a ordenação por data passaria o teste.
        self.assertEqual(
            [prop.reference for prop in found],
            [self.inside.reference, near.reference],
        )

    def test_the_edge_of_the_circle_is_inclusive(self) -> None:
        """A borda entra: quem está a exactamente o raio está dentro do raio."""
        edge = self.property_on_the_edge("T2 na borda do círculo")

        found = PropertyQueryService.search(self.area(math.ceil(self.edge_distance)))

        self.assertIn(edge.reference, [prop.reference for prop in found])

    def test_property_just_outside_the_edge_is_excluded(self) -> None:
        """A caixa envolvente é folgada, mas a distância verdadeira decide."""
        just_outside = self.property_on_the_edge("T2 logo fora do raio")

        found = PropertyQueryService.search(self.area(math.floor(self.edge_distance)))

        self.assertNotIn(just_outside.reference, [prop.reference for prop in found])

    def test_area_composes_with_the_other_filters(self) -> None:
        """A área não apaga a restante pesquisa: os dois filtros mandam em conjunto."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            title="Moradia para venda no Kilamba",
        )

        found = PropertyQueryService.search(self.area(2_000, purpose=Property.Purpose.RENT))

        self.assertEqual([prop.reference for prop in found], [self.inside.reference])

    def test_property_without_coordinates_is_never_matched(self) -> None:
        """Sem pin verificado não há onde procurar o imóvel."""
        without_pin = make_property(
            curator=self.curator,
            owner=self.owner,
            latitude=None,
            longitude=None,
            title="Imóvel sem coordenadas",
        )
        without_pin.location_verified_at = None
        without_pin.save()

        found = PropertyQueryService.search(self.area(50_000))

        self.assertNotIn(without_pin.reference, [prop.reference for prop in found])

    def test_unpublished_property_inside_the_area_is_not_leaked(self) -> None:
        """Um imóvel em curadoria não é publicado por estar dentro do raio."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.IN_REVIEW,
            title="Em curadoria dentro do raio",
        )

        found = PropertyQueryService.search(self.area(2_000))

        self.assertEqual([prop.reference for prop in found], [self.inside.reference])

    def test_area_filter_does_not_add_queries(self) -> None:
        """A área é filtrada em Python, mas não pode multiplicar as consultas."""
        with self.assertNumQueries(2):
            list(PropertyQueryService.search(self.area(5_000)))

    def test_area_survives_the_query_params(self) -> None:
        """A área tem de sobreviver à paginação e aos chips, senão perde-se a pesquisa."""
        params = self.area(2_000).as_query_params()

        self.assertEqual(params["center_lat"], "-8.918430")
        self.assertEqual(params["center_lon"], "13.184700")
        self.assertEqual(params["radius_m"], "2000")
        self.assertIn("radius_m=2000", self.area(2_000).chip_query(purpose="RENT"))

    def test_no_area_means_no_params(self) -> None:
        """Sem círculo, a URL fica limpa como sempre esteve."""
        self.assertNotIn("radius_m", PropertyFilters(province="LUANDA").as_query_params())


class PropertyAreaQueryStringTests(TestCase):
    """Uma URL à mão não pode travar nem alargar a pesquisa para sempre."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)

    def filters_for(self, query: dict[str, str]) -> PropertyFilters:
        """Passa a query por `from_query`, como a view faz."""
        return PropertyFilters.from_query(query)

    def test_a_complete_area_is_accepted(self) -> None:
        """Com centro e raio, a área entra."""
        filters = self.filters_for(
            {"center_lat": "-8.918430", "center_lon": "13.184700", "radius_m": "2000"}
        )

        self.assertTrue(filters.has_area())
        self.assertEqual(filters.radius_m, 2_000)

    def test_half_an_area_is_ignored(self) -> None:
        """Centro sem raio, ou raio sem centro, não é uma pesquisa."""
        without_radius = self.filters_for({"center_lat": "-8.918430", "center_lon": "13.184700"})
        without_center = self.filters_for({"radius_m": "2000"})

        self.assertFalse(without_radius.has_area())
        self.assertFalse(without_center.has_area())

    def test_malformed_area_is_ignored(self) -> None:
        """Texto, coordenadas fora do planeta e raios absurdos caem fora."""
        for query in (
            {"center_lat": "abc", "center_lon": "13.18", "radius_m": "2000"},
            {"center_lat": "-8.91", "center_lon": "13.18", "radius_m": "abc"},
            {"center_lat": "-91", "center_lon": "13.18", "radius_m": "2000"},
            {"center_lat": "-8.91", "center_lon": "999", "radius_m": "2000"},
            {"center_lat": "-8.91", "center_lon": "13.18", "radius_m": "0"},
            {"center_lat": "-8.91", "center_lon": "13.18", "radius_m": "-5000"},
            {"center_lat": "-8.91", "center_lon": "13.18", "radius_m": "5000000"},
            {"center_lat": "NaN", "center_lon": "13.18", "radius_m": "2000"},
        ):
            with self.subTest(query=query):
                self.assertFalse(self.filters_for(query).has_area())

    def test_the_other_filters_survive_a_rejected_area(self) -> None:
        """Descartar a área não pode arrastar a província com ela."""
        filters = self.filters_for(
            {
                "province": "LUANDA",
                "center_lat": "abc",
                "center_lon": "13.18",
                "radius_m": "2000",
            }
        )

        self.assertFalse(filters.has_area())
        self.assertEqual(filters.province, "LUANDA")


class ProvinceScopedMunicipalityTests(TestCase):
    """As sugestões de município seguem a província escolhida.

    O campo aceita texto livre e as sugestões são o que o formulário oferece; se
    as duas coisas divergirem, quem escreve "Viana" com Benguela escolhida
    recebe uma lista onde Viana não está, e não descobre se errou ou se o
    sistema está partido.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.benguela = make_property(
            curator=self.curator,
            owner=self.owner,
            province_ref="BENGUELA",
            municipality="Catumbela",
        )
        self.viana = make_property(
            curator=self.curator,
            owner=self.owner,
            province_ref="LUANDA",
            municipality="Viana",
        )
        self.client.force_login(self.curator)

    def test_o_campo_da_equipa_tem_o_datalist_que_o_input_aponta(self) -> None:
        """O `list` do campo aponta para um `id` que existe na mesma página.

        Antes não apontava para nada, e nada denunciava: o campo continuava a
        aceitar texto, a equipa escrevia o município à mão, e o sintoma era uma
        lista de opções em falta que ninguém conseguia nomear. Um atributo que
        aponta para o vazio não dá erro, e o campo parece funcionar — só não
        sugere.
        """
        for url in (
            reverse("properties:curator_create"),
            reverse("properties:curator_detail", args=[self.benguela.reference]),
        ):
            with self.subTest(pagina=url):
                html = self.client.get(url).content.decode()
                campo = re.search(r'<input[^>]*\bname="municipality"[^>]*>', html)
                self.assertIsNotNone(campo, "o formulário da equipa não tem o campo município")
                alvo = re.search(r'\blist="([^"]+)"', campo.group(0))
                self.assertIsNotNone(alvo, "o campo município não aponta para nenhuma lista")
                self.assertIn(
                    f'<datalist id="{alvo.group(1)}"',
                    html,
                    f'o `list` aponta para {alvo.group(1)!r} e esse id não está na página',
                )

    def test_o_datalist_da_equipa_traz_a_lista_da_provincia_do_imovel(self) -> None:
        """As sugestões são as da província que o imóvel já tem, não as de todas."""
        html = self.client.get(
            reverse("properties:curator_detail", args=[self.benguela.reference])
        ).content.decode()

        bloco = re.search(
            r'<datalist id="municipality-options">(.*?)</datalist>', html, re.DOTALL
        )
        opcoes = re.findall(r'value="([^"]*)"', bloco.group(1))
        self.assertIn("Catumbela", opcoes)
        self.assertNotIn("Cazombo", opcoes)

    def test_o_botao_de_centrar_vive_sobre_o_mapa(self) -> None:
        """O botão tem de estar dentro do contentor do mapa, não no painel ao lado.

        A diferença entre "centrar no mapa" e "centrar ao lado do mapa" é só de
        onde a coisa está escrita, e o teste é o que diz qual das duas foi
        feita. `leaflet.draw` continua em cima à esquerda, e este em cima à
        direita.
        """
        html = self._html_da_pagina({})

        self.assertIn("area-centrar", ids_dentro_de(html, "area-map"))

    def test_o_botao_de_centrar_e_um_botao_com_nome(self) -> None:
        """`type="button"`, nome visível, e nome acessível que diz o que faz.

        Um `Centrar` sem nome acessível não diz a ninguém se é o centro do mapa ou
        o centro de quem o vê, e um `div` com um clique não chega ao teclado.
        """
        html = self._html_da_pagina({})

        botao = re.search(r'<button[^>]*\bid="area-centrar"[^>]*>.*?</button>', html, re.DOTALL)
        self.assertIsNotNone(botao, "o botão de centrar não está no HTML")
        marca = botao.group(0)
        self.assertIn('type="button"', marca)
        nome = re.sub(r"<[^>]+>", "", marca).strip()
        self.assertTrue(nome, "o botão de centrar não tem nome visível")
        rotulo = re.search(r'aria-label="([^"]+)"', marca)
        self.assertIsNotNone(rotulo, "o botão de centrar não tem nome acessível")
        self.assertIn(nome, rotulo.group(1))

    def test_a_mensagem_da_localizacao_e_um_status_e_nao_um_alerta(self) -> None:
        """A caixa dos tiles já é um `alert`; dois alertas leem a mensagem errada."""
        html = self._html_da_pagina({})

        caixa = re.search(r'<p[^>]*\bid="area-local"[^>]*>', html)
        self.assertIsNotNone(caixa, "falta a caixa que fala da localização")
        self.assertIn('role="status"', caixa.group(0))
        self.assertNotIn('role="alert"', caixa.group(0))

    def test_a_sugestao_de_luanda_nao_inclui_catumbela(self) -> None:
        response = self.client.get(reverse("properties:property_list"), {"province": "LUANDA"})
        sugestoes = response.context["municipality_suggestions"]
        self.assertIn("Viana", sugestoes)
        self.assertNotIn("Catumbela", sugestoes)

    def test_a_sugestao_de_benguela_nao_inclui_viana(self) -> None:
        response = self.client.get(reverse("properties:property_list"), {"province": "BENGUELA"})
        sugestoes = response.context["municipality_suggestions"]
        self.assertIn("Catumbela", sugestoes)
        self.assertNotIn("Viana", sugestoes)

    def test_a_sugestao_traz_a_lista_da_provincia_mesmo_sem_imoveis(self) -> None:
        """"Moxico Leste" não tem nenhum imóvel, e as sugestões não ficam vazias.

        Uma lista que só sai dos imóveis publicados oferece zero para quem está
        a filter por uma província ainda sem catálogo, e o campo parece partido.
        """
        response = self.client.get(
            reverse("properties:property_list"), {"province": "MOXICO_LESTE"}
        )
        sugestoes = response.context["municipality_suggestions"]
        self.assertIn("Cazombo", sugestoes)
        self.assertNotIn("Viana", sugestoes)

    def test_sem_provincia_as_sugestoes_trazem_todos_os_municipios(self) -> None:
        response = self.client.get(reverse("properties:property_list"))
        sugestoes = response.context["municipality_suggestions"]
        self.assertIn("Viana", sugestoes)
        self.assertIn("Cazombo", sugestoes)
        self.assertIn("Catumbela", sugestoes)

    def test_o_HTML_mostra_a_sugestao_da_provincia_escolhida(self) -> None:
        """O que aparece sem JavaScript é o que o servidor escreveu."""
        html = self._html_da_pagina({"province": "LUANDA"})
        self.assertIn('value="Talatona"', html)
        self.assertNotIn('value="Catumbela"', html)

    def test_a_pagina_nao_escreve_as_outras_provincias_no_datalist(self) -> None:
        """O `<datalist>` que a página entrega tem só a província escolhida.

        A página não leva os cento e setenta e um nomes no HTML à espera de
        alguém escrever: escrevê-los todos seria oferecer todos, que é o
        contrário do que o filtro promete.
        """
        html = self._html_da_pagina({"province": "BENGUELA"})
        sugestoes = self._datalist(html)
        self.assertIn("Catumbela", sugestoes)
        self.assertNotIn("Talatona", sugestoes)
        self.assertNotIn("Cazombo", sugestoes)

    @staticmethod
    def _datalist(html: str) -> list[str]:
        """As opções do `<datalist>` que o HTML carrega, e nada mais.

        Medir a página inteira é medir o que não é a lista: o `placeholder` do
        campo, o cartão de um imóvel e o texto de ajuda são todos coisas que
        podem escrever um nome de município sem que a lista o contenha.
        """
        bloco = re.search(
            r'<datalist id="municipios-disponiveis">(.*?)</datalist>', html, re.DOTALL
        )
        if bloco is None:
            return []
        return re.findall(r'value="([^"]*)"', bloco.group(1))

    def _html_da_pagina(self, parametros: dict[str, str]) -> str:
        response = self.client.get(reverse("properties:property_list"), parametros)
        return response.content.decode()

    def test_o_endpoint_devolve_a_lista_da_provincia(self) -> None:
        """O JavaScript troca as opções por aqui, e o que ele recebe é o mesmo
        que o `<datalist>` da página tinha."""
        response = self.client.get(reverse("properties:municipalities"), {"province": "BENGUELA"})

        self.assertEqual(response.status_code, 200)
        sugestoes = response.json()["sugestoes"]
        self.assertIn("Catumbela", sugestoes)
        self.assertNotIn("Talatona", sugestoes)

    def test_o_endpoint_sem_provincia_devolve_todos(self) -> None:
        response = self.client.get(reverse("properties:municipalities"))
        sugestoes = response.json()["sugestoes"]
        self.assertIn("Talatona", sugestoes)
        self.assertIn("Cazombo", sugestoes)

    def test_o_endpoint_da_a_mesma_lista_que_a_pagina(self) -> None:
        """Duas listas parecidas divergem na segunda mudança de província."""
        pagina = self.client.get(reverse("properties:property_list"), {"province": "LUANDA"})
        endpoint = self.client.get(reverse("properties:municipalities"), {"province": "LUANDA"})
        self.assertEqual(
            pagina.context["municipality_suggestions"], endpoint.json()["sugestoes"]
        )


class PropertyAreaViewTests(TestCase):
    """O catálogo tem de responder com e sem JavaScript, e sem focar a área no mapa."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)
        self.area_query = {
            "center_lat": "-8.918430",
            "center_lon": "13.184700",
            "radius_m": "2000",
        }

    def test_catalog_renders_the_map_configuration(self) -> None:
        """A página tem de entregar ao JavaScript os tiles, o centro e o raio máximo.

        O teste aponta para o contrato do Leaflet — os placeholders `{z}/{x}/{y}` e
        uma atribuição — e não para um fornecedor. O fornecedor é uma decisão de
        operação e muda; o que não pode mudar é o formato do que o Leaflet lê.
        """
        response = self.client.get(reverse("properties:property_list"))

        self.assertEqual(response.status_code, 200)
        config = response.context["map_config"]
        self.assertEqual(config["maxRadiusM"], MAX_SEARCH_RADIUS_M)
        for placeholder in ("{z}", "{x}", "{y}"):
            self.assertIn(placeholder, config["tileUrl"])
        self.assertTrue(config["attribution"].strip())
        self.assertIn("invertTiles", config)

    def test_catalog_filters_by_area_without_javascript(self) -> None:
        """A URL tem de filtrar por área mesmo sem HTMX, para o link poder ser partilhado."""
        response = self.client.get(reverse("properties:property_list"), self.area_query)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T3 no Kilamba com quintal")

    def test_htmx_request_filters_by_area(self) -> None:
        """O mesmo recorte aplica-se ao swap, e devolve só o fragmento."""
        response = self.client.get(
            reverse("properties:property_list"), self.area_query, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/property_results.html")
        self.assertContains(response, "T3 no Kilamba com quintal")

    def test_empty_area_returns_to_the_whole_catalog(self) -> None:
        """Um raio malformado no link não pode esvaziar a página."""
        response = self.client.get(
            reverse("properties:property_list"),
            {"center_lat": "abc", "center_lon": "13.18", "radius_m": "2000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T3 no Kilamba com quintal")

    def test_pagination_links_keep_the_area(self) -> None:
        """Mudar de página não pode deitar a área fora."""
        for index in range(13):
            make_property(curator=self.curator, owner=self.owner, title=f"T2 número {index}")

        response = self.client.get(reverse("properties:property_list"), self.area_query)

        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertContains(response, "radius_m=2000")
        self.assertContains(response, "center_lat=-8.918430")

    def test_hidden_area_inputs_carry_invariant_decimals(self) -> None:
        """Os campos ocultos têm de devolver coordenadas que o servidor consiga ler.

        A interface é `pt-AO`, e `{{ filters.center_lat }}` produzia
        `-8,918430`. Ao mexer em qualquer outro filtro, o formulário devolvia essa
        vírgula ao servidor, o `Decimal` recusava-a e a área desenhada
        desaparecia sem aviso. Pontos, sempre.
        """
        response = self.client.get(reverse("properties:property_list"), self.area_query)

        self.assertContains(response, 'name="center_lat" id="f-area-lat" value="-8.91843"')
        self.assertContains(response, 'name="center_lon" id="f-area-lon" value="13.1847"')
        self.assertContains(response, 'name="radius_m" id="f-area-raio" value="2000"')

    def test_area_survives_a_change_to_another_filter(self) -> None:
        """O utilizador que muda a província não pode perder a área sem dar por isso."""
        url = reverse("properties:property_list")
        first = self.client.get(url, self.area_query)
        self.assertEqual(first.status_code, 200)

        query = {**self.area_query, "province": "Luanda"}
        second = self.client.get(url, query)

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.context["filters"].has_area)
        self.assertEqual(second.context["filters"].radius_m, Decimal("2000"))
        self.assertContains(second, 'id="f-area-lat" value="-8.91843"')

    def test_clear_area_link_keeps_the_other_filters(self) -> None:
        """Limpar a área não pode limpar a pesquisa toda."""
        response = self.client.get(
            reverse("properties:property_list"),
            {**self.area_query, "province": "Luanda", "purpose": "rent"},
        )

        kept = parse_qs(response.context["clear_area_query"])
        self.assertEqual(kept, {"province": ["Luanda"], "purpose": ["rent"]})
        self.assertContains(response, "province=Luanda")
        self.assertContains(response, "purpose=rent")


class PropertyAreaMarkupTests(TestCase):
    """O mapa é melhoria progressiva: a página tem de se aguentar sem ele."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)

    def test_drawing_the_area_has_a_button_with_a_name(self) -> None:
        """Desenhar é a acção principal e não pode ser um ícone sem legenda.

        O botão de 30×30 que o `leaflet.draw` trazia funcionava, mas lia-se como
        um quadrado vazio. Quem não percebeu o ícone partiu do princípio de que
        o mapa estava partido, e a ferramenta estava a funcionar.
        """
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'id="area-draw"')
        self.assertContains(response, "Desenhar círculo")
        # E o botão é um `<button>` de verdade: tem de apanhar Tab e Enter.
        self.assertContains(
            response,
            '<button class="btn btn--sm btn--primary" type="button" id="area-draw">',
        )

    def test_map_explains_what_the_dots_are(self) -> None:
        """Duas cores sem legenda são duas cores a adivinhar."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'id="area-count"')
        self.assertContains(response, "area-search__dot--rent")
        self.assertContains(response, "area-search__dot--sale")
        self.assertContains(response, "Para arrendar")
        self.assertContains(response, "Para vender")

    def test_map_says_it_has_no_properties_yet(self) -> None:
        """        Um mapa vazio sem explicação parece um mapa que não carregou.

        Com catálogo, o número de pinos substitui a explicação. Sem catálogo, o
        texto tem de dizer que o problema é não haver imóveis e não o mapa.
        """
        self.prop.delete()
        response = self.client.get(reverse("properties:property_list"))


        self.assertEqual(response.context["map_payload"]["items"], [])
        self.assertContains(response, "Ainda não há imóveis publicados")
        self.assertContains(response, "localização")

    def test_map_prices_do_not_reach_javascript_unescaped(self) -> None:
        """O JSON dos pinos escapa o que a equipa escreveu no imóvel.

        O título de um imóvel é texto livre e vai parar ao popup por JavaScript.
        A `json_script` do Django é o que garante que uma aspa ou um `<` no
        título não fecham o atributo nem injectam HTML no popup.
        """
        self.prop.title = 'T3 "com" aspas & <b>negrito</b>'
        self.prop.save(update_fields=["title"])

        body = self.client.get(reverse("properties:property_list")).content.decode()

        self.assertIn("id=\"area-map-markers\"", body)
        self.assertNotIn("<b>negrito</b>", body.split('id="area-map-markers"')[1][:2000])

    def test_catalog_ships_the_vendored_map(self) -> None:
        """O mapa nunca vem de CDN: versão fixa em `static/vendor/`."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, "/static/vendor/leaflet/1.9.4/leaflet.css")
        self.assertContains(response, "/static/vendor/leaflet/1.9.4/leaflet.js")
        self.assertContains(response, "/static/vendor/leaflet-draw/1.0.4/leaflet.draw.css")
        self.assertContains(response, "/static/vendor/leaflet-draw/1.0.4/leaflet.draw.js")
        # Só o mapa é forbidido de CDN aqui; o `base.html` ainda serve o HTMX de
        # `unpkg.com`, o que é dívida com data marcada, não parte desta mudança.
        self.assertNotContains(response, "unpkg.com/leaflet")
        self.assertNotContains(response, "jsdelivr.net/npm/leaflet")

    def test_vendored_map_runs_before_our_own_javascript(self) -> None:
        """O Leaflet tem de executar antes de o `echilo.js` tentar montar o mapa.

        Com `defer`, o documento já não está a carregar quando um script diferido
        corre, por isso o `ready()` do `echilo.js` dispara na hora em vez de
        esperar pelo `DOMContentLoaded`. Se o Leaflet viesse depois no corpo,
        `window.L` ainda não existia, a secção ficava escondida e o mapa não
        aparecia — sem um único erro na consola. Já aconteceu; daí o teste.
        """
        body = self.client.get(reverse("properties:property_list")).content.decode()

        self.assertLess(
            body.index("vendor/leaflet/1.9.4/leaflet.js"),
            body.index("/static/js/echilo.js"),
        )
        self.assertLess(
            body.index("vendor/leaflet-draw/1.0.4/leaflet.draw.js"),
            body.index("/static/js/echilo.js"),
        )

    def test_htmx_is_served_by_us_and_not_by_a_cdn(self) -> None:
        """O HTMX tem de vir de `static/vendor`, não do `unpkg`.

        A proteção contra tracking do browser bloqueia o armazenamento de
        scripts de terceiros e avisa no `console`. Se o HTMX não carregar, os
        filtros e a pesquisa por área deixam de funcionar — e a página continua
        a parecer correcta.
        """
        body = self.client.get(reverse("properties:property_list")).content.decode()

        self.assertIn("/static/vendor/htmx/1.9.12/htmx.min.js", body)
        self.assertNotIn("unpkg.com", body)
        self.assertNotIn("cdn.", body)

    def test_htmx_runs_before_our_own_javascript(self) -> None:
        """O nosso código pode depender do `htmx` estar carregado."""
        body = self.client.get(reverse("properties:property_list")).content.decode()

        self.assertLess(
            body.index("/static/vendor/htmx/1.9.12/htmx.min.js"),
            body.index("/static/js/echilo.js"),
        )

    def test_the_favicon_is_declared_so_the_browser_stops_guessing(self) -> None:
        """Sem `rel="icon"`, o browser pede `/favicon.ico` e leva um 404."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'href="/static/img/favicon.svg"')

    def test_map_container_is_labelled_and_receives_its_config(self) -> None:
        """O mapa tem de ser anunciável e saber de onde se vão buscar os tiles."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'id="area-map" role="region"')
        self.assertContains(
            response, "Mapa de Angola com os imóveis publicados e a área de pesquisa"
        )
        self.assertContains(response, 'id="area-map-config" type="application/json"')
        self.assertContains(response, '"maxRadiusM": 50000')
        self.assertContains(response, 'id="area-map-markers" type="application/json"')


    def test_map_has_a_way_to_explain_a_blocked_tile(self) -> None:
        """Um fornecedor que recusa os tiles tem de dizer qualquer coisa.

        Um 403 deixava um rectângulo vazio e silencioso, e o filtro por área
        parecia estar partido quando o culpado era o fornecedor. O aviso também
        serve de âncora para quem usa leitor de ecrã.
        """
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'id="area-error" role="alert" hidden')

    @override_settings(
        ECHILO_MAP_TILE_URL="https://api.maptiler.com/maps/dark/256/{z}/{x}/{y}.png?key={key}",
        ECHILO_MAP_TILE_KEY="",
    )
    def test_unfilled_key_is_reported_instead_of_becoming_a_403(self) -> None:
        """Um `.env` em branco tem de dizer o que falta, não devolver `{key}` ao tiles.

        MapTiler, Stadia e Mapbox respondem 401 ou 403 a uma chave vazia. Sem
        este aviso, o mapa ficava vazio e a culpa parecia do código.
        """
        response = self.client.get(reverse("properties:property_list"))

        self.assertIn("configError", response.context["map_config"])
        self.assertIn("ECHILO_MAP_TILE_KEY", response.context["map_config"]["configError"])

    @override_settings(
        ECHILO_MAP_TILE_URL="https://api.maptiler.com/maps/dark/256/{z}/{x}/{y}.png?key={key}",
        ECHILO_MAP_TILE_KEY="abc123",
    )
    def test_the_key_is_substituted_into_the_tile_url(self) -> None:
        """A chave é pública e vai no URL; o resto da query fica intacto."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertEqual(
            response.context["map_config"]["tileUrl"],
            "https://api.maptiler.com/maps/dark/256/{z}/{x}/{y}.png?key=abc123",
        )
        self.assertNotIn("configError", response.context["map_config"])

    def test_map_reaches_its_inputs_through_the_search_form(self) -> None:
        """Um único pedido: o mapa escreve no formulário que o HTMX já escuta."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'hx-push-url="true"')
        self.assertContains(response, 'hx-target="#listing-resultados"')
        # A ordem importa: o mapa escreve, o formulário envia.
        body = response.content.decode()
        self.assertLess(body.index('id="f-area-lat"'), body.index('id="listing-resultados"'))

    def test_keyboard_route_is_offered_next_to_the_mouse(self) -> None:
        """Desenhar com o rato não pode ser a única forma de chegar a uma área."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertContains(response, 'for="area-raio"')
        self.assertContains(response, "Foque o mapa, desloque-o com as setas")
        self.assertContains(response, "Procurar nesta área")

    def test_only_the_catalog_pays_for_leaflet(self) -> None:
        """A ficha do imóvel não carrega uma biblioteca que não usa."""
        make_image(self.prop)
        response = self.client.get(f"/imovel/{self.prop.reference}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "leaflet")

    def test_clear_area_is_offered_only_when_an_area_exists(self) -> None:
        """Um botão que não faz nada é pior do que não existir."""
        without_area = self.client.get(reverse("properties:property_list"))
        self.assertContains(without_area, 'id="area-clear" hidden')

        with_area = self.client.get(
            reverse("properties:property_list"),
            {"center_lat": "-8.918430", "center_lon": "13.184700", "radius_m": "2000"},
        )
        self.assertContains(with_area, 'id="area-clear"')
        self.assertNotContains(with_area, 'id="area-clear" hidden')


class MapMarkerTests(TestCase):
    """O mapa é a forma de ver o catálogo, por isso tem de mostrar o catálogo."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role="CURATOR", email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)

    def test_map_marks_a_published_property_with_its_price_already_formatted(self) -> None:
        """O preço do pino é o mesmo do cartão, porque é a mesma função."""
        payload = build_map_payload(PropertyFilters())

        self.assertEqual(payload["total"], 1)
        item = payload["items"][0]
        self.assertEqual(item["preco"], "450 mil Kz")
        self.assertTrue(item["por_mes"])
        self.assertEqual(item["local"], "Kilamba")
        self.assertIn(self.prop.reference, item["url"])
        self.assertAlmostEqual(item["lat"], -8.918430, places=5)

    def test_map_only_marks_what_the_public_can_see(self) -> None:
        """Um rascunho e um imóvel sem pin não aparecem no mapa público."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            title="Rascunho que ninguem pode ver",
        )
        make_property(
            curator=self.curator,
            owner=self.owner,
            latitude=None,
            longitude=None,
            title="Sem pin verificado",
        )

        payload = build_map_payload(PropertyFilters())

        self.assertEqual(payload["total"], 1)
        self.assertEqual([item["titulo"] for item in payload["items"]], [self.prop.title])

    def test_map_says_when_it_cannot_draw_all_of_them(self) -> None:
        """Pinos a menos do que o número anunciado fazem o pino sumir na procura."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            title="Outro imóvel no mesmo mapa",
        )

        payload = build_map_payload(PropertyFilters(), limit=1)

        self.assertEqual(payload["mostrados"], 1)
        self.assertEqual(payload["total"], 2)
        self.assertTrue(payload["incompletos"])

    def test_map_keeps_the_neighbourhood_when_the_area_narrows(self) -> None:
        """O círculo aperta a lista, não o mapa.

        Se os pinos desaparecessem à medida que o raio diminuísse, desenhar um
        círculo parecia não fazer nada: o mapa esvaziava e a única mudança
        visível era o número em cima.
        """
        make_property(
            curator=self.curator,
            owner=self.owner,
            title="Imóvel a 30 km",
            latitude=Decimal("-9.300000"),
            longitude=Decimal("13.900000"),
        )
        perto = PropertyFilters(
            center_lat=Decimal("-8.918430"),
            center_lon=Decimal("13.184700"),
            radius_m=1000,
        )

        payload = build_map_payload(perto)
        dentro = PropertyQueryService.search(perto)

        self.assertEqual(len(dentro), 1)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["items"]), 2)

    def test_map_respects_the_filters_it_understands(self) -> None:
        """Um pino para "comprar" não pode aparecer quando a pesquisa é para arrendar."""
        make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000",
            title="Moradia para vender",
        )

        payload = build_map_payload(PropertyFilters(purpose=Property.Purpose.RENT))

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["titulo"], self.prop.title)
        self.assertEqual(payload["caixa"], [[-8.91843, 13.1847], [-8.91843, 13.1847]])

    def test_map_without_properties_has_no_box_to_frame(self) -> None:
        """Sem pinos não há nada para enquadrar, e a vista fica no centro configurado."""
        self.prop.delete()

        payload = build_map_payload(PropertyFilters())

        self.assertEqual(payload["items"], [])
        self.assertIsNone(payload["caixa"])

    def test_catalog_hands_the_pins_to_the_map(self) -> None:
        """A página entrega os pinos em JSON, e não os escreve no HTML à mão."""
        response = self.client.get(reverse("properties:property_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="area-map-markers"')
        self.assertIn(self.prop.reference, str(response.context["map_payload"]))

    def test_catalog_marks_the_pins_without_a_query_per_property(self) -> None:
        """Um mapa que custa uma consulta por imóvel deixa de ser um mapa.

        Cinco imóveis, cinco consultas. O `locality` entra no `.only()` dos
        pinos por causa disto: esquecê-lo faz cada pino buscá-lo à parte, e o
        número de consultas cresce com o catálogo sem dar erro nenhum.
        """
        for indice in range(4):
            make_property(
                curator=self.curator,
                owner=self.owner,
                title=f"Imóvel {indice}",
                latitude=Decimal("-8.918430") + Decimal(indice) / 1000,
            )

        with self.assertNumQueries(5):
            response = self.client.get(reverse("properties:property_list"))
            self.assertEqual(len(response.context["map_payload"]["items"]), 5)


class OwnerProfileTests(TestCase):
    """O telefone do proprietário nunca é exposto no catálogo público."""

    def test_owner_phone_is_not_reachable_from_public_template(self) -> None:
        """A ficha pública não mostra o contacto directo do proprietário."""
        curator = make_user(role="CURATOR", email="curador@echilo.ao")
        owner = make_owner(created_by=curator)
        prop = make_property(curator=curator, owner=owner)
        make_image(prop)

        response = Client().get(f"/imovel/{prop.reference}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, owner.phone)


class AdministrativeLayerTests(TestCase):
    """O mapa diz em que divisão está, e diz de onde vieram as linhas."""

    def setUp(self) -> None:
        self.response = self.client.get(reverse("properties:property_list"))

    def test_a_configuracao_aponta_para_as_camadas_versionadas(self) -> None:
        """O JavaScript nunca compõe o caminho: é o servidor que o dá.

        Um caminho escrito no `echilo.js` é um caminho que ninguém revê quando o
        ficheiro muda de sítio, e o mapa deixa de ter frontiers sem erro nenhum.
        """
        config = self.response.context["map_config"]
        self.assertEqual(
            config["provinciasUrl"],
            static("vendor/geo/angola-provincias.json"),
        )
        self.assertEqual(
            config["municipiosUrl"],
            static("vendor/geo/angola-municipios.json"),
        )
        self.assertGreaterEqual(config["zoomMunicipios"], 9)

    def test_a_pagina_tem_onde_escrever_a_divisao(self) -> None:
        self.assertContains(self.response, 'id="area-fraccao"')

    def test_a_atribuicao_da_licenca_esta_na_pagina(self) -> None:
        # A CC BY 4.0 exige atribuição visível a quem consome os dados. Este teste
        # compara com o que o ficheiro de dados declara, para que as duas coisas
        # não possam divergir sem dar erro.
        licenca = json.loads(FICHEIRO_MUNICIPIOS.read_text(encoding="utf-8"))["fonte"]
        self.assertContains(self.response, licenca["licenca"])
        self.assertContains(self.response, "geoBoundaries")

    def test_a_atribuicao_diz_que_as_linhas_foram_simplificadas(self) -> None:
        # Sem isto, um utilizador pode tomar o contorno por um limite oficial e
        # regretar o passo. A simplificação é um limite de referência, não um
        # registo cadastral, e a diferença tem de estar escrita.
        self.assertContains(self.response, "simplificadas para referência")

    def test_os_ficheiros_de_divisoas_estao_no_repositorio(self) -> None:
        """Vendorizado, sem CDN.

        Se estes ficheiros não estiverem versionados, o mapa funciona em
        desenvolvimento e parte em produção, porque em produção o `collectstatic`
        não os encontra.
        """
        for caminho in (FICHEIRO_PROVINCIAS, FICHEIRO_MUNICIPIOS):
            with self.subTest(caminho=caminho.name):
                self.assertTrue(caminho.exists(), f"{caminho} não está no repositório")
                self.assertIn("vendor", caminho.parts)

    def test_os_nomes_dos_municipios_so_aparecem_a_partir_do_zoom_certo(self) -> None:
        """157 nomes a zoom 9 não se leem: viram uma mancha sobre a fotografia.

        A classe é alternada pelo mapa. Se a regra passar a esconder os nomes sem
        a classe, o mapa fica mudo; se a deixar de fora, enche-se de letras.
        """
        folha = Path(settings.BASE_DIR) / "static" / "css" / "pages.css"
        css = folha.read_text(encoding="utf-8")
        self.assertIn(".zoom-municipios", css)
        self.assertIn("divisao-label--municipio", css)


class AdminBoundaryDataTests(SimpleTestCase):
    """A camada de divisões que o mapa desenha e nomeia.

    Os ficheiros são versionados e gerados por `build_admin_boundaries`. Estes
    testes não vão à rede: leem o que está no repositório e verificam o que o
    mapa dá como garantido. A ligação à fonte é do comando, com `--check`.

    Os testes lêem as features como o Leaflet as lê, por `properties` e
    `geometry`. Ler por uma chave à escolha era escrever o teste contra a nossa
    imaginação do ficheiro em vez de contra o que o browser consome.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.provincias = cls._le(FICHEIRO_PROVINCIAS)["features"]
        cls.municipios = cls._le(FICHEIRO_MUNICIPIOS)["features"]

    @staticmethod
    def _le(caminho: Path) -> dict:
        return json.loads(caminho.read_text(encoding="utf-8"))

    @staticmethod
    def _aneis_gravados(divisao: dict) -> list[list[list[float]]]:
        """Os anéis tal como estão no ficheiro, em `[lon, lat]`."""
        return [
            anel for poligono in divisao["geometry"]["coordinates"] for anel in poligono
        ]

    def test_os_ficheiros_sao_feature_collections_de_geojson(self) -> None:
        """O formato decide se o mapa desenha alguma coisa.

        `L.geoJSON` só aceita `FeatureCollection`. Um ficheiro com as divisões
        numa chave própria — `provincias`, `municipios` — é um pacote que o
        Leaflet abre e não encontra, e o mapa ficava sem nomes sem dar erro.
        """
        for caminho in (FICHEIRO_PROVINCIAS, FICHEIRO_MUNICIPIOS):
            with self.subTest(caminho=caminho.name):
                documento = self._le(caminho)
                self.assertEqual(documento["type"], "FeatureCollection")
                for feature in documento["features"]:
                    self.assertEqual(feature["type"], "Feature")
                    self.assertEqual(feature["geometry"]["type"], "MultiPolygon")
                    self.assertIn("nome", feature["properties"])

    def test_as_coordenadas_sao_lista_de_poligonos(self) -> None:
        """`MultiPolygon` é polígonos, cada um com os seus anéis.

        Uma lista plana de anéis é a forma que parece certa e não é: o Leaflet
        lê o primeiro elemento como um anel de pontos e recebe números onde
        esperava coordenadas, e o mapa fica sem Divisions sem dar erro. A
        simplificação e a troca de eixos acontecem no mesmo passo, e um anel
        perdido aqui dentro é um município que desaparece.
        """
        for caminho in (FICHEIRO_PROVINCIAS, FICHEIRO_MUNICIPIOS):
            for divisao in self._le(caminho)["features"]:
                with self.subTest(divisao=divisao["properties"]["nome"]):
                    self.assertGreaterEqual(len(divisao["geometry"]["coordinates"]), 1)
                    for poligono in divisao["geometry"]["coordinates"]:
                        self.assertGreaterEqual(len(poligono), 1)
                        for anel in poligono:
                            self.assertGreaterEqual(len(anel), 4)
                            for ponto in anel:
                                self.assertIsInstance(ponto[0], (int, float))
                                self.assertIsInstance(ponto[1], (int, float))

    def test_toda_divisao_desenhada_e_uma_provincia_do_projecto(self) -> None:
        """O mapa não desenha nenhuma divisão que os filtros não ofereçam.

        Um código no ficheiro que não esteja na lista seria um nome que o
        cliente nunca vê e que o `divisoesDe` não sabe traduzir.
        """
        codigos = {p["properties"]["codigo"] for p in self.provincias}
        do_projecto = {codigo for codigo, _ in ANGOLA_PROVINCES}
        self.assertEqual(codigos - do_projecto, set())

    def test_as_provincias_sem_contorno_sao_as_que_a_fonte_nao_conhece(self) -> None:
        """Nem toda a lista do projecto é desenhada, e a diferença é declarada.

        A lista tem vinte e uma entradas e a fonte dezoito divisões. A
        divergência é um facto, não um erro, e escrevê-la num sítio só é o que
        impede o número de aparecer em código.
        """
        desenhados = {p["properties"]["codigo"] for p in self.provincias}
        self.assertEqual(
            set(provinces_without_boundary()),
            {"Icolo e Bengo", "Moxico Leste"},
        )
        self.assertNotIn("ICOLO_E_BENGO", desenhados)
        self.assertNotIn("MOXICO_LESTE", desenhados)

    def test_quando_e_cubango_partilham_um_contorno_so(self) -> None:
        """Duas entradas da lista, uma divisão desenhada, e o nome que a fonte dá.

        A fonte conhece "Cuando Cubango" como uma divisão. Desenhá-la duas vezes
        era duplicar geometria e dar ao mapa duas linhas sobrepostas; deixá-la de
        fora punia quem filtra por qualquer das duas. Fica uma, com o nome da
        fonte, que é o que o mapa diz e o que a fonte sustenta.
        """
        desenhados = {p["properties"]["codigo"] for p in self.provincias}
        self.assertIn("CUANDO", desenhados)
        self.assertNotIn("CUBANGO", desenhados)
        nomes = {
            p["properties"]["nome"]
            for p in self.provincias
            if p["properties"]["codigo"] == "CUANDO"
        }
        self.assertEqual(nomes, {"Cuando Cubango"})

    def test_cada_municipio_pertenece_a_uma_provincia_conhecida(self) -> None:
        codigos = {p["properties"]["codigo"] for p in self.provincias}
        for municipio in self.municipios:
            with self.subTest(municipio=municipio["properties"]["nome"]):
                self.assertIn(municipio["properties"]["provincia"], codigos)

    def test_nenhuma_provincia_fica_sem_municipios(self) -> None:
        com_municipios = {m["properties"]["provincia"] for m in self.municipios}
        for provincia in self.provincias:
            with self.subTest(provincia=provincia["properties"]["codigo"]):
                self.assertIn(provincia["properties"]["codigo"], com_municipios)

    def test_a_geometria_gravada_esta_na_ordem_do_geojson(self) -> None:
        """`[lon, lat]` no ficheiro, como a RFC 7946 manda e o `L.geoJSON` lê.

        O comando raciocina em `[lat, lon]`, que é a ordem de `centro()`, de
        `dentro()` e a que `L.circleMarker` recebe. Se essa ordem chegar ao
        ficheiro, o Leaflet lê a latitude como longitude e a longitude como
        latitude: os contornos aparecem no Atlântico, a oeste de Angola, e o
        browser não dá erro nenhum. A bbox é a mesma generosa de propósito: o
        objectivo é apanhar uma divisão projectada para o lado errado do globo.
        """
        for divisao in (*self.provincias, *self.municipios):
            for anel in self._aneis_gravados(divisao):
                for lon, lat in anel:
                    with self.subTest(divisao=divisao["properties"]["nome"]):
                        self.assertTrue(11.0 <= lon <= 24.5, f"lon {lon}")
                        self.assertTrue(-19.0 <= lat <= -4.0, f"lat {lat}")

    def test_tudo_o_que_o_mapa_desenha_fica_dentro_de_angola(self) -> None:
        """Os contornos que o comando calculou, já na ordem em que ele pensa."""
        for divisao in (*self.provincias, *self.municipios):
            for anel in aneis_de(divisao):
                for lat, lon in anel:
                    with self.subTest(divisao=divisao["properties"]["nome"]):
                        self.assertTrue(-19.0 <= lat <= -4.0, f"lat {lat}")
                        self.assertTrue(11.0 <= lon <= 24.5, f"lon {lon}")

    def test_cada_provincia_esta_onde_o_seu_ponto_diz(self) -> None:
        """O ponto do rótulo cai na sua província, e em mais nenhuma.

        É o teste que apanha os eixos trocados. Com `[lat, lon]` no ficheiro o
        `L.geoJSON` desenhava cada província no Atlântico, e o ponto do rótulo —
        esse sim na ordem certa — ficava a salvo no mar: nenhuma província
        continha o seu próprio ponto. Um ficheiro desenhado no sítio errado e um
        ficheiro com a forma certa davam o mesmo número de paths no DOM.
        """
        for provincia in self.provincias:
            ponto = provincia["properties"]["ponto"]
            onde = [
                p["properties"]["nome"]
                for p in self.provincias
                if dentro(ponto, aneis_de(p))
            ]
            with self.subTest(provincia=provincia["properties"]["nome"]):
                self.assertEqual(onde, [provincia["properties"]["nome"]])

    def test_o_ponto_do_rotulo_continua_na_ordem_do_leaflet(self) -> None:
        """`ponto` é propriedade nossa, não geometria GeoJSON.

        Vai directo para `L.circleMarker`, que quer `[lat, lon]`. Um ficheiro bem
        formado não tem de ser uniforme por dentro: com `ponto` em ordem GeoJSON
        os nomes iam para o oceano enquanto os contornos apareciam no sítio
        certo, e nenhum dos dois erros se denunciava.
        """
        for divisao in (*self.provincias, *self.municipios):
            lat, lon = divisao["properties"]["ponto"]
            with self.subTest(divisao=divisao["properties"]["nome"]):
                self.assertTrue(-19.0 <= lat <= -4.0, f"lat {lat}")
                self.assertTrue(11.0 <= lon <= 24.5, f"lon {lon}")

    def test_todo_o_contorno_fecha_uma_area(self) -> None:
        for divisao in (*self.provincias, *self.municipios):
            for anel in aneis_de(divisao):
                with self.subTest(divisao=divisao["properties"]["nome"]):
                    self.assertGreaterEqual(len(anel), 4)
                    self.assertEqual(anel[0], anel[-1])

    def test_o_ponto_do_rotulo_cae_dentro_do_proprio_contorno(self) -> None:
        # O rótulo escreve-se sozinho, sem o utilizador pedir. Se o ponto cair
        # fora da divisão, o nome aparece no sítio ao lado e passa a ser errado.
        # Vale para as duas camadas: as províncias também escrevem o nome, e com
        # um contorno provincial recortado como o de Luanda a média dos vértices
        # cai no mar.
        for divisao in (*self.provincias, *self.municipios):
            with self.subTest(divisao=divisao["properties"]["nome"]):
                self.assertTrue(dentro(divisao["properties"]["ponto"], aneis_de(divisao)))

    def test_a_licenca_da_fonte_via_no_ficheiro(self) -> None:
        # A CC BY 4.0 exige atribuição, e uma atribuição escondida num README
        # não cumpre a licença. O mapa escreve isto no painel.
        for caminho in (FICHEIRO_PROVINCIAS, FICHEIRO_MUNICIPIOS):
            with self.subTest(caminho=caminho.name):
                fonte = self._le(caminho)["fonte"]
                self.assertEqual(fonte["licenca"], "CC BY 4.0")
                self.assertTrue(fonte["url"])
                self.assertTrue(fonte["nome"])

    def test_a_camada_nao_pesa_mais_do_que_o_justo(self) -> None:
        # As províncias vão no primeiro carregamento; os municípios, só quando
        # o utilizador se aproxima. Um ficheiro que cresce sem limite paga-se na
        # primeira pintura, e o teste é a rede de segurança dessa promessa.
        self.assertLess(FICHEIRO_PROVINCIAS.stat().st_size, 150_000)
        self.assertLess(FICHEIRO_MUNICIPIOS.stat().st_size, 900_000)

    def test_o_mapa_escreve_o_nome_que_os_filtros_usam(self) -> None:
        """A fonte e o projecto discordam da ortografia; o projecto manda.

        `Kuito`, `Baía Farta`, `Nharea` e `Amboim` chegam da fonte como `Cuito`,
        `Baia Farta`, `N'harea` e `Amboim (Gabela)`. Um rótulo com o nome da
        fonte ao lado de um filtro com o nome do projecto obriga o utilizador a
        saber que são a mesma coisa.
        """
        nomes = {m["properties"]["nome"] for m in self.municipios}
        for esperado in ("Kuito", "Baía Farta", "Nharea", "Amboim", "Xá-Muteba"):
            with self.subTest(municipio=esperado):
                self.assertIn(esperado, nomes)

    def test_o_mapa_nao_escreve_o_nome_que_a_fonte_da(self) -> None:
        """`Amboim (Gabela)` é o mesmo município, escrito de outra maneira."""
        nomes = {m["properties"]["nome"] for m in self.municipios}
        self.assertNotIn("Amboim (Gabela)", nomes)

    def test_nenhum_roteiro_da_fonte_atinge_o_mapa(self) -> None:
        for divisao in (*self.provincias, *self.municipios):
            with self.subTest(divisao=divisao["properties"]["nome"]):
                self.assertNotRegex(divisao["properties"]["nome"], r"[<>]")


class AdminBoundarySimplificationTests(SimpleTestCase):
    """A simplificação é um tecto de desvio, não uma licença para apagar."""

    def test_um_anel_menor_que_a_tolerancia_nao_desaparece(self) -> None:
        # Uma cintura de 30 m, muito abaixo da tolerância de 223 m. Se a
        # Douglas-Peucker a apagar, o município some do mapa sem aviso.
        cintura = {
            "type": "Polygon",
            "coordinates": [
                [[13.0, 13.0], [13.0003, 13.0], [13.0, 13.0003], [13.0, 13.0]]
            ],
        }
        self.assertTrue(limites(cintura, TOLERANCIA))

    def test_um_anel_grande_perde_vertices_mas_mantem_a_area(self) -> None:
        circulo = [
            [
                [
                    13.0 + 0.1 * math.cos(math.radians(graus)),
                    13.0 + 0.1 * math.sin(math.radians(graus)),
                ]
                for graus in range(0, 360, 2)
            ]
        ]
        reduzido = limites({"type": "Polygon", "coordinates": circulo}, TOLERANCIA)
        self.assertLess(len(reduzido[0]), 200)
        self.assertTrue(dentro([13.0, 13.0], reduzido))
        self.assertFalse(dentro([14.0, 13.0], reduzido))

    def test_um_anel_aberto_e_inutil_nao_e_gravado(self) -> None:
        degenerado = {"type": "Polygon", "coordinates": [[[13.0, 13.0]]]}
        self.assertEqual(limites(degenerado, TOLERANCIA), [])

    def test_a_versao_sem_acentos_casa_nomes_que_o_utilizador_escreve(self) -> None:
        self.assertEqual(sem_acentos("Huíla"), "HUILA")
        self.assertEqual(chave("Baía Farta"), chave("BAIA FARTA"))
        self.assertEqual(chave("Bula-Atumba"), chave("Bula Atumba"))
        self.assertEqual(chave("N'harea"), chave("Nharea"))

    def test_um_nome_com_nome_antigo_entre_parenteses_tem_duas_formas(self) -> None:
        self.assertEqual(
            variantes("Amboim (Gabela)"),
            {"Amboim (Gabela)", "Amboim", "Gabela"},
        )
        self.assertEqual(
            variantes("Sumbe (Ngangula)"),
            {"Sumbe (Ngangula)", "Sumbe", "Ngangula"},
        )
        self.assertEqual(variantes("Benguela"), {"Benguela"})

    def test_um_nome_composto_tambem_tem_a_forma_curta(self) -> None:
        self.assertIn("Dembos", variantes("Dembos-Quibaxe"))
