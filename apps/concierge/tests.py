"""Testes do atendimento: adesão, visitas, propostas e fila de contactos."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.pagination import PAGINA_PADRAO
from apps.core.testing import make_owner, make_property, make_user
from apps.properties.models import Property

from .models import Lead, Offer, VisitRequest

User = get_user_model()


def _future_day(offset: int = 3) -> str:
    """Devolve uma data futura no formato aceito pelo formulário."""
    return (timezone.localdate() + timedelta(days=offset)).isoformat()


class OwnerIntakeTests(TestCase):
    """Etapa 1 da captação: o formulário público cria um Lead OWNER_INTAKE."""

    url = "/atendimento/aderir-imovel/"

    def setUp(self) -> None:
        cache.clear()
        self.url = reverse("concierge:owner_intake")

    def test_form_is_public(self) -> None:
        """O formulário de adesão não exige autenticação (§2.1)."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anunciar o seu imóvel com a equipa Echilo")

    def test_valid_submission_creates_lead_in_new_state(self) -> None:
        """Uma adesão válida cria o Lead em estado NEW, atribuível à equipa."""
        response = self.client.post(
            self.url,
            {
                "full_name": "Joaquim Ferreira",
                "phone": "+244 912 345 678",
                "email": "joaquim@exemplo.ao",
                "purpose_interest": "RENT",
                "message": "Tenho um T2 no Kilamba para arrendar.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recebemos o seu contacto")
        lead = Lead.objects.get()
        self.assertEqual(lead.lead_type, Lead.Type.OWNER_INTAKE)
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertEqual(lead.full_name, "Joaquim Ferreira")
        self.assertEqual(lead.purpose_interest, "RENT")

    def test_invalid_submission_creates_nothing(self) -> None:
        """Sem telefone nem nome não há Lead: o formulário volta com erros."""
        response = self.client.post(self.url, {"full_name": "", "phone": ""})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Lead.objects.exists())

    def test_repeated_submissions_are_throttled(self) -> None:
        """A captação é limitada para não encher a fila de spam (§6)."""
        payload = {
            "full_name": "Joaquim Ferreira",
            "phone": "+244 912 345 678",
            "email": "joaquim@exemplo.ao",
        }
        for _ in range(4):
            self.client.post(self.url, payload)
        response = self.client.post(self.url, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), 4)
        self.assertFormError(
            response,
            "form",
            None,
            "Recebemos muitos contactos deste dispositivo. Tente mais tarde.",
        )


