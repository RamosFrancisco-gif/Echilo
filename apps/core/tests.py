"""Smoke test das rotas públicas e internas, com catálogo semeado."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from importlib import import_module
from math import asin, cos, degrees, radians, sin
from pathlib import Path

from django import forms
from django.apps import apps
from apps.core.pagination import PAGINA_PADRAO, pagina_de
from apps.core.templatetags.echilo_format import distance, kwanza, kwanza_compact
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import HttpRequest
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template import Context, Template
from django.template.loader import render_to_string
from django.views.generic import ListView
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.concierge.models import Conversation, Lead, Offer, VisitRequest
from apps.core import navigation
from apps.core.forms import BaseStyledForm
from apps.core.geo import (
    EARTH_RADIUS_M,
    circle_bounding_box,
    haversine_metres,
)
from apps.core.validators import (
    LIMITE_PEDIDO_MB,
    LIMITE_UPLOAD_MB,
    MARGEM_PEDIDO_MB,
    MAX_SEARCH_RADIUS_M,
    MIN_SEARCH_RADIUS_M,
    age_in_years,
    normalize_nif,
    to_decimal_or_none,
    validate_adult,
    validate_center,
    validate_latitude,
    validate_nif,
    validate_search_radius_m,
)
from apps.core.management.commands.verify_map_tiles import _tile_xy
from apps.core.testing import (
    make_image,
    make_owner,
    make_property,
    make_user,
    make_verified_documents,
    select_options,
)

from apps.properties.models import Property
from apps.properties.reference import ANGOLA_PROVINCES


class RouteSmokeTests(TestCase):
    """Garante que cada rota renderiza e devolve o estado HTTP certo."""

    @classmethod
    def setUpTestData(cls) -> None:
        cache.clear()
        cls.curator = make_user(role=User.Role.CURATOR, email="curador@echilo.ao")
        cls.owner = make_owner(created_by=cls.curator)
        cls.rent = make_property(
            curator=cls.curator, owner=cls.owner, title="T3 no Kilamba com quintal"
        )
        cls.sale = make_property(
            curator=cls.curator,
            owner=cls.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            type=Property.Type.HOUSE,
            title="Moradia com piscina em Talatona",
        )
        for prop in (cls.rent, cls.sale):
            for index in range(5):
                make_image(prop, caption=f"Foto {index}")
            make_verified_documents(prop)
        cls.client_user = make_user(email="cliente@echilo.ao")

    def setUp(self) -> None:
        self.anonymous = Client()

    def test_home_renders_with_featured_properties(self) -> None:
        """A home usa `index.html` e mostra imóveis publicados."""
        response = self.anonymous.get(reverse("properties:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "index.html")
        self.assertContains(response, "T3 no Kilamba com quintal")
        self.assertContains(response, "Moradia com piscina em Talatona")

    def test_property_list_renders(self) -> None:
        """O catálogo público abre sem autenticação."""
        response = self.anonymous.get(reverse("properties:property_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T3 no Kilamba com quintal")

    def _fotos_do_carrocel(self, html: str) -> list[str]:
        """As tags das imagens que rodam, e nada mais.

        Contar `card-property__foto` na pagina inteira nao funciona: o contentor
        da pilha e `card-property__fotos` e comeca pela mesma palavra, e contar
        `src=` na pagina apanha o logotipo e o heroi. Ficar pelas tags resolve
        as duas coisas.
        """
        return re.findall(r'<img[^>]*class="card-property__foto[^"]*"[^>]*>', html)

    def test_a_home_rota_as_fotografias_dos_imoveis_publicados(self) -> None:
        """Os imoveis publicados na home mostram todas as fotografias, nao so a capa."""
        response = self.anonymous.get(reverse("properties:home"))

        html = response.content.decode()
        # Cinco fotografias por imovel, em dois imoveis.
        self.assertEqual(len(self._fotos_do_carrocel(html)), 10)
        self.assertEqual(html.count('class="card-property__fotos"'), 2)
        self.assertIn("data-intervalo=", html)

    def test_a_home_nao_pede_todas_as_fotografias_de_cima(self) -> None:
        """Só a capa é pedida ao servidor; as restantes esperam pela vez delas.

        Seis cartões com quinze fotografias cada são noventa imagens. Com `src`
        nas noventa, a página pede noventa ficheiros de uma vez, e o primeiro
        ecrã aparece quando a última fotografia chega. `data-src` adia o pedido
        até a imagem estar a ser mostrada.

        O `src` não pode ser contado com `\\bsrc=`: o hífen não é caractere de
        palavra, e `\\bsrc=` casa dentro de `data-src=`.
        """
        response = self.anonymous.get(reverse("properties:home"))

        fotos = self._fotos_do_carrocel(response.content.decode())
        pedidos = [foto for foto in fotos if re.search(r'(?<![\w-])src="', foto)]
        adiadas = [foto for foto in fotos if 'data-src="' in foto]
        # Dois imóveis, dois pedidos imediatos: uma capa por imóvel.
        self.assertEqual(len(pedidos), 2)
        self.assertEqual(len(adiadas), 8)

    def test_a_fotografia_que_esta_a_vez_e_a_unica_que_o_leitor_de_ecra_o_le(self) -> None:
        """As fotografias que não estão à vista ficam fora da leitura.

        Quinze fotografias com o mesmo texto alternativo são quinze repetições
        do título do imóvel para quem navega por leitor de ecrã. Só a activa é
        exposta, e o JavaScript vai trocando isso com a imagem.
        """
        response = self.anonymous.get(reverse("properties:home"))

        fotos = self._fotos_do_carrocel(response.content.decode())
        expostas = [foto for foto in fotos if 'aria-hidden="true"' not in foto]
        escondidas = [foto for foto in fotos if 'aria-hidden="true"' in foto]
        # Duas expostas: uma capa por imóvel, que é o que a pessoa lê.
        self.assertEqual(len(expostas), 2)
        self.assertEqual(len(escondidas), 8)
        for foto in expostas:
            self.assertIn("card-property__foto is-ativa", foto)

    def test_o_botao_de_paragem_das_fotos_so_existe_se_houver_javascript(self) -> None:
        """O botão vem escondido no HTML e é o JavaScript que o mostra.

        Um botão de pausa que não faz nada sem JavaScript lê-se como uma falha
        da página, e é pior do que não ter botão nenhum.
        """
        response = self.anonymous.get(reverse("properties:home"))

        html = response.content.decode()
        self.assertRegex(html, r'<button[^>]*id="pausa-fotos"[^>]*hidden')

    def test_o_catalogo_nao_roda_as_fotografias(self) -> None:
        """A rotação é da página inicial, e o catálogo lista imóveis para comparar.

        No catálogo a pessoa está a escolher entre doze, e uma fotografia que
        muda a cada quarenta segundos por baixo do texto que está a ler é o
        oposto de ajudar.
        """
        response = self.anonymous.get(reverse("properties:property_list"))

        self.assertNotContains(response, "data-fotos")

    def test_a_home_traz_as_fotografias_de_todos_os_imoveis_numa_consulta(self) -> None:
        """A página inteira lê as imagens de todos os imóveis com uma consulta.

        Com o `prefetch_related` a contar as fotografias, a home fazia uma
        consulta por imóvel. O sintoma não é a lentidão com seis imóveis: é que
        o número cresce com o catálogo, e a página fica mais lenta sem que
        nenhum teste diga que algo mudou.
        """
        for prop in (self.rent, self.sale):
            for extra in range(10):
                make_image(prop, caption=f"Extra {extra}")

        with CaptureQueriesContext(connection) as contexto:
            response = self.anonymous.get(reverse("properties:home"))

        self.assertEqual(response.status_code, 200)
        # Filtrar por "propertyimage" em geral não chega: a query dos imóveis
        # traz `COUNT(properties_propertyimage.id)` do `featured` e contava
        # como se fosse uma segunda leitura das imagens.
        consultas_de_imagens = [
            consulta
            for consulta in contexto.captured_queries
            if "FROM `properties_propertyimage`" in consulta["sql"]
        ]
        self.assertEqual(
            len(consultas_de_imagens),
            1,
            "esperava uma consulta às fotografias, houve "
            f"{len(consultas_de_imagens)}",
        )

    def test_a_home_oferece_as_vinte_e_uma_provincias(self) -> None:
        """A home e o catálogo são a mesma pesquisa, e oferecem o mesmo.

        A home listava só as províncias com imóveis publicados, e quem entrava
        pela porta da frente via uma lista mais curta do que a de quem chegava
        pelo catálogo. Uma província que o produto não menciona é uma província
        que não existe para quem lê; uma província sem imóveis dá um resultado
        vazio, que é honesto.
        """
        response = self.anonymous.get(reverse("properties:home"))

        opcoes = select_options(response.content.decode(), "search-province")
        self.assertEqual(
            opcoes, [""] + [codigo for codigo, _ in ANGOLA_PROVINCES]
        )


    def test_property_list_htmx_returns_only_the_results(self) -> None:
        """O pedido HTMX devolve apenas o fragmento da listagem."""
        response = self.anonymous.get(reverse("properties:property_list"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/property_results.html")
        self.assertNotContains(response, "<!DOCTYPE html>")

    def test_property_list_filters_by_province(self) -> None:
        """Um filtro sem resultados devolve a listagem vazia, sem erro."""
        response = self.anonymous.get(
            reverse("properties:property_list"), {"province": "CABINDA"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T3 no Kilamba com quintal", count=0)

    def test_property_list_filters_by_type(self) -> None:
        """O filtro de tipologia é operável, não um controlo morto."""
        response = self.anonymous.get(
            reverse("properties:property_list"), {"type": Property.Type.LAND}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "T3 no Kilamba com quintal", count=0)
        self.assertContains(response, "Moradia com piscina em Talatona", count=0)

    def test_type_filter_is_submitted_by_the_form(self) -> None:
        """A select de tipologia tem de ter nome e valor utilizáveis."""
        response = self.anonymous.get(reverse("properties:property_list"))

        self.assertContains(response, 'name="type"')
        self.assertNotContains(response, "disabled")

    def test_chips_keep_the_other_filters(self) -> None:
        """Trocar de separador não apaga a província escolhida."""
        response = self.anonymous.get(
            reverse("properties:property_list"), {"province": "LUANDA"}
        )

        body = response.content.decode()
        self.assertIn("purpose=RENT", body)
        self.assertIn("province=LUANDA", body)

    def test_property_detail_renders(self) -> None:
        """A ficha pública mostra o imóvel e o preço em Kwanza."""
        response = self.anonymous.get(self.rent.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "450.000")

    def test_owner_contact_is_never_public(self) -> None:
        """§2.1: o telefone do proprietário não aparece no público."""
        response = self.anonymous.get(self.rent.get_absolute_url())

        self.assertNotContains(response, self.owner.phone)

    def test_owner_intake_renders(self) -> None:
        """O formulário público de adesão abre."""
        response = self.anonymous.get(reverse("concierge:owner_intake"))

        self.assertEqual(response.status_code, 200)

    def test_assistant_window_renders(self) -> None:
        """O widget do assistente abre para visitantes."""
        response = self.anonymous.get(reverse("assistant:chat"))

        self.assertEqual(response.status_code, 200)

    def test_assistant_health_endpoint_is_public(self) -> None:
        """O diagnóstico do assistente responde JSON."""
        response = self.anonymous.get(reverse("assistant:health"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("enabled", response.json())

    def test_login_page_renders(self) -> None:
        """A página de login abre."""
        response = self.anonymous.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form", html=False)

    def test_registration_page_renders(self) -> None:
        """O registo público abre e não exige conta."""
        response = self.anonymous.get(reverse("accounts:register"))

        self.assertEqual(response.status_code, 200)

    def test_password_reset_pages_render(self) -> None:
        """As três etapas da recuperação abrem."""
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            with self.subTest(page=name):
                response = self.anonymous.get(reverse(f"accounts:{name}"))
                self.assertEqual(response.status_code, 200)

    def test_logout_redirects_to_login_for_anonymous(self) -> None:
        """Sair sem sessão não expõe nada."""
        response = self.anonymous.get(reverse("accounts:logout"))

        self.assertIn(response.status_code, (302, 405))

    def test_404_uses_the_project_template(self) -> None:
        """Uma referência inexistente devolve a página de erro do projecto."""
        response = self.anonymous.get("/imovel/ECH-NAO-EXISTE/")

        self.assertEqual(response.status_code, 404)

    def test_internal_curation_routes_require_team(self) -> None:
        """As rotas de curadoria e a fila de contactos não são públicas."""
        self.anonymous.force_login(self.client_user)

        create = self.anonymous.get(reverse("properties:curator_create"))
        queue = self.anonymous.get(reverse("concierge:lead_queue"))
        detail = self.anonymous.get(
            reverse("properties:curator_detail", args=[self.rent.reference])
        )

        self.assertEqual(create.status_code, 403)
        self.assertEqual(queue.status_code, 403)
        self.assertEqual(detail.status_code, 403)

    @override_settings(MANAGER_EMAILS=["gestor@echilo.ao"])
    def test_admin_index_renders_for_manager(self) -> None:
        """O painel interno abre para quem está em MANAGER_EMAILS."""
        manager = make_user(email="gestor@echilo.ao")
        self.anonymous.force_login(manager)

        response = self.anonymous.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imóveis")

    def test_visit_and_offer_pages_render_for_client(self) -> None:
        """Cliente com conta vê o formulário de visita e o de proposta."""
        self.anonymous.force_login(self.client_user)

        visit = self.anonymous.get(reverse("concierge:visit_request", args=[self.rent.reference]))
        offer = self.anonymous.get(reverse("concierge:offer_create", args=[self.sale.reference]))

        self.assertEqual(visit.status_code, 200)
        self.assertEqual(offer.status_code, 200)

    def test_curator_detail_renders_for_team(self) -> None:
        """A ficha interna de curadoria abre para a equipa."""
        self.anonymous.force_login(self.curator)

        response = self.anonymous.get(
            reverse("properties:curator_detail", args=[self.rent.reference])
        )

        self.assertEqual(response.status_code, 200)

    def test_design_system_assets_exist(self) -> None:
        """As folhas de estilo do projecto são encontradas pelo collectstatic."""
        for asset in ("css/echilo.css", "css/pages.css"):
            with self.subTest(asset=asset):
                self.assertTrue(finders.find(asset), f"{asset} não foi encontrado")


class NavigationByProfileTests(TestCase):
    """O menu é uma resposta ao perfil, e a página tem de concordar com ele (§3).

    A navegação e a guarda das vistas são coisas diferentes: o menu diz o que
    existe, a vista decide o que abre. Um teste que só olhasse para o menu
    passaria com um menu correcto em cima de vistas abertas a toda a gente, que
    é exactamente o defeito que a equipa descreveu.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cache.clear()
        cls.admin = make_user(role=User.Role.ADMIN, email="admin@echilo.ao")
        cls.agent = make_user(role=User.Role.AGENT, email="agente@echilo.ao")
        cls.curator = make_user(role=User.Role.CURATOR, email="curador@echilo.ao")
        cls.cliente = make_user(email="cliente@echilo.ao")
        cls.owner = make_owner(created_by=cls.curator)
        cls.prop = make_property(curator=cls.curator, owner=cls.owner)

    def _menu(self, user: object) -> list[dict]:
        """Devolve o menu do utilizador já desenhado para o template."""
        return navigation.para_template(user, nome_atual="home")

    def test_o_visitante_so_ve_a_navegacao_publica(self) -> None:
        """Sem conta não há Curadoria nem contas, e a lista pública está inteira."""
        rotulos = [entrada["label"] for entrada in self._menu(None)]

        self.assertIn("Arrendar", rotulos)
        self.assertNotIn("Curadoria", rotulos)
        self.assertNotIn("Equipa e clientes", rotulos)

    def test_o_cliente_registado_nao_ve_nada_de_interior(self) -> None:
        """Ter conta não é ter acesso: um `CLIENT` vê o mesmo menu do visitante."""
        rotulos = [entrada["label"] for entrada in self._menu(self.cliente)]

        self.assertNotIn("Curadoria", rotulos)
        self.assertNotIn("Equipa e clientes", rotulos)

    def test_o_curador_ve_a_curadoria_com_imoveis_e_formulario(self) -> None:
        """O ramo de curadoria tem as duas entradas que o perfil permite."""
        ramos = {e["label"]: e for e in self._menu(self.curator) if e.get("is_group")}

        self.assertIn("Curadoria", ramos)
        self.assertEqual(
            [folha["label"] for folha in ramos["Curadoria"]["links"]],
            ["Imóveis", "Novo imóvel"],
        )

    def test_o_agente_ve_a_curadoria_mas_nao_as_contas(self) -> None:
        """Validar documentos não dá o direito de criar contas."""
        ramos = {e["label"] for e in self._menu(self.agent) if e.get("is_group")}

        self.assertIn("Curadoria", ramos)
        self.assertNotIn("Equipa e clientes", ramos)

    def test_o_administrador_ve_a_curadoria_e_as_contas(self) -> None:
        """`ADMIN` é o único perfil com os dois ramos."""
        ramos = {e["label"] for e in self._menu(self.admin) if e.get("is_group")}

        self.assertEqual(ramos, {"Curadoria", "Equipa e clientes"})

    def test_a_folha_marcada_diz_onde_esta_a_pessoa(self) -> None:
        """Só uma entrada por página pode dizer "está aqui"."""
        menu = navigation.para_template(self.admin, nome_atual="client_list")
        actuais = [
            folha["label"]
            for entrada in menu
            for folha in (entrada["links"] if entrada.get("is_group") else [entrada])
            if folha["is_current"]
        ]

        self.assertEqual(actuais, ["Clientes"])

    def test_o_ramo_da_curadoria_acende_na_página_da_curadoria(self) -> None:
        """Estar dentro da curadoria acende o ramo, e não só a folha."""
        menu = navigation.para_template(self.curator, nome_atual="curator_detail")
        ramo = next(e for e in menu if e.get("is_group") and e["label"] == "Curadoria")

        self.assertTrue(ramo["is_current"])

    def test_o_menu_desenha_o_submenu_como_ramo_abrir_e_fechar(self) -> None:
        """O ramo é um `details`, para o teclado e o `aria-expanded` serem do browser.

        Um botão com JavaScript que não chegou a ser escrito é um ramo que não
        abre, e ninguém descobre se o erro é o menu ou a página.
        """
        self.client.force_login(self.curator)

        html = self.client.get(reverse("properties:home")).content.decode()

        self.assertIn('class="nav__group"', html)
        self.assertIn("<summary", html)
        self.assertIn("Curadoria", html)
        self.assertIn(reverse("properties:curator_dashboard"), html)

    def test_o_cliente_nao_ve_o_ramo_da_curadoria_no_html(self) -> None:
        """A ausência também se mede: o ramo não pode estar só escondido por CSS."""
        self.client.force_login(self.cliente)

        html = self.client.get(reverse("properties:home")).content.decode()

        self.assertNotIn(reverse("properties:curator_dashboard"), html)
        self.assertNotIn("Equipa e clientes", html)

    def test_a_curadoria_abre_para_a_equipa_e_e_403_para_o_cliente(self) -> None:
        """O menu e a vista dizem a mesma coisa: a página também recusa."""
        url = reverse("properties:curator_dashboard")

        for membro in (self.curator, self.agent, self.admin):
            with self.subTest(perfil=membro.role):
                self.client.force_login(membro)
                self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_o_dashboard_da_curadoria_filtra_por_estado(self) -> None:
        """O filtro escreve o estado no URL e a lista obedece."""
        rascunho = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            title="Moradia por publicar em Benfica",
        )
        self.client.force_login(self.curator)
        url = reverse("properties:curator_dashboard")

        sem_filtro = self.client.get(url)
        rascunhos = self.client.get(url + "?estado=DRAFT")

        self.assertContains(sem_filtro, rascunho.title)
        self.assertContains(rascunhos, rascunho.title)
        self.assertNotContains(rascunhos, self.prop.title)

    def test_o_dashboard_descarta_o_estado_desconhecido_e_marca_o_todos(self) -> None:
        """Um estado escrito à mão não parte a rota nem fica sem filtro marcado.

        O estado é descartado por inteiro, como a área na pesquisa (§2.12): a
        lista volta a ser a lista, e "Todos" é o que fica marcado. Um recorte a
        meio filtrar é pior do que nenhum recorte.
        """
        self.client.force_login(self.curator)

        response = self.client.get(reverse("properties:curator_dashboard") + "?estado=TZAR")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.prop.title)
        self.assertContains(response, '<a class="badge badge--gold"')

    def test_o_dashboard_ignora_o_estado_em_minusculas(self) -> None:
        """O estado não é um campo livre: o que a página escreve é o que a base devolve."""
        self.client.force_login(self.curator)
        url = reverse("properties:curator_dashboard")

        response = self.client.get(url + "?estado=draft")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.prop.title)

    def test_a_conta_do_administrador_entra_sem_ser_superuser(self) -> None:
        """As páginas internas seguem o perfil, não as permissões do Django admin.

        O `admin@echilo.ao` local é `is_staff` e não é superuser. Se a gestão de
        contas dependesse de `is_superuser`, o administrador do produto ficaria
        de fora da única parte do produto que é só dele.
        """
        self.assertFalse(self.admin.is_superuser)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:team_list"))

        self.assertEqual(response.status_code, 200)


