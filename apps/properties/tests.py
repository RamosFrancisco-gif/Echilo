"""Testes das regras do ciclo de vida do imóvel (§2.8) e da documentação (§2.7)."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.templatetags.static import static
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.geo import haversine_metres
from apps.core.testing import (
    jpeg_bytes,
    make_image,
    make_owner,
    make_property,
    make_submission,
    make_user,
    make_verified_documents,
)
from apps.core.validators import MAX_SEARCH_RADIUS_M
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
from apps.properties.models import Property, PropertyStatusEvent
from apps.properties.reference import ANGOLA_PROVINCES
from apps.properties.selectors import PropertyFilters, PropertyQueryService
from apps.properties.services import build_map_payload


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

        self.client.post(self.transition_url, {"to_status": Property.Status.PUBLISHED, "reason": "  "})

        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.UNDER_VALIDATION)

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

    def test_image_upload_becomes_the_cover(self) -> None:
        """A primeira fotografia carregada é a capa (§2.1)."""
        self.client.force_login(self.curator)

        response = self.client.post(
            self.images_url,
            {"image": SimpleUploadedFile("capa.jpg", jpeg_bytes(), content_type="image/jpeg")},
        )

        self.assertEqual(response.status_code, 302)
        image = self.prop.images.get()
        self.assertEqual(image.sort_order, 0)

    def test_second_upload_keeps_the_cover(self) -> None:
        """Uma segunda fotografia não rouba a capa."""
        self.client.force_login(self.curator)
        self.client.post(
            self.images_url,
            {"image": SimpleUploadedFile("capa.jpg", jpeg_bytes(), content_type="image/jpeg")},
        )
        self.client.post(
            self.images_url,
            {"image": SimpleUploadedFile("segunda.jpg", jpeg_bytes(), content_type="image/jpeg")},
        )

        images = list(self.prop.images.order_by("sort_order"))
        self.assertEqual([image.sort_order for image in images], [0, 1])

    def test_upload_refuses_a_non_image(self) -> None:
        """Um ficheiro que não é imagem não entra na galeria."""
        self.client.force_login(self.curator)

        self.client.post(
            self.images_url,
            {"image": SimpleUploadedFile("nota.pdf", b"%PDF-1.4", content_type="application/pdf")},
        )

        self.assertEqual(self.prop.images.count(), 0)

    def test_upload_is_capped_at_thirty_photos(self) -> None:
        """Acima de trinta fotografias o formulário recusa (§2.1)."""
        self.client.force_login(self.curator)
        for index in range(30):
            make_image(self.prop, caption=f"Foto {index}")

        self.client.post(
            self.images_url,
            {"image": SimpleUploadedFile("extra.jpg", jpeg_bytes(), content_type="image/jpeg")},
        )

        self.assertEqual(self.prop.images.count(), 30)


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

        self.assertEqual(filters.chip_query(purpose="RENT", type=""), "province=LUANDA&municipality=Kilamba&purpose=RENT")
        self.assertEqual(filters.chip_query(purpose="", type="LAND"), "province=LUANDA&municipality=Kilamba&type=LAND")
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
        self.assertContains(response, '<button class="btn btn--sm btn--primary" type="button" id="area-draw">')

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

    def test_as_dezoito_provincias_sao_as_do_projecto(self) -> None:
        codigos = {p["properties"]["codigo"] for p in self.provincias}
        self.assertEqual(codigos, {codigo for codigo, _ in ANGOLA_PROVINCES})

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

    def test_tudo_o_que_o_mapa_desenha_fica_dentro_de_angola(self) -> None:
        # A bbox é generosa de propósito: o objectivo é apanhar uma divisão
        # projectada para o lado errado do globo, que o browser desenhava no
        # oceano sem dizer nada.
        for divisao in (*self.provincias, *self.municipios):
            for anel in aneis_de(divisao):
                for lat, lon in anel:
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
        for divisao in self.municipios:
            with self.subTest(municipio=divisao["properties"]["nome"]):
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

        `Kuito`, `Baía Farta`, `Nharea` e `Gabela` chegam da fonte como `Cuito`,
        `Baia Farta`, `N'harea` e `Amboim (Gabela)`. Um rótulo com o nome da
        fonte ao lado de um filtro com o nome do projecto obriga o utilizador a
        saber que são a mesma coisa.
        """
        nomes = {m["properties"]["nome"] for m in self.municipios}
        for esperado in ("Kuito", "Baía Farta", "Nharea", "Gabela", "Xá-Muteba"):
            with self.subTest(municipio=esperado):
                self.assertIn(esperado, nomes)

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
