"""Testes do atendimento: adesão, visitas, propostas e fila de contactos."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.pagination import PAGINA_PADRAO
from apps.core.testing import make_owner, make_property, make_user
from apps.properties.models import Property

from .models import Lead, Offer, VisitRequest
from .services import (
    advance_lead,
    answer_offer,
    counter_offer,
    decide_visit,
    due_for_scheduling,
    get_or_create_conversation,
    request_visit,
    submit_offer,
)

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


class DecideVisitTests(TestCase):
    """A equipa decide; o cliente lê o que lhe diz respeito (§2.10)."""

    def setUp(self) -> None:
        cache.clear()
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.rent = make_property(
            curator=self.curator, owner=self.owner, title="T3 para arrendar"
        )
        self.sale = make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            title="Moradia para venda",
        )
        self.client_user = make_user(email="cliente@exemplo.ao")

    def _visit(self, prop: object | None = None, days: int = 3) -> VisitRequest:
        """Cria o pedido pela via do cliente, como o produto faz."""
        return request_visit(
            prop=prop or self.rent,
            user=self.client_user,
            scheduled_for=timezone.now() + timedelta(days=days),
        )

    def _visible(self, prop: object):
        """As mensagens que o cliente pode ler sobre aquele imóvel."""
        conversation = get_or_create_conversation(
            user=self.client_user, property_interest=prop
        )
        return conversation.messages.filter(is_internal=False)

    def test_decline_without_reason_is_refused(self) -> None:
        """Recusar sem motivo não muda nada: o motivo é o que o cliente lê."""
        visit = self._visit()

        with self.assertRaises(ValidationError):
            decide_visit(
                visit=visit, target=VisitRequest.Status.DECLINED, actor=self.agent
            )

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitRequest.Status.PENDING)

    def test_decline_records_reason_and_notifies(self) -> None:
        """A recusa grava motivo, autor e data, e o cliente lê o motivo."""
        visit = self._visit()

        decide_visit(
            visit=visit,
            target=VisitRequest.Status.DECLINED,
            actor=self.agent,
            reason="O dono viaja nessa semana.",
        )

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitRequest.Status.DECLINED)
        self.assertEqual(visit.decline_reason, "O dono viaja nessa semana.")
        self.assertEqual(visit.handled_by, self.agent)
        self.assertIsNotNone(visit.handled_at)
        self.assertIn("O dono viaja nessa semana.", self._visible(self.rent).last().body)

    def test_confirm_does_not_fill_decline_reason(self) -> None:
        """A nota interna da confirmação não cai no campo da recusa."""
        visit = self._visit()

        decide_visit(
            visit=visit,
            target=VisitRequest.Status.CONFIRMED,
            actor=self.agent,
            reason="Cliente prefere de manhã.",
        )

        visit.refresh_from_db()
        self.assertEqual(visit.decline_reason, "")
        bodies = [message.body for message in self._visible(self.rent)]
        self.assertTrue(any("confirmada" in body for body in bodies))
        self.assertFalse(any("manhã" in body for body in bodies))

    def test_reopen_clears_stale_reason(self) -> None:
        """Reabrir limpa o motivo: pertence à recusa, não à visita."""
        visit = self._visit()
        decide_visit(
            visit=visit,
            target=VisitRequest.Status.DECLINED,
            actor=self.agent,
            reason="Chuva.",
        )

        decide_visit(visit=visit, target=VisitRequest.Status.PENDING, actor=self.agent)

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitRequest.Status.PENDING)
        self.assertEqual(visit.decline_reason, "")

    def test_no_show_writes_nothing_to_client(self) -> None:
        """O `NO_SHOW` é registo interno: o cliente sabe que não apareceu."""
        visit = self._visit()
        decide_visit(
            visit=visit, target=VisitRequest.Status.CONFIRMED, actor=self.agent
        )
        before = self._visible(self.rent).count()

        decide_visit(visit=visit, target=VisitRequest.Status.NO_SHOW, actor=self.agent)

        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitRequest.Status.NO_SHOW)
        self.assertEqual(self._visible(self.rent).count(), before)

    def test_completed_on_sale_asks_for_offer(self) -> None:
        """Visita concluída em imóvel à venda pergunta pela proposta."""
        visit = self._visit(prop=self.sale)
        decide_visit(
            visit=visit, target=VisitRequest.Status.CONFIRMED, actor=self.agent
        )

        decide_visit(
            visit=visit, target=VisitRequest.Status.COMPLETED, actor=self.agent
        )

        self.assertIn("proposta", self._visible(self.sale).last().body)

    def test_completed_on_rent_has_no_offer_question(self) -> None:
        """Em arrendamento não há o que propor: a pergunta não aparece."""
        visit = self._visit()
        decide_visit(
            visit=visit, target=VisitRequest.Status.CONFIRMED, actor=self.agent
        )

        decide_visit(
            visit=visit, target=VisitRequest.Status.COMPLETED, actor=self.agent
        )

        self.assertNotIn("proposta", self._visible(self.rent).last().body)

    def test_invalid_transition_raises(self) -> None:
        """Um `PENDING` não é concluído sem passar pela confirmação."""
        visit = self._visit()

        with self.assertRaises(ValidationError):
            decide_visit(
                visit=visit, target=VisitRequest.Status.COMPLETED, actor=self.agent
            )


class AnswerOfferTests(TestCase):
    """A resposta da equipa chega ao cliente certo (§2.5)."""

    def setUp(self) -> None:
        cache.clear()
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.sale = make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            title="Moradia para venda",
        )
        self.client_user = make_user(email="cliente@exemplo.ao")

    def _offer(self, amount: str = "82000000.00") -> Offer:
        return submit_offer(
            prop=self.sale, user=self.client_user, amount=Decimal(amount)
        )

    def _visible(self):
        conversation = get_or_create_conversation(
            user=self.client_user, property_interest=self.sale
        )
        return conversation.messages.filter(is_internal=False)

    def test_accept_notifies_client(self) -> None:
        """O aceite regista autor e data, e o cliente lê que foi aceite."""
        offer = self._offer()

        answer_offer(offer=offer, target=Offer.Status.ACCEPTED, actor=self.agent)

        offer.refresh_from_db()
        self.assertEqual(offer.responded_by, self.agent)
        self.assertIsNotNone(offer.responded_at)
        self.assertIn("aceite", self._visible().last().body)

    def test_countered_target_is_refused(self) -> None:
        """`COUNTERED` por transição é recusado: não regista o preço da equipa."""
        offer = self._offer()

        with self.assertRaises(ValidationError):
            answer_offer(
                offer=offer, target=Offer.Status.COUNTERED, actor=self.agent
            )

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.SUBMITTED)

    def test_accept_on_counter_reaches_original_client(self) -> None:
        """O aviso do aceite da contraproposta vai ao cliente, não ao agente."""
        offer = self._offer()
        contra = counter_offer(
            offer=offer,
            amount=Decimal("80000000.00"),
            actor=self.agent,
            message="Último preço.",
        )

        answer_offer(offer=contra, target=Offer.Status.ACCEPTED, actor=self.agent)

        bodies = [message.body for message in self._visible()]
        self.assertTrue(any("aceite" in body for body in bodies))

    def test_withdraw_notifies_client(self) -> None:
        """A desistência registada pela equipa aparece ao cliente como retirada."""
        offer = self._offer()

        answer_offer(
            offer=offer,
            target=Offer.Status.WITHDRAWN,
            actor=self.agent,
            notes="Cliente desistiu ao telefone.",
        )

        self.assertIn("retirada", self._visible().last().body)

    def test_answer_on_closed_offer_raises(self) -> None:
        """Proposta fechada não responde duas vezes."""
        offer = self._offer()
        answer_offer(offer=offer, target=Offer.Status.ACCEPTED, actor=self.agent)

        with self.assertRaises(ValidationError):
            answer_offer(offer=offer, target=Offer.Status.REJECTED, actor=self.agent)


class CounterOfferTests(TestCase):
    """A contraproposta é uma proposta nova com pai, não um estado."""

    def setUp(self) -> None:
        cache.clear()
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.sale = make_property(
            curator=self.curator,
            owner=self.owner,
            purpose=Property.Purpose.SALE,
            price="85000000.00",
            title="Moradia para venda",
        )
        self.client_user = make_user(email="cliente@exemplo.ao")

    def test_counter_links_parent_and_moves_state(self) -> None:
        """A contraproposta referencia a original e a original passa a `COUNTERED`."""
        offer = submit_offer(
            prop=self.sale, user=self.client_user, amount=Decimal("82000000.00")
        )

        contra = counter_offer(
            offer=offer,
            amount=Decimal("80000000.00"),
            actor=self.agent,
            message="Último preço.",
        )

        self.assertEqual(contra.parent, offer)
        self.assertTrue(contra.is_counter())
        self.assertFalse(offer.is_counter())
        self.assertEqual(contra.submitted_by, self.agent)
        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.COUNTERED)
        self.assertEqual(offer.responded_by, self.agent)

    def test_counter_of_counter_is_refused(self) -> None:
        """Cadeias de contraproposta não existem: responde-se à original."""
        offer = submit_offer(
            prop=self.sale, user=self.client_user, amount=Decimal("82000000.00")
        )
        contra = counter_offer(
            offer=offer, amount=Decimal("80000000.00"), actor=self.agent
        )

        with self.assertRaises(ValidationError):
            counter_offer(
                offer=contra, amount=Decimal("79000000.00"), actor=self.agent
            )

    def test_counter_of_closed_offer_is_refused(self) -> None:
        """Proposta encerrada não é contraposta."""
        offer = submit_offer(
            prop=self.sale, user=self.client_user, amount=Decimal("82000000.00")
        )
        answer_offer(offer=offer, target=Offer.Status.REJECTED, actor=self.agent)

        with self.assertRaises(ValidationError):
            counter_offer(
                offer=offer, amount=Decimal("80000000.00"), actor=self.agent
            )

    def test_counter_with_zero_amount_is_refused(self) -> None:
        """A contraproposta valida o valor como a proposta: zero não entra."""
        offer = submit_offer(
            prop=self.sale, user=self.client_user, amount=Decimal("82000000.00")
        )

        with self.assertRaises(ValidationError):
            counter_offer(offer=offer, amount=Decimal(0), actor=self.agent)

        self.assertEqual(Offer.objects.count(), 1)
        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.SUBMITTED)


class AdvanceLeadTests(TestCase):
    """O contacto anda na máquina e fica com responsável (§2.9)."""

    def setUp(self) -> None:
        cache.clear()
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.other = make_user(role=User.Role.AGENT, email="outro@exemplo.ao")

    def _lead(self) -> Lead:
        return Lead.objects.create(
            full_name="Maria dos Santos",
            phone="+244 923 456 789",
            lead_type=Lead.Type.CLIENT_ENQUIRY,
        )

    def test_advance_assigns_actor_when_unassigned(self) -> None:
        """Quem trata fica dono do contacto, se ainda não houver dono."""
        lead = self._lead()

        advance_lead(lead=lead, target=Lead.Status.CONTACTED, actor=self.agent)

        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CONTACTED)
        self.assertEqual(lead.assigned_to, self.agent)

    def test_advance_keeps_existing_assignee(self) -> None:
        """Dono posto não é trocado a cada passagem."""
        lead = self._lead()
        advance_lead(lead=lead, target=Lead.Status.CONTACTED, actor=self.agent)

        advance_lead(lead=lead, target=Lead.Status.QUALIFIED, actor=self.other)

        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.agent)

    def test_invalid_transition_raises(self) -> None:
        """Um `NEW` não é convertido sem passar pelo contacto e pela qualificação."""
        lead = self._lead()

        with self.assertRaises(ValidationError):
            advance_lead(lead=lead, target=Lead.Status.CONVERTED, actor=self.agent)


class SchedulingTests(TestCase):
    """O trabalho de fundo avisa uma vez e marca o que viu."""

    def setUp(self) -> None:
        cache.clear()
        self.agent = make_user(role=User.Role.AGENT, email="agente@exemplo.ao")
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.prop = make_property(
            curator=self.curator, owner=self.owner, title="T3 para arrendar"
        )
        self.client_user = make_user(email="cliente@exemplo.ao")

    def _confirmed_in(self, hours: int) -> VisitRequest:
        visit = request_visit(
            prop=self.prop,
            user=self.client_user,
            scheduled_for=timezone.now() + timedelta(hours=hours),
        )
        return decide_visit(
            visit=visit, target=VisitRequest.Status.CONFIRMED, actor=self.agent
        )

    def _reminders(self) -> list:
        conversation = get_or_create_conversation(
            user=self.client_user, property_interest=self.prop
        )
        return [
            message.body
            for message in conversation.messages.filter(is_internal=False)
            if "Lembrete" in message.body
        ]

    def test_reminder_runs_once(self) -> None:
        """Correr duas vezes avisa uma: a segunda devolve zero."""
        self._confirmed_in(hours=10)

        first = due_for_scheduling()
        second = due_for_scheduling()

        self.assertEqual(first, {"lembretes": 1, "reconfirmacoes": 0})
        self.assertEqual(second, {"lembretes": 0, "reconfirmacoes": 0})
        self.assertEqual(len(self._reminders()), 1)

    def test_outside_window_is_ignored(self) -> None:
        """Visita a 30 horas não entra na janela de 24."""
        self._confirmed_in(hours=30)

        self.assertEqual(
            due_for_scheduling(), {"lembretes": 0, "reconfirmacoes": 0}
        )
        self.assertEqual(self._reminders(), [])

    def test_unhandled_pending_is_flagged(self) -> None:
        """Pedido por tratar com data em cima aparece à reconfirmação."""
        VisitRequest.objects.create(
            property=self.prop,
            requested_by=self.client_user,
            scheduled_for=timezone.now() - timedelta(hours=1),
        )

        result = due_for_scheduling()

        self.assertEqual(result["reconfirmacoes"], 1)
        visit = VisitRequest.objects.get()
        self.assertIsNotNone(visit.reconfirmation_flagged_at)

    def test_handled_pending_is_not_flagged(self) -> None:
        """O que a equipa já tratou não volta à fila por causa do carimbo."""
        VisitRequest.objects.create(
            property=self.prop,
            requested_by=self.client_user,
            scheduled_for=timezone.now() - timedelta(hours=1),
            handled_by=self.agent,
            handled_at=timezone.now() - timedelta(hours=2),
        )

        self.assertEqual(
            due_for_scheduling(), {"lembretes": 0, "reconfirmacoes": 0}
        )