class ConciergeDataIntegrityTests(TestCase):
    """Garante que o que o concierge grava corresponde ao que a equipa vê."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role=User.Role.CURATOR, email="curador@echilo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)
        self.client_user = make_user(email="cliente@echilo.ao")

    def test_visit_request_creates_a_human_conversation(self) -> None:
        """Pedir visita abre imediatamente uma conversa com a equipa (§2.4)."""
        self.client.force_login(self.client_user)
        tomorrow = (timezone.localdate() + timezone.timedelta(days=2)).isoformat()

        self.client.post(
            reverse("concierge:visit_request", args=[self.prop.reference]),
            {"visit_date": tomorrow, "visit_time": "11:00"},
        )

        conversation = Conversation.objects.get()
        self.assertEqual(conversation.status, Conversation.Status.ESCALATED)
        self.assertEqual(conversation.handled_by, Conversation.HandledBy.HUMAN)
        self.assertEqual(conversation.property_interest, self.prop)

    def test_offer_and_visit_are_tracked_per_property(self) -> None:
        """Propostas e visitas ficam ligadas ao imóvel, sem duplicar registos."""
        sale = make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            title="Moradia para venda",
        )
        self.client.force_login(self.client_user)
        self.client.post(
            reverse("concierge:offer_create", args=[sale.reference]),
            {"amount": "80.000.000"},
        )

        offer = Offer.objects.get()
        self.assertEqual(offer.property, sale)
        self.assertEqual(sale.offers.count(), 1)
        self.assertTrue(offer.is_active())
        self.assertEqual(VisitRequest.objects.count(), 0)
        self.assertEqual(Lead.objects.count(), 0)

TAG = re.compile(r"<[a-zA-Z][^>]*>")
CLASS = re.compile(r'\sclass="([^"]*)"')
# E-mails não suportam folhas de estilo externas: inline é a única opção.
STYLE_EXEMPT = ("templates/accounts/emails",)
DUPLICATE_CLASS = re.compile(r'<[a-zA-Z][^>]*\sclass="[^"]*"[^>]*\sclass="[^"]*"[^>]*>')
# Pictogramas e emojis não pertencem à interface: a AGENTS.md exige ícones SVG.
PICTOGRAMS = re.compile(
    "[\u2190-\u21ff\u2300-\u23ff\u2460-\u24ff\u25a0-\u27bf\U0001f000-\U0001faff]"
)
# Tipografia legítima em português que não pode ser confundida com emoji.
ALLOWED_TYPOGRAPHY = ("·", "—", "©")


def _regras_css(texto: str) -> list[tuple[str, str]]:
    """Devolve `(selector, corpo)` de cada regra, entrando nos `@media`.

    Um regex simples não chega: dentro de um `@media` há mais um par de
    chavetas, e as regras que lá vivem — incluindo a segunda definição de
    `.chip` — ficariam de fora da contagem.
    """
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
    regras: list[tuple[str, str]] = []
    i = 0
    while i < len(texto):
        abre = texto.find("{", i)
        if abre == -1:
            break
        seletor = texto[i:abre].strip()
        nivel, j = 1, abre + 1
        while j < len(texto) and nivel:
            nivel += (texto[j] == "{") - (texto[j] == "}")
            j += 1
        corpo = texto[abre + 1 : j - 1]
        if seletor.startswith(("@media", "@supports")):
            regras.extend(_regras_css(corpo))
        else:
            regras.append((" ".join(seletor.split()), corpo))
        i = j
    return regras


class TemplateMarkupTests(SimpleTestCase):
    """Impede a regressão para estilos inline e atributos `class` duplicados."""


    def _templates(self):
        root = Path(settings.BASE_DIR) / "templates"
        return sorted(path for path in root.rglob("*.html"))

    def test_no_inline_styles_outside_emails(self) -> None:
        for path in self._templates():
            if any(marker in path.as_posix() for marker in STYLE_EXEMPT):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "style=" in line:
                    self.fail(f"{path.name}:{number} usa style inline: {line.strip()[:90]}")

    def test_no_duplicate_class_attributes(self) -> None:
        for path in self._templates():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if DUPLICATE_CLASS.search(line):
                    self.fail(f"{path.name}:{number} tem class duplicado: {line.strip()[:90]}")

    def test_no_third_party_script_comes_from_a_cdn(self) -> None:
        """Nenhum script de biblioteca pode vir de um CDN.

        A proteção contra tracking do Edge bloqueia o armazenamento de scripts
        de terceiros e larga avisos no `console`; em definições mais restritivas
        chega a não executar. O HTMX é o que faz os filtros e a pesquisa por área
        funcionarem, portanto perdê-lo parte o catálogo. As duas bibliotecas do
        mapa já eram vendorizadas pela mesma razão.
        """
        for path in self._templates():
            texto = path.read_text(encoding="utf-8")
            for number, line in enumerate(texto.splitlines(), 1):
                if re.search(r'<script[^>]+src="https?://', line):
                    self.fail(f"{path.name}:{number} carrega script de CDN: {line.strip()[:90]}")

    def test_the_only_cdn_left_is_the_webfont(self) -> None:
        """A webfont do Google é a última dependência externa: dívida, não esquecimento.

        Vendorizar `Fraunces` e `Inter` exige os `woff2` por peso e por subconjunto
        e uma folha de estilo reescrita à mão. Fica para antes da produção, com o
        consentimento que uma chamada ao Google traz colado ao site.

        Quando se vendorizar, este teste apaga-se em vez de ser relaxado: é a
        lista do que ainda está por fechar.
        """
        externos: set[str] = set()
        for path in self._templates():
            for linha in path.read_text(encoding="utf-8").splitlines():
                for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', linha):
                    externos.add(url.split("/")[2])

        self.assertEqual(externos, {"fonts.googleapis.com", "fonts.gstatic.com"})

    def test_our_stylesheets_never_use_the_background_shorthand_on_leaflet_links(self) -> None:
        """O atalho `background` apaga o sprite dos botões das bibliotecas do mapa.

        O `leaflet.draw` dá à barra de desenhar a classe `leaflet-bar`, por isso
        uma regra nossa para a barra de zoom também a apanhava. E `background`
        repõe `background-image` a `none`: o botão do círculo ficava com a caixa
        certa e nenhuma imagem dentro, e a ferramenta parecia morta.

        Só os `<a>` interessam: é neles que o Leaflet põe os sprites. Um `div`
        como o `.leaflet-container` não tem imagem para perder.
        """
        for nome in ("echilo.css", "pages.css"):
            caminho = Path(settings.BASE_DIR) / "static" / "css" / nome
            for sel, corpo in _regras_css(caminho.read_text(encoding="utf-8")):
                if "leaflet" not in sel or not re.search(r"(^|[\s,])a($|[\s,:])", sel):
                    continue
                if re.search(r"(?<!-)\bbackground\s*:", corpo):
                    self.fail(
                        f"{nome}: `{sel}` usa o atalho background e apaga o sprite "
                        f"do leaflet. Use background-color."
                    )

    def test_button_like_labels_are_centred(self) -> None:
        """O rótulo de um botão ao estilo de cápsula fica no meio.

        A `.chip` é um `<a>`, e um link herda `text-align: left` do navegador:
        com `min-height` e `padding` lateral o texto ficava encostado à
        esquerda. Depender do valor por omissão do browser também não é uma
        garantia, porque basta um reset noutro sítio para o texto descentrar.
        """
        for nome in ("echilo.css", "pages.css"):
            caminho = Path(settings.BASE_DIR) / "static" / "css" / nome
            for sel, corpo in _regras_css(caminho.read_text(encoding="utf-8")):
                alvos = {p.strip() for p in sel.split(",")}
                if not alvos & {".btn", ".chip", ".tab"}:
                    continue
                # Uma regra que não mexe no alinhamento — uma transição dentro de
                # um `prefers-reduced-motion`, por exemplo — é inocente.
                if not re.search(r"(text-align|justify-content)\s*:", corpo):
                    continue
                if re.search(r"text-align:\s*center", corpo):
                    continue
                if re.search(r"justify-content:\s*center", corpo):
                    continue
                self.fail(f"{nome}: `{sel}` não centra o rótulo")

    def test_every_class_is_defined_in_the_stylesheets(self) -> None:

        sheets = [
            (Path(settings.BASE_DIR) / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("echilo.css", "pages.css")
        ]
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", "\n".join(sheets)))
        used: set[str] = set()
        for path in self._templates():
            for value in re.findall(r'\sclass="([^"{]+)"', path.read_text(encoding="utf-8")):
                used.update(value.split())
        missing = sorted(used - defined)
        self.assertEqual(missing, [], f"classes sem estilo: {missing}")

    def test_every_css_variable_used_is_defined(self) -> None:
        """Uma `var(--x)` por resolver não dá erro: a declaração é inválida e o
        valor cai no inicial.

        No mapa, `var(--success)` no ponto verde da legenda desenhava um círculo
        transparente ao lado de pinos verdes no mapa. Duas cores sem legenda são
        duas cores a adivinhar, e uma legenda com o ponto invisível é a mesma
        coisa com mais trabalho.
        """
        tokens = (Path(settings.BASE_DIR) / "static" / "css" / "echilo.css").read_text(
            encoding="utf-8"
        )
        definidas = set(re.findall(r"(--[a-z0-9-]+)\s*:", tokens))
        por_resolver: dict[str, str] = {}
        for nome in ("echilo.css", "pages.css"):
            texto = (Path(settings.BASE_DIR) / "static" / "css" / nome).read_text(
                encoding="utf-8"
            )
            # Os comentários saem antes da procura, com as linhas em cima: o
            # comentário que explica a variável por resolver mencionava-a.
            texto = re.sub(
                r"/\*.*?\*/",
                lambda m: "\n" * m.group(0).count("\n"),
                texto,
                flags=re.DOTALL,
            )
            for linha, conteudo in enumerate(texto.splitlines(), 1):
                for variavel in re.findall(r"var\((--[a-z0-9-]+)\)", conteudo):
                    por_resolver.setdefault(variavel, f"{nome}:{linha}")

        self.assertEqual(
            {v: o for v, o in por_resolver.items() if v not in definidas},
            {},
            "variáveis por resolver: "
            f"{ {v: o for v, o in por_resolver.items() if v not in definidas} }",
        )

    def test_o_ouvinte_do_circulo_nao_recebe_o_evento_como_raio(self) -> None:
        """`map.on(tipo, fn)` passa o evento a `fn`, e um evento não é um raio.

        Registar a função de redesenho directamente no `EDITED` do `leaflet.draw`
        fazia o evento do Leaflet parar a `clampRadius` como se fosse um número.
        `Number(evento)` dá `NaN`, `clampRadius` devolve o mínimo, e arrastar um
        círculo encolhia-o para 100 m sem erro nenhum: o mapa acceptava o
        comando e desenhava outra coisa.

        Não dá para apanhar isto no browser em cada alteração, e o teste de
       markup também não o apanha — é um valor, não um elemento.
        """
        js = (Path(settings.BASE_DIR) / "static" / "js" / "echilo.js").read_text(
            encoding="utf-8"
        )

        # A comparação é feita linha a linha para a falha mostrar a linha culpada
        # e não o ficheiro inteiro: um `assertNotIn` com o texto de dentro
        # despeja as quarenta kilobytes do JavaScript na consola.
        registos = [
            linha.strip()
            for linha in js.splitlines()
            if "L.Draw.Event.EDITED" in linha and "redrawFromLayer" in linha
        ]
        self.assertTrue(registos, "o redesenho deixou de estar ligado ao EDITED")
        for linha in registos:
            self.assertIn(
                "function",
                linha,
                "o evento do EDITED seria tomado como raio; envolver a chamada "
                f"numa função que não recebe argumentos: {linha}",
            )

    def test_every_visible_form_field_has_a_label(self) -> None:
        """Campos escondidos não são anunciados, logo não precisam de rótulo."""
        for path in self._templates():
            text = path.read_text(encoding="utf-8")
            for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", text):
                if 'type="hidden"' in tag:
                    continue
                field = re.search(r'\bid="([^"]+)"', tag)
                if not field:
                    continue
                self.assertIn(
                    f'for="{field.group(1)}"',
                    text,
                    f"{path.name}: o campo #{field.group(1)} não tem <label for>",
                )

    def test_templates_have_no_emojis_or_pictograms(self) -> None:
        """Emojis são substituídos por ícones do sprite; ver AGENTS.md §5.6."""
        offenders: list[str] = []
        for path in self._templates():
            text = path.read_text(encoding="utf-8")
            for symbol in ALLOWED_TYPOGRAPHY:
                text = text.replace(symbol, "")
            for number, line in enumerate(text.splitlines(), 1):
                found = PICTOGRAMS.search(line)
                if found:
                    offenders.append(f"{path.name}:{number} -> {found.group(0)!r}")
        self.assertEqual(offenders, [], f"pictogramas fora do sprite: {offenders}")

    def test_every_svg_use_points_to_a_sprite_symbol(self) -> None:
        sprite = (Path(settings.BASE_DIR) / "templates" / "partials" / "icons.html").read_text(
            encoding="utf-8"
        )
        symbols = set(re.findall(r'<symbol id="([^"]+)"', sprite))
        self.assertIn("arrow-right", symbols, "falta o ícone de seta no sprite")
        for path in self._templates():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for target in re.findall(r'<use href="#([^"]+)"', line):
                    if "{%" in target:
                        continue  # O ícone é escolhido em tempo de execução.
                    self.assertIn(
                        target,
                        symbols,
                        f"{path.name}:{number} usa #{target} que não existe no sprite",
                    )

    def test_nav_burger_is_an_accessible_animated_toggle(self) -> None:
        """O hamburger é um botão real: contractado por ARIA e animado por CSS."""
        nav = (Path(settings.BASE_DIR) / "templates" / "partials" / "nav.html").read_text(
            encoding="utf-8"
        )
        burger = re.search(r'<button[^>]*id="nav-burger".*?</button>', nav, re.DOTALL)
        self.assertIsNotNone(burger, "o botão do hamburger desapareceu do cabeçalho")
        markup = burger.group(0)
        opening = markup[: markup.index(">") + 1]
        self.assertIn('aria-expanded="false"', opening)
        self.assertIn('aria-controls="nav-menu"', opening)
        self.assertIn("aria-label=", opening)
        self.assertEqual(
            len(re.findall(r'nav__burger-bar--\w+', markup)),
            3,
            "o X precisa de três barras",
        )
        self.assertIn("nav__menu-inner", nav, "falta o wrapper que colapsa a altura do menu")

    def test_motion_is_gated_behind_prefers_reduced_motion(self) -> None:
        """Animações de entrada só existem para quem as pediu."""
        css = (Path(settings.BASE_DIR) / "static" / "css" / "echilo.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("@media (prefers-reduced-motion: no-preference)", css)
        for keyframe in ("reveal-up", "message-in", "menu-item-in", "modal-in"):
            self.assertIn(f"@keyframes {keyframe}", css, f"falta a animação {keyframe}")

    def test_reveal_falls_back_when_scroll_timelines_are_missing(self) -> None:
        """Firefox não tem `animation-timeline`: o observer tem de cobrir esse caso."""
        js = (Path(settings.BASE_DIR) / "static" / "js" / "echilo.js").read_text(encoding="utf-8")
        self.assertIn("IntersectionObserver", js)
        self.assertIn("animation-timeline", js)
        self.assertIn("has-reveal", js)
        # O conteúdo introduzido pelo HTMX também tem de ser animado.
        self.assertIn("htmx:afterSwap", js)
        css = (Path(settings.BASE_DIR) / "static" / "css" / "echilo.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".has-reveal main [data-reveal]", css)
        self.assertIn(".is-revealed", css)

    def test_gallery_thumbnails_are_real_buttons(self) -> None:
        """Miniaturas sem `<button>` não são operáveis por teclado."""
        detail = (
            Path(settings.BASE_DIR) / "templates" / "properties" / "property_detail.html"
        ).read_text(encoding="utf-8")
        self.assertIn('class="gallery__thumb', detail)
        self.assertIn("data-full=", detail)
        self.assertIn("is-active", detail, "a primeira miniatura tem de nascer activa")
        # A imagem principal continua a ser a fonte de verdade do alt.
        self.assertIn('id="galeria-img"', detail)

    def test_reveal_is_applied_across_the_public_templates(self) -> None:
        """A animação de entrada não pode ficar só na página inicial."""
        expected = (
            "index.html",
            "properties/property_list.html",
            "properties/property_detail.html",
            "assistant/chat.html",
            "concierge/owner_intake.html",
            "accounts/base_auth.html",
            "partials/property_card.html",
        )
        root = Path(settings.BASE_DIR) / "templates"
        for relative in expected:
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("data-reveal", text, f"{relative} não tem animação de entrada")

    def test_chrome_never_shows_a_loading_state(self) -> None:
        """O cabeçalho e o rodapé são permanentes: nunca entram em loading."""
        root = Path(settings.BASE_DIR) / "templates"
        for relative in ("partials/nav.html", "partials/footer.html"):
            text = (root / relative).read_text(encoding="utf-8")
            for marker in ("skeleton", "data-reveal", "htmx-indicator", "aria-busy"):
                self.assertNotIn(
                    marker,
                    text,
                    f"{relative} não pode conter {marker}: o chrome não carrega",
                )

        css = (Path(settings.BASE_DIR) / "static" / "css" / "echilo.css").read_text(
            encoding="utf-8"
        )
        # A guarda tem de existir para sobreviver a uma edição futura.
        self.assertIn(".nav .htmx-skeleton", css)
        self.assertIn(".footer .htmx-skeleton", css)
        self.assertIn("main [data-reveal]", css)

        js = (Path(settings.BASE_DIR) / "static" / "js" / "echilo.js").read_text(encoding="utf-8")
        self.assertIn("main [data-reveal]", js, "o observer também só pode ver o conteúdo")

    def test_skeleton_is_sibling_of_the_htmx_target(self) -> None:
        """Se o esqueleto estiver dentro do alvo, o swap apaga-o."""
        for relative, target in (
            ("properties/property_list.html", "listing-resultados"),
            ("assistant/chat.html", "chat-body"),
        ):
            text = (Path(settings.BASE_DIR) / "templates" / relative).read_text(encoding="utf-8")
            pane_at = text.index('class="skeleton-pane')
            target_at = text.index(f'id="{target}"')
            skeleton_at = text.index('class="htmx-skeleton')
            self.assertLess(
                pane_at,
                target_at,
                f"{relative}: o alvo #{target} tem de estar dentro do painel",
            )
            self.assertLess(
                target_at,
                skeleton_at,
                f"{relative}: o esqueleto tem de vir depois do alvo #{target}, como irmão",
            )
            self.assertIn(
                f'hx-target="#{target}"',
                text,
                f"{relative}: o alvo do HTMX tem de continuar a ser #{target}",
            )
            self.assertIn('hx-indicator="#', text, f"{relative} precisa de ligar o esqueleto")

    def test_skeleton_repeats_a_bounded_number_of_cards(self) -> None:
        """Um esqueleto sem limite bloquearia a grelha se a view mandar mais."""
        from apps.core.templatetags.echilo_format import repeat

        self.assertEqual(len(repeat(6)), 6)
        self.assertEqual(repeat(999), [None] * 12, "o limite de 12 não é negociável")
        self.assertEqual(repeat(-3), [])
        self.assertEqual(repeat("nope"), [])


class ConfirmacaoModalTests(TestCase):
    """A confirmação de acções destrutivas é um componente do sistema.

    O `window.confirm` que estava no `echilo.js` era o único diálogo nativo que
    restava. Um diálogo do browser não se deixa estilizar, é desenhado pelo
    browser e não pela folha de estilos, e aparece como um quadrado cinzento no
    meio de uma interface feita à mão. Numa página onde se apaga uma fotografia
    sem forma de a recuperar, um botão de "OK" genérico por cima de um aviso
    genérico é a forma mais rápida de apagar a coisa errada.
    """

    # `alert(`, `confirm(` e `prompt(` do browser. O `\b` inicial é o que
    # impede que apanhe o nosso `data-confirmar`, que é um atributo e não uma
    # chamada.
    DIALOGO_NATIVO = re.compile(r"\b(?:window\.)?(?:alert|confirm|prompt)\s*\(")
    COMMENTO_JS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)

    def _js_do_projecto(self) -> list[Path]:
        pasta = Path(settings.BASE_DIR) / "static" / "js"
        return [p for p in sorted(pasta.glob("*.js")) if "vendor" not in p.parts]

    def _sem_comentarios(self, texto: str) -> str:
        """O código sem os comentários.

        O ficheiro explica *porquê* o `alert` dos tiles foi trocado por uma caixa
        e menciona as palavras `alert` e `confirm` em prosa. Se a procura não
        tirar os comentários primeiro, o teste acusa o próprio teste de estar
        errado — e o próximo que o ler deixa de lhe dar crédito.
        """
        return self.COMMENTO_JS.sub(lambda m: "\n" * m.group(0).count("\n"), texto)

    def test_nao_ha_dialogos_nativos_no_javascript(self) -> None:
        """Nenhum `alert`, `confirm` ou `prompt` sobrevive no nosso código.

        Todos os ficheiros de `static/js/` são lidos, e não só o `echilo.js`:
        um `confirm` num ficheiro novo passava a resposta e voltava a apagar
        imóveis sem o modal aparecer. A comparação é feita linha a linha para a
        falha dizer o ficheiro e a linha em vez de despejar o JavaScript.
        """
        achados: list[str] = []
        for caminho in self._js_do_projecto():
            for numero, linha in enumerate(
                self._sem_comentarios(caminho.read_text(encoding="utf-8")).splitlines(), 1
            ):
                if self.DIALOGO_NATIVO.search(linha):
                    achados.append(f"{caminho.name}:{numero}: {linha.strip()}")

        self.assertEqual(achados, [], f"diálogos nativos: {achados}")

    def test_a_caixa_existe_em_todas_as_paginas(self) -> None:
        """A caixa vem do `base.html`, e não de cada página que a usa.

        Uma cópia por página daria duas respostas à mesma pergunta, e a segunda
        é sempre a que ninguém actualiza. Duas páginas diferentes são renderizadas
        para provar que o include chega às duas.
        """
        base = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('{% include "partials/confirmacao.html" %}', base)

        for url in ("/", "/imoveis/"):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode("utf-8")
                self.assertIn('id="confirmacao"', html)
                self.assertIn("data-modal-confirmar", html)

    def test_o_cancelar_vem_antes_do_confirmar(self) -> None:
        """O `showModal()` foca o primeiro elemento da caixa.

        Quem abre a confirmação com o teclado e prime logo `Enter` não pode ter o
        botão que apaga a fotografia debaixo do dedo. É a ordem no HTML que
        decide qual é o primeiro, e não o CSS: um `order` resolveria o desenho e
        não o foco.
        """
        parcial = render_to_string("partials/confirmacao.html")

        self.assertLess(
            parcial.index("data-modal-cancelar"),
            parcial.index("data-modal-confirmar"),
            "o botão de cancelar tem de ser o primeiro focável da caixa",
        )

    def test_a_caixa_usa_o_formulario_dialog_e_nao_o_atributo_open(self) -> None:
        """`method="dialog"` é o que dá `Esc`, `Enter` e o `returnValue`.

        O `Esc` e o valor devolvido à mão são o caminho que dá dois `addEventListener`
        para cada tecla e um `keydown` global a disputar o foco com o resto da
        página. O atributo `open` é o oposto do que se quer: abre uma caixa que
        não é modal, sem `::backdrop` e sem o resto da página inerte.
        """
        parcial = render_to_string("partials/confirmacao.html")

        self.assertIn('method="dialog"', parcial)
        self.assertNotIn(" open", parcial)
        self.assertIn('value="confirmar"', parcial)
        self.assertIn('value="cancelar"', parcial)

    def test_o_javascript_usa_showmodal(self) -> None:
        """`showModal()` e não `dialog.open = true`, pelo mesmo motivo."""
        js = (Path(settings.BASE_DIR) / "static" / "js" / "echilo.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("showModal()", js)
        self.assertNotIn(".open = true", js)
        # `showModal()` numa caixa já aberta lança `InvalidStateError`, e o clique
        # que confirmou a acção destrutiva é um clique como os outros.
        self.assertIn("dialogo.open", js)

    def test_as_accoes_destrutivas_titulo_e_rotulo_proprios(self) -> None:
        """Cada acção destrutiva escreve o seu título e o seu botão.

        Sem os dois atributos a caixa mostra "Confirmar acção" e "Confirmar" por
        cima de um aviso que apaga uma ficha. Um botão que não diz o que faz
        convida ao erro, e o `data-confirmar` sozinho não chega.
        """
        sem_titulo: list[str] = []
        templates = Path(settings.BASE_DIR) / "templates"
        for caminho in sorted(templates.rglob("*.html")):
            texto = caminho.read_text(encoding="utf-8")
            if 'data-confirmar="' not in texto:
                continue
            for bloco in re.findall(r"<button[^>]*data-confirmar=[^>]*>", texto, re.DOTALL):
                if "data-confirmar-titulo=" not in bloco or "data-confirmar-ok=" not in bloco:
                    sem_titulo.append(f"{caminho.name}: {bloco[:60]}...")

        self.assertEqual(sem_titulo, [], f"acções sem título ou rótulo: {sem_titulo}")

    def test_as_accoes_destrutivas_declaradas_em_python_tambem_sao_confirmadas(self) -> None:
        """Um widget destrutivo também escreve título e rótulo.

        A acção destrutiva nem sempre é um `<button>` num template: a caixa de
        selecção que remove a fotografia do perfil é um widget declarado no
        formulário, e o `{{ field }}` do partial não tem como saber que aquele
        campo apaga alguma coisa. Varrer só os templates deixava este caso de
        fora da regra sem dar erro — a caixa perguntava "Confirmar acção" sobre
        um ficheiro que ia embora.
        """
        sem_titulo: list[str] = []
        for app in sorted((Path(settings.BASE_DIR) / "apps").glob("*/forms.py")):
            texto = app.read_text(encoding="utf-8")
            if "data-confirmar" not in texto:
                continue
            for bloco in re.findall(r'"data-confirmar":.*?\}\)', texto, re.DOTALL):
                if "data-confirmar-titulo" not in bloco or "data-confirmar-ok" not in bloco:
                    sem_titulo.append(f"{app.parent.name}: {bloco[:60]}...")

        self.assertEqual(sem_titulo, [], f"widgets sem título ou rótulo: {sem_titulo}")

    def test_a_caixa_de_selecao_destrutiva_tambem_pede_confirmacao(self) -> None:
        """Marcar a caixa que remove a fotografia abre a confirmação.

        A caixa de selecção é a acção destrutiva que não é um clique de
        perigo: quem a marca está a dizer o que quer, e mesmo assim o ficheiro
        vai com a conta. A confirmação desliga-se disto, não da ausência de
        `data-confirmar` no botão.
        """
        from apps.accounts.forms import ProfileForm

        widget = ProfileForm().fields["remover_foto"].widget
        attrs = widget.attrs

        self.assertIn("data-confirmar", attrs)
        self.assertIn("data-confirmar-titulo", attrs)
        self.assertIn("data-confirmar-ok", attrs)

    def test_a_confirmacao_da_caixa_de_selecao_desmarca_quando_se_cancela(self) -> None:
        """Cancelar tem de desfazer a marca, e não deixá-la como estava.

        A caixa já fica marcada quando a pessoa a selecciona, e é por isso que o
        caminho da selecção não repete o clique: escreve a marca. Sem o
        `checked = confirmado` do `close`, "Cancelar" confirmava em silêncio a
        remoção que dizia estar a cancelar.
        """
        js = (Path(settings.BASE_DIR) / "static" / "js" / "echilo.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("eCaixa(alvo)", js)
        self.assertIn("alvo.checked = confirmado", js)
        # O caminho do clique não pode tratar a caixa de selecção: o browser já
        # a marcou, e repetir o clique com um `preventDefault` em cima dava um
        # estado que ninguém pediu.
        self.assertIn("if (eCaixa(botao)) return;", js)

    def _sem_blocos_de(self, texto: str, condicao: str) -> str:
        """Devolve o CSS sem os blocos `@media` cuja condição é a indicada.

        Contar chavetas é o que faz isto certo: com um regex, um bloco que
       termina onde acaba o primeiro `{` interior corta a folha ao meio e o
        teste passa a medir outra coisa.
        """
        fora: list[str] = []
        i = 0
        while True:
            abre = texto.find("{", i)
            if abre == -1:
                fora.append(texto[i:])
                break
            cabecalho = texto[i:abre]
            nivel, j = 1, abre + 1
            while j < len(texto) and nivel:
                nivel += (texto[j] == "{") - (texto[j] == "}")
                j += 1
            if condicao in cabecalho:
                fora.append("\n" * cabecalho.count("\n"))
            else:
                fora.append(texto[i:j])
            i = j
        return "".join(fora)

    def test_a_caixa_move_se_so_com_movimento_pedido(self) -> None:
        """A entrada da caixa respeita a preferência de movimento reduzido.

        O teste mede o inverso do óbvio: tira os blocos `no-preference` e exige
        que a animação já não esteja lá. Contar quantos blocos existem não
        diria nada — a folha tem mais do que um, e o número é uma coincidência
        da redacção, não uma regra.
        """
        css = (Path(settings.BASE_DIR) / "static" / "css" / "echilo.css").read_text(
            encoding="utf-8"
        )
        sem_movimento = self._sem_blocos_de(css, "prefers-reduced-motion: no-preference")

        self.assertIn(".modal[open]", css)
        self.assertNotIn(
            ".modal[open]",
            sem_movimento,
            "a caixa anima-se fora do bloco de movimento pedido",
        )
        self.assertIn("@keyframes modal-in", css)

    def test_a_caixa_diz_o_que_esta_em_risco(self) -> None:
        """A caixa tem de estar rotulada pelo texto e pelo título que descreve.

        Um `<dialog>` sem `aria-labelledby` é uma caixa sem nome, e o leitor de
        ecrã anuncia "diálogo" sem dizer sobre o quê. O texto é o que o
        JavaScript escreve, e é por isso que a descrição aponta para o mesmo
        `id`.
        """
        parcial = render_to_string("partials/confirmacao.html")

        self.assertIn('aria-labelledby="confirmacao-titulo"', parcial)
        self.assertIn('aria-describedby="confirmacao-texto"', parcial)
        self.assertIn('id="confirmacao-titulo"', parcial)
        self.assertIn('id="confirmacao-texto"', parcial)
        # O texto nasce vazio: quem escreve nele é o `data-confirmar` do botão.
        # Mark-up com um aviso de exemplo seria um aviso falso em três páginas.
        self.assertIn('id="confirmacao-texto" class="modal__texto"></p>', parcial)


class AngolanIdentityValidatorTests(SimpleTestCase):
    """Testa as regras do NIF e da idade isoladas, sem passar por um formulário."""

    def test_nif_accepts_the_angolan_format(self) -> None:
        """O formato é 9 dígitos, 2 letras e 3 dígitos."""
        validate_nif("009671373HA093")

    def test_nif_tolerates_spaces_and_lowercase(self) -> None:
        """Quem digita com espaços ou minúsculas não deve ser recusado."""
        self.assertEqual(normalize_nif(" 009671373 ha 093 "), "009671373HA093")
        validate_nif("009671373 ha 093")
        validate_nif("009671373ha093")

    def test_nif_refuses_wrong_shapes(self) -> None:
        """Faltar um dígito, trocar letras por números ou alongar é erro."""
        for invalid in (
            "00671373HA093",  # só 8 dígitos
            "0096713731A093",  # 10 dígitos
            "009671373H093",  # só 1 letra
            "009671373HAG93",  # 3 letras
            "00967137394093",  # letras por dígitos
            "ABCDEFGHIJKLMN",
            "",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_nif(invalid)

    def test_age_counts_completed_years(self) -> None:
        """O dia do aniversário conta no próprio dia, não no dia seguinte."""
        self.assertEqual(age_in_years(date(2008, 5, 20), today=date(2026, 5, 20)), 18)
        self.assertEqual(age_in_years(date(2008, 5, 21), today=date(2026, 5, 20)), 17)
        self.assertEqual(age_in_years(date(2008, 2, 29), today=date(2026, 2, 28)), 17)
        self.assertEqual(age_in_years(date(2008, 12, 31), today=date(2026, 1, 1)), 17)

    def test_adult_refuses_a_minor(self) -> None:
        """Um menor de idade não abre conta, mesmo no dia anterior ao aniversário."""
        with self.assertRaisesMessage(ValidationError, "pelo menos 18 anos"):
            validate_adult(date(2008, 5, 21), today=date(2026, 5, 20))

    def test_adult_accepts_exactly_eighteen(self) -> None:
        """Fazer 18 anos hoje é suficiente."""
        validate_adult(date(2008, 5, 20), today=date(2026, 5, 20))

    def test_adult_refuses_the_future(self) -> None:
        """Uma data de nascimento no futuro é sempre erro de digitação."""
        with self.assertRaisesMessage(ValidationError, "futuro"):
            validate_adult(date(2027, 1, 1), today=date(2026, 5, 20))

    def test_adult_refuses_implausible_age(self) -> None:
        """Uma data de 130 anos atrás é erro, não um Senior angulano de 130 anos."""
        with self.assertRaisesMessage(ValidationError, "Verifique a data"):
            validate_adult(date(1890, 1, 1), today=date(2026, 5, 20))


def _destination_point(
    lat: float, lon: float, distance_m: float, bearing_degrees: float
) -> tuple[float, float]:
    """Ponto a `distance_m` de `(lat, lon)` na direcção indicada.

    Implementação independente de `circle_bounding_box`, pela lei esférica dos
    cosenos, para o teste não estar a validar a fórmula contra si mesma.
    """
    delta = distance_m / EARTH_RADIUS_M
    bearing = radians(bearing_degrees)
    phi1, lambda1 = radians(lat), radians(lon)
    phi2 = asin(sin(phi1) * cos(delta) + cos(phi1) * sin(delta) * cos(bearing))
    lambda2 = lambda1 + asin(sin(bearing) * sin(delta) / cos(phi2))
    return degrees(phi2), degrees(lambda2)


class AreaSearchGeometryTests(SimpleTestCase):

    def test_haversine_is_zero_for_the_same_point(self) -> None:
        """O centro do círculo está a zero metros de si próprio."""
        self.assertEqual(haversine_metres(-8.918430, 13.184700, -8.918430, 13.184700), 0.0)

    def test_haversine_measures_a_degree_of_latitude(self) -> None:
        """Um grau de latitude vale perto dos 111 195 m da esfera de referência."""
        distance = haversine_metres(0.0, 0.0, 1.0, 0.0)

        self.assertAlmostEqual(distance, 111_195.0, delta=500.0)

    def test_haversine_knows_the_distance_between_two_angolan_cities(self) -> None:
        """Luanda e Benguela distam 416 km em linha recta, não em estrada."""
        distance = haversine_metres(-8.8390, 13.2894, -12.5783, 13.4915)

        self.assertAlmostEqual(distance / 1000, 416.4, delta=2.0)

    def test_haversine_is_symmetric(self) -> None:
        """A distância não depende da ordem dos pontos."""
        forward = haversine_metres(-8.8390, 13.2894, -12.5783, 13.4915)
        backward = haversine_metres(-12.5783, 13.4915, -8.8390, 13.2894)

        self.assertEqual(forward, backward)

    def test_bounding_box_contains_the_whole_circle(self) -> None:
        """A caixa é uma superserquisa: nenhum ponto do círculo fica de fora.

        Os pontos são gerados pela lei esférica dos cosenos, e não por uma
        projecção linear: só assim se prova que a caixa contém o círculo que a
        distância exacta mede. Foi exactamente esta divergência entre as duas
        fórmulas que fez a caixa cortar a borda.
        """
        lat, lon, radius = -8.918430, 13.184700, 2_000.0

        lat_min, lat_max, lon_min, lon_max = circle_bounding_box(lat, lon, radius)

        for bearing_degrees in range(0, 360, 15):
            point_lat, point_lon = _destination_point(lat, lon, radius, bearing_degrees)
            with self.subTest(bearing=bearing_degrees):
                self.assertGreaterEqual(point_lat, float(lat_min))
                self.assertLessEqual(point_lat, float(lat_max))
                self.assertGreaterEqual(point_lon, float(lon_min))
                self.assertLessEqual(point_lon, float(lon_max))

    def test_bounding_box_holds_a_property_sitting_on_the_edge(self) -> None:
        """Um imóvel plantado na borda tem de sobreviver ao filtro SQL."""
        lat, lon, radius = -8.918430, 13.184700, 2_224.0
        edge_lat, edge_lon = _destination_point(lat, lon, radius, 180.0)

        lat_min, lat_max, lon_min, lon_max = circle_bounding_box(lat, lon, radius)

        self.assertTrue(lat_min <= Decimal(f"{edge_lat:.6f}") <= lat_max)
        self.assertTrue(lon_min <= Decimal(f"{edge_lon:.6f}") <= lon_max)

    def test_bounding_box_narrows_with_a_smaller_radius(self) -> None:
        """Um raio de 500 m tem de produzir uma caixa menor que a de 5 km."""
        lat, lon = -8.918430, 13.184700

        small = circle_bounding_box(lat, lon, 500.0)
        large = circle_bounding_box(lat, lon, 5_000.0)

        self.assertLess(float(small[1]) - float(small[0]), float(large[1]) - float(large[0]))
        self.assertLess(float(small[3]) - float(small[2]), float(large[3]) - float(large[2]))

    def test_bounding_box_never_leaves_the_planet(self) -> None:
        """Um raio absurdo junto ao polo tem de ser aparado, não rebentar."""
        lat_min, lat_max, lon_min, lon_max = circle_bounding_box(89.9, 0.0, 50_000_000.0)

        self.assertGreaterEqual(float(lat_min), -90.0)
        self.assertLessEqual(float(lat_max), 90.0)
        self.assertGreaterEqual(float(lon_min), -180.0)
        self.assertLessEqual(float(lon_max), 180.0)

    def test_bounding_box_matches_the_model_precision(self) -> None:
        """A caixa sai em Decimal com seis casas, como `Property.latitude`."""
        _, _, lon_min, _ = circle_bounding_box(-8.918430, 13.184700, 2_000.0)

        self.assertEqual(lon_min.as_tuple().exponent, -6)


class AreaSearchValidatorTests(SimpleTestCase):
    """Testa os limites da área isolados, sem passar por um formulário."""

    def test_center_accepts_a_point_in_angola(self) -> None:
        """O Kilamba é um centro de círculo legítimo."""
        validate_center("-8.918430", "13.184700")

    def test_coordinates_off_the_planet_are_refused(self) -> None:
        """Latitude acima de 90 ou longitude acima de 180 não são pontos."""
        for latitude, longitude in (
            ("91", "13"),
            ("-91", "13"),
            ("8.9", "181"),
            ("8.9", "-181"),
        ):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValidationError):
                    validate_center(latitude, longitude)

    def test_area_that_is_not_a_number_is_refused(self) -> None:
        """Texto ou número vazio não podem virar coordenada."""
        for value in ("abc", "", None, "NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.assertIsNone(to_decimal_or_none(value))
                with self.assertRaises(ValidationError):
                    validate_latitude(value)

    def test_radius_bounds_are_enforced(self) -> None:
        """Abaixo de 100 m não é uma área; acima de 50 km é uma tabela inteira."""
        validate_search_radius_m(MIN_SEARCH_RADIUS_M)
        validate_search_radius_m(MAX_SEARCH_RADIUS_M)

        for invalid in (0, 99, -1_000, MAX_SEARCH_RADIUS_M + 1, "50 km"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_search_radius_m(invalid)

    def test_radius_error_names_the_limit_in_kilometres(self) -> None:
        """A mensagem tem de ser legível por quem a lê no formulário."""
        with self.assertRaisesMessage(ValidationError, "50 km"):
            validate_search_radius_m(500_000)


class LimiteDoPedidoTests(SimpleTestCase):
    """Nenhum tecto de ficheiro pode ser maior do que o pedido que o transporta.

    A Vercel recusa o pedido na edge aos 4,5 MB, e recusa-o antes de o Django ver
    a requisição. Um formulário que aceita 5 MB tem uma validação que nunca
    corre, e o aviso que a pessoa recebe é um «Content Too Large» sem página.
    """

    def test_o_tecto_de_upload_cabe_no_pedido(self) -> None:
        self.assertLess(LIMITE_UPLOAD_MB, LIMITE_PEDIDO_MB)
        self.assertGreater(LIMITE_UPLOAD_MB, 0)

    def test_o_tecto_de_upload_sao_megabytes_inteiros(self) -> None:
        """A validação compara com um tamanho de ficheiro, e um `float` não arredonda.

        `motivo_recusa_imagem(limite_mb=3.5)` escreve `3.5 * 1024 * 1024`, que é um
        `float` a comparar-se com os bytes de um ficheiro. Funciona, e continua a
        funcionar quando o valor deixa de caber no que o browser envia.
        """
        self.assertIsInstance(LIMITE_UPLOAD_MB, int)
        self.assertEqual(LIMITE_UPLOAD_MB * 1024 * 1024, 4 * 1024 * 1024)

    def test_a_margem_e_o_que_fica_entre_o_tecto_e_o_pedido(self) -> None:
        """A margem é o espaço do multipart e do resto do formulário."""
        self.assertGreaterEqual(
            LIMITE_UPLOAD_MB + MARGEM_PEDIDO_MB, LIMITE_PEDIDO_MB - 0.001
        )

    def test_nenhum_formulario_de_upload_passa_do_pedido(self) -> None:
        """Os tectos que escrevem `5` no `help_text` aceitam o que a plataforma recusa."""
        from apps.accounts.forms import LIMITE_FOTO_PERFIL_MB
        from apps.properties.validators import ORCAMENTO_LOTE_MB

        for nome, tecto in (
            ("fotografia de perfil", LIMITE_FOTO_PERFIL_MB),
            ("lote de fotografias", ORCAMENTO_LOTE_MB),
        ):
            with self.subTest(ficheiro=nome):
                self.assertLessEqual(tecto, LIMITE_UPLOAD_MB)

    def test_o_orcamento_do_lote_fica_abaixo_do_tecto(self) -> None:
        """O orçamento é o que se persegue, e o tecto é onde a validação corta.

        São números diferentes com trabalhos diferentes: o orçamento divide-se por
        quinze, e é por isso que é uma fracção. Igualá-los ao tecto faz o orçamento
        estragar no primeiro ficheiro que não cabe na divisão.
        """
        from apps.properties.validators import ORCAMENTO_LOTE_MB

        self.assertLess(ORCAMENTO_LOTE_MB, LIMITE_UPLOAD_MB)


class InvariantNumberTests(SimpleTestCase):
    """`LANGUAGE_CODE = "pt-ao"` localiza números, e números Viajam.

    `{{ valor }}` escreve `-8,918430`. Numa label ou num cartão isso é o que
    queremos; num `<input>` que o formulário reenvia, o servidor recebe texto que
    o `Decimal` recusa e a área desenhada desaparece sem aviso. Estes testes
    fixam a diferença entre as duas viagens.
    """

    TEMPLATE = "{% load echilo_format %}{{ value }}|{{ value|coordinate }}"

    def _render(self, value: object) -> tuple[str, str]:
        rendered = Template(self.TEMPLATE).render(Context({"value": value}))
        localized, invariant = rendered.split("|")
        return localized, invariant

    def test_the_plain_output_is_what_breaks_the_round_trip(self) -> None:
        """A armadilha é real: o valor cru não volta a ser número."""
        localized, invariant = self._render(Decimal("-8.918430"))

        self.assertEqual(localized, "-8,918430")
        with self.assertRaises(ArithmeticError):
            Decimal(localized)
        self.assertEqual(Decimal(invariant), Decimal("-8.918430"))

    def test_coordinate_keeps_the_dot_and_drops_the_padding(self) -> None:
        """Zeros à direita não são informação, e o mapa não precisa deles."""
        cases = {
            Decimal("-8.918430"): "-8.91843",
            Decimal("13.184700"): "13.1847",
            Decimal("50000"): "50000",
            0: "0",
            2000: "2000",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(self._render(value)[1], expected)

    def test_coordinate_never_falls_back_to_scientific_notation(self) -> None:
        """`Decimal('1E+2')` é o mesmo número, e uma coordenada que o mapa recusa."""
        self.assertEqual(self._render(Decimal("2E+3"))[1], "2000")
        self.assertEqual(self._render(Decimal("1234567"))[1], "1234567")

    def test_coordinate_outputs_nothing_rather_than_a_broken_value(self) -> None:
        """Uma vírgula decimal em trânsito é exactamente o defeito que se quer evitar."""
        for value in (None, "", "abc", "13,1847", "1.000,00"):
            with self.subTest(value=value):
                self.assertEqual(self._render(value)[1], "")

    def test_distance_reads_as_a_person_would_say_it(self) -> None:
        """O raio viaja em metros no URL, mas «50 000 m» não se lê como raio."""
        cases = {
            100: "100 m",
            999: "999 m",
            1000: "1 km",
            2500: "2,5 km",
            50_000: "50 km",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(distance(value), expected)

    def test_reading_filters_still_speak_angolan(self) -> None:
        """Dinheiro e distância continuam em ponto de milhar e vírgula decimal."""
        self.assertEqual(kwanza(450_000), "450.000,00 Kz")
        self.assertEqual(kwanza_compact(85_000_000), "85 M Kz")


class FormStylingTests(SimpleTestCase):
    """Garante que nenhum formulário escape ao sistema de design dos campos."""

    def test_every_form_inherits_the_styled_base(self) -> None:
        """Um `forms.Form` cru produz widgets sem `field-control`, ou seja, sem estilo."""
        offenders = []
        for app_config in apps.get_app_configs():
            if not app_config.name.startswith("apps."):
                continue
            try:
                forms_module = import_module(f"{app_config.name}.forms")
            except ModuleNotFoundError:
                continue
            for name, obj in vars(forms_module).items():
                if not isinstance(obj, type) or not issubclass(obj, forms.Form):
                    continue
                if obj.__module__ != forms_module.__name__:
                    continue
                if not issubclass(obj, BaseStyledForm):
                    offenders.append(f"{obj.__module__}.{name}")
        self.assertEqual(
            offenders,
            [],
            "herdam de `BaseStyledForm` ou o input fica sem estilo: " + ", ".join(offenders),
        )

    def test_password_reset_input_carries_the_design_system_class(self) -> None:
        """A página de recuperação foi reportada com o campo sem estilo nenhum."""
        response = self.client.get(reverse("accounts:password_reset"))
        self.assertEqual(response.status_code, 200)
        email_input = response.context["form"]["email"]
        self.assertIn("field-control", email_input.field.widget.attrs.get("class", ""))
        self.assertIn('class="field-control"', response.content.decode())

    def test_password_reset_keeps_email_autocomplete(self) -> None:
        """A classe base não pode apagar o `autocomplete` que o Django define."""
        from apps.accounts.forms import EchiloPasswordResetForm

        attrs = EchiloPasswordResetForm().fields["email"].widget.attrs
        self.assertEqual(attrs.get("autocomplete"), "email")
        self.assertEqual(EchiloPasswordResetForm().fields["email"].label, "E-mail")


class VerifyMapTilesTests(SimpleTestCase):
    """O comando tem de distinguir um mapa de um aviso com a mesma forma.

    Ambos são HTTP 200 e PNG 256x256. A diferença está no conteúdo, e o critério
    não pode ser o tamanho nem a variedade de tons: um basemap minimalista é
    legitimamente pequeno e quase liso, e um tile de oceano também.
    """

    def test_the_tile_coordinates_follow_the_webs_mercator_convention(self) -> None:
        """A ordem dos eixos é o que faz o mapa cair no sítio certo.

        O Esri serve em `{z}/{y}/{x}` e o Leaflet escreve `{z}/{x}/{y}`. Um
        `replace` por posição trocava os dois e ia buscar o lado errado do mundo.

        Luanda dá (1099, 1074), que é o tile que se vê no mapa. Nairobi dá
        (1233, 1031): a soma de `tan` e `sec` de -1,29° é 0,9777, o logaritmo
        é -0,02252, e `(1 - (-0,02252)/pi) / 2 * 2048` dá 1031.
        """
        self.assertEqual(_tile_xy(-8.8383, 13.2344, 11), (1099, 1074))
        self.assertEqual(_tile_xy(-1.29, 36.82, 11), (1233, 1031))

    def test_zooming_in_doubles_the_tile_indices(self) -> None:
        """Cada nível de zoom dobra a grade, e o tile cobre metade do terreno."""
        x11, y11 = _tile_xy(-8.8383, 13.2344, 11)
        x12, y12 = _tile_xy(-8.8383, 13.2344, 12)

        self.assertEqual(x12, x11 * 2)
        self.assertEqual(y12, y11 * 2)


class PaginaDeTests(SimpleTestCase):
    """O paginador das views que não são `ListView`."""

    def _pedido(self, pagina: str = "") -> HttpRequest:
        return RequestFactory().get("/", {"page": pagina} if pagina else {})

    def test_divide_a_lista_pelas_paginas(self) -> None:
        """Treze linhas a seis dão duas páginas cheias e uma de uma."""
        pagina = pagina_de(range(PAGINA_PADRAO * 2 + 1), self._pedido())

        self.assertEqual(len(pagina.object_list), PAGINA_PADRAO)
        self.assertEqual(pagina.paginator.num_pages, 3)
        self.assertTrue(pagina.has_next())

    def test_a_segunda_pagina_tem_o_que_sobra(self) -> None:
        pagina = pagina_de(range(7), self._pedido("2"))

        self.assertEqual(list(pagina.object_list), [6])

    def test_uma_pagina_que_nao_existe_volta_a_ultima(self) -> None:
        """Um `?page=99` escrito à mão não devolve uma página de erro.

        O `get_page` do Django é o que resolve isto, e é a razão de a fila de
        contactos não precisar de um `try` à volta do `page()`.
        """
        pagina = pagina_de(range(7), self._pedido("99"))

        self.assertEqual(pagina.number, 2)

    def test_uma_pagina_que_nao_e_numero_volta_a_primeira(self) -> None:
        """`?page=lixo` também não é uma página de erro."""
        pagina = pagina_de(range(7), self._pedido("lixo"))

        self.assertEqual(pagina.number, 1)


class ListagensUsamOPaginaPadraoTests(SimpleTestCase):
    """Nenhuma listagem escreve o seu próprio tamanho de página.

    O número estava em três sítios com três valores — `12`, `24` e `25` —, e a
    divergência não dá erro nenhum: cada listagem respondia à pergunta com um
    número diferente e todas elas pareciam correctas. A regra é uma só, e este
    teste é o que a mantém.
    """

    def _listagens(self) -> list[tuple[type[ListView], str]]:
        encontradas: list[tuple[type[ListView], str]] = []
        for app_config in apps.get_app_configs():
            try:
                modulo = import_module(f"{app_config.name}.views")
            except ModuleNotFoundError:
                continue
            for nome, obj in vars(modulo).items():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, ListView)
                    and obj is not ListView
                ):
                    encontradas.append((obj, f"{app_config.name}.views.{nome}"))
        return encontradas

    def test_toda_a_listagem_usa_o_tamanho_partilhado(self) -> None:
        """Cada `ListView` do projecto tem de paginar com `PAGINA_PADRAO`."""
        for view, nome in self._listagens():
            with self.subTest(listagem=nome):
                self.assertEqual(view.paginate_by, PAGINA_PADRAO)

    def test_a_varredura_encontra_as_listagens_do_projecto(self) -> None:
        """A varredura não pode passar a verde por não ver nada.

        Um teste que percorre classes que ninguém voltou a registar continua a
        passar quando a lista esvazia, e é a forma mais barata de um teste de
        guarda não estar a guardar nada.
        """
        self.assertGreaterEqual(len(self._listagens()), 5)