class VisitRequestTests(TestCase):
    """§2.10: pedidos de visita exigem conta e nunca são confirmados sozinhos."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(curator=self.curator, owner=self.owner)
        self.client_user = make_user(email="visitante@exemplo.ao")
        self.url = reverse("concierge:visit_request", args=[self.prop.reference])

    def test_anonymous_is_redirected_to_login(self) -> None:
        """Um visitante sem conta não marca visitas."""
        response = self.client.post(
            self.url, {"visit_date": _future_day(), "visit_time": "10:00"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertFalse(VisitRequest.objects.exists())

    def test_valid_request_is_pending(self) -> None:
        """O pedido nasce PENDING; só a equipa confirma (§2.10)."""
        self.client.force_login(self.client_user)
        response = self.client.post(
            self.url,
            {"visit_date": _future_day(), "visit_time": "10:00", "notes": "Venho às 10h."},
        )

        self.assertRedirects(response, self.prop.get_absolute_url())
        visit = VisitRequest.objects.get()
        self.assertEqual(visit.status, VisitRequest.Status.PENDING)
        self.assertEqual(visit.requested_by, self.client_user)
        self.assertEqual(visit.property, self.prop)
        self.assertIsNone(visit.handled_by)
        self.assertEqual(timezone.localtime(visit.scheduled_for).hour, 10)

    def test_past_date_is_refused(self) -> None:
        """Uma data passada não é aceite."""
        self.client.force_login(self.client_user)
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(
            self.url, {"visit_date": yesterday, "visit_time": "10:00"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, "form", "visit_date", "Escolha uma data futura para a visita.")
        self.assertFalse(VisitRequest.objects.exists())

    def test_unpublished_property_is_not_reachable(self) -> None:
        """Imóveis não publicados não são acessíveis pelo fluxo de visitas."""
        draft = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            title="T3 em rascunho",
        )
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("concierge:visit_request", args=[draft.reference]))

        self.assertEqual(response.status_code, 404)


class OfferTests(TestCase):
    """§2.5: a proposta é sempre registada em Kwanza e tratada por um humano."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.sale = make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            title="Moradia para venda",
        )
        self.rent = make_property(
            curator=self.curator,
            owner=self.owner,
            title="T3 para arrendar",
        )
        self.client_user = make_user(email="cliente@exemplo.ao")
        self.url = reverse("concierge:offer_create", args=[self.sale.reference])

    def test_rent_property_has_no_offer_form(self) -> None:
        """Só imóveis à venda aceitam proposta formal."""
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("concierge:offer_create", args=[self.rent.reference]))

        self.assertRedirects(response, self.rent.get_absolute_url())
        self.assertFalse(Offer.objects.exists())

    def test_valid_offer_is_submitted_in_kwanza(self) -> None:
        """A proposta nasce SUBMITTED, em AOA, com o valor exacto em Decimal."""
        self.client.force_login(self.client_user)
        response = self.client.post(
            self.url, {"amount": "82.000.000", "message": "Fecho esta semana."}
        )

        self.assertRedirects(response, self.sale.get_absolute_url())
        offer = Offer.objects.get()
        self.assertEqual(offer.status, Offer.Status.SUBMITTED)
        self.assertEqual(offer.currency, "AOA")
        self.assertEqual(offer.amount, Decimal("82000000.00"))
        self.assertEqual(offer.submitted_by, self.client_user)
        self.assertIsNone(offer.responded_by)

    def test_zero_amount_is_refused(self) -> None:
        """Uma proposta de valor zero ou negativo é rejeitada."""
        self.client.force_login(self.client_user)
        response = self.client.post(self.url, {"amount": "0"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, "form", "amount", "O valor proposto tem de ser maior do que zero.")
        self.assertFalse(Offer.objects.exists())


class LeadQueueTests(TestCase):
    """A fila de contactos é exclusiva da equipa (§3)."""

    def setUp(self) -> None:
        cache.clear()
        self.url = reverse("concierge:lead_queue")
        self.client_user = make_user(email="leitor@exemplo.ao")
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.intake = Lead.objects.create(
            full_name="Joaquim Ferreira",
            phone="+244 912 345 678",
            lead_type=Lead.Type.OWNER_INTAKE,
            status=Lead.Status.NEW,
        )
        self.enquiry = Lead.objects.create(
            full_name="Carlos Baptista",
            phone="+244 933 222 111",
            lead_type=Lead.Type.CLIENT_ENQUIRY,
            status=Lead.Status.QUALIFIED,
        )

    def test_client_is_forbidden(self) -> None:
        """Um CLIENT não acede ao painel interno."""
        self.client.force_login(self.client_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self) -> None:
        """Sem sessão há redireccionamento para o login."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_agent_sees_every_lead(self) -> None:
        """Um agente vê a fila completa."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["leads"]), 2)
        self.assertContains(response, "Joaquim Ferreira")
        self.assertContains(response, "Carlos Baptista")

    def test_filter_narrows_the_queue(self) -> None:
        """Os filtros da fila são aplicados à base de dados."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url, {"lead_type": Lead.Type.OWNER_INTAKE})

        self.assertEqual(list(response.context["leads"]), [self.intake])
        self.assertNotContains(response, "Carlos Baptista")

    def test_filter_by_status(self) -> None:
        """O filtro por estado também restringe a lista."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url, {"status": Lead.Status.QUALIFIED})

        self.assertEqual(list(response.context["leads"]), [self.enquiry])

    def test_invalid_filter_is_ignored(self) -> None:
        """Um valor de filtro inválido não parte a view."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url, {"lead_type": "INEXISTENTE"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["leads"]), 2)

    def test_a_fila_nao_custa_uma_consulta_por_contacto(self) -> None:
        """Cinco contactos na página custam as mesmas consultas que dois.

        A comparação é entre duas capturas com número de linhas diferente, e não
        com um número solto. Um total absoluto muda sempre que se toca numa linha
        do `base.html` — o mesmo ficheiro que desenha o cabeçalho, o rodapé e o
        `avatar` de cada membro da equipa —, e aí o teste falha sem que a
        listagem tenha piorado. O que tem de ser constante é o custo *por linha*.

        É o `select_related` da view que segura isto: o `assigned_to` entra na
        consulta, e a linha do template lê `lead.assigned_to.full_name`. Todos os
        contactos têm de estar atribuídos: uma fila onde ninguém foi responsável
        por nada não chega ao campo que faria a consulta, e o teste passava
        com o `select_related` removido.
        """
        Lead.objects.update(assigned_to=self.agent)
        self.client.force_login(self.agent)

        with CaptureQueriesContext(connection) as com_dois:
            self.client.get(self.url)

        for indice in range(3):
            Lead.objects.create(
                full_name=f"Contacto {indice:02d}",
                phone="+244 912 345 678",
                lead_type=Lead.Type.CLIENT_ENQUIRY,
                status=Lead.Status.NEW,
                assigned_to=self.agent,
            )

        with CaptureQueriesContext(connection) as com_cinco:
            response = self.client.get(self.url)

        # Cinco cabem numa página (`PAGINA_PADRAO` são seis): a comparação mede
        # linhas desenhadas, e linhas de uma segunda página não medem nada.
        self.assertEqual(len(response.context["leads"]), 5)
        self.assertEqual(len(com_cinco), len(com_dois))


class LeadQueuePaginationTests(TestCase):
    """A fila de contactos é paginada, e a paginação sabe o que está a mostrar."""

    def setUp(self) -> None:
        cache.clear()
        self.url = reverse("concierge:lead_queue")
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.leads = [
            Lead.objects.create(
                full_name=f"Contacto {indice:02d}",
                phone="+244 912 345 678",
                lead_type=Lead.Type.CLIENT_ENQUIRY,
                status=Lead.Status.NEW,
            )
            for indice in range(PAGINA_PADRAO + 1)
        ]

    def test_a_fila_mostra_seis_de_seis(self) -> None:
        """A primeira página traz seis e a segunda traz a que resta."""
        self.client.force_login(self.agent)
        primeira = self.client.get(self.url)
        segunda = self.client.get(self.url, {"page": 2})

        self.assertEqual(len(primeira.context["leads"]), PAGINA_PADRAO)
        self.assertEqual(len(segunda.context["leads"]), 1)
        # A fila é `-created_at`, ou seja o mais recente primeiro. O contacto que
        # fica de fora é o primeiro a ter entrado, e é esse que a segunda página
        # tem de trazer — a escolha errada aqui era supor ordem de inserção.
        mais_antigo = self.leads[0]
        self.assertNotIn(mais_antigo.full_name, primeira.content.decode())
        self.assertContains(segunda, mais_antigo.full_name)

    def test_a_fila_diz_quantas_paginas_tem(self) -> None:
        """A caixa de navegação aparece e diz o total."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url)

        self.assertContains(response, "Paginação da fila de contactos")
        self.assertContains(response, "de 2")
        self.assertContains(response, "page=2")

    def test_a_pagina_seguinte_guarda_o_filtro(self) -> None:
        """Mudar de página a perder o filtro é trocar a vista sem ninguém pedir."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url, {"status": Lead.Status.NEW})

        self.assertContains(response, "status=NEW")
        self.assertContains(response, "page=2")

    def test_a_fila_nao_corta_mais_a_cento(self) -> None:
        """Já não há um tecto mudo: o total é o que o paginador diz."""
        self.client.force_login(self.agent)
        response = self.client.get(self.url)

        self.assertEqual(response.context["leads"].paginator.count, PAGINA_PADRAO + 1)
