"""Testes do nível 1: escalação, ferramentas e ciclo de tool-use sem rede."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import add_message, constants
from django.contrib.messages.middleware import MessageMiddleware
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse

from apps.assistant.provider import AssistantUnavailable, GroqProvider, should_escalate
from apps.assistant.services import _history_for, ask
from apps.assistant.tools import (
    TOOL_SCHEMAS,
    TOOL_SEARCH_PROPERTIES,
    _coerce_decimal,
    _coerce_int,
    run_tool,
)
from apps.assistant.views import chat_window
from apps.concierge.models import Conversation, Message
from apps.concierge.services import messages_after
from apps.core.testing import make_owner, make_property, make_user
from apps.properties.models import Property

from .fakes import FakeGroqClient, FakeRefusingClient, text_reply, tool_reply

User = get_user_model()


class EscalationMarkerTests(TestCase):
    """§2.4: certos assuntos nunca são tratados só pela IA."""

    def test_visit_booking_is_escalated(self) -> None:
        """Pedidos de agendamento vão para a equipa."""
        escalate, _ = should_escalate("Quero marcar uma visita para amanhã")

        self.assertTrue(escalate)

    def test_price_negotiation_is_escalated(self) -> None:
        """Negociação de preço é sempre humana."""
        escalate, _ = should_escalate("Aceitam 80 milhões em vez de 85?")

        self.assertTrue(escalate)

    def test_property_question_is_not_escalated(self) -> None:
        """Dúvidas sobre o imóvel continuam no nível 1."""
        escalate, reason = should_escalate("O imóvel tem tanque de água?")

        self.assertFalse(escalate)
        self.assertEqual(reason, "")

    def test_annual_payment_question_is_not_escalated(self) -> None:
        """Perguntar se aceita pagamento anual é nível 1, não negociação."""
        escalate, _ = should_escalate("Aceita pagamento anual?")

        self.assertFalse(escalate)

    def test_discount_request_is_escalated(self) -> None:
        """Um pedido de desconto vai para a equipa."""
        escalate, reason = should_escalate("Faz algum desconto se fechar já?")

        self.assertTrue(escalate)
        self.assertEqual(reason, "negociação de preço")

    def test_lower_offer_is_escalated(self) -> None:
        """Uma contraproposta com valor é negociação."""
        escalate, _ = should_escalate("Aceitam 80 milhões em vez de 85?")

        self.assertTrue(escalate)

    def test_complaint_is_escalated(self) -> None:
        """Uma queixa sobre um anúncio vai para a equipa."""
        escalate, reason = should_escalate("Quero reclamar de um anúncio falso")

        self.assertTrue(escalate)
        self.assertEqual(reason, "queixa ou disputa")

    def test_removal_request_is_escalated(self) -> None:
        """Pedir a retirada de um imóvel é sempre humano."""
        escalate, _ = should_escalate("Quero retirar o meu imóvel do site")

        self.assertTrue(escalate)


@override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key")
class GroqProviderTests(TestCase):
    """O loop de ferramentas só termina com uma resposta sem tool calls."""

    def test_plain_answer_is_returned(self) -> None:
        """Uma resposta sem ferramentas é devolvida tal como o modelo a deu."""
        client = FakeGroqClient([text_reply("A renda é 450.000 Kz por mês.")])

        with patch("groq.Groq", return_value=client):
            reply = GroqProvider().complete(
                system_prompt="prompt", history=[], question="Qual é a renda?"
            )

        self.assertEqual(reply.content, "A renda é 450.000 Kz por mês.")
        self.assertFalse(reply.should_escalate)
        self.assertEqual(client.request_count, 1)

    def test_answer_suggesting_a_visit_never_escalates(self) -> None:
        """Sugerir uma visita na resposta não retira o cliente do nível 1."""
        client = FakeGroqClient(
            [
                text_reply(
                    "A renda é 450.000 Kz por mês. Se quiseres marcar uma visita, "
                    "a equipa entra em contacto contigo."
                )
            ]
        )

        with patch("groq.Groq", return_value=client):
            reply = GroqProvider().complete(
                system_prompt="prompt", history=[], question="Qual é a renda do Kilamba?"
            )

        self.assertFalse(reply.should_escalate)
        self.assertEqual(reply.escalated_reason, "")

    def test_tool_result_is_fed_back_to_the_model(self) -> None:
        """O resultado da ferramenta volta ao modelo antes da resposta final."""
        client = FakeGroqClient(
            [
                tool_reply("call_1", "search_properties", '{"purpose": "RENT"}'),
                text_reply("Encontrei um T3 no Kilamba por 450.000 Kz."),
            ]
        )

        with patch("groq.Groq", return_value=client):
            reply = GroqProvider().complete(
                system_prompt="prompt", history=[], question="Mostra-me arrendamentos"
            )

        self.assertIn("450.000 Kz", reply.content)
        second_call = client.calls[1]
        tool_messages = [m for m in second_call["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_1")
        self.assertIn("total_found", json.loads(tool_messages[0]["content"]))

    def test_history_is_capped(self) -> None:
        """O histórico enviado ao modelo é limitado para não estourar o contexto."""
        client = FakeGroqClient([text_reply("Certo.")])

        with patch("groq.Groq", return_value=client):
            GroqProvider().complete(
                system_prompt="prompt",
                history=[{"role": "user", "content": f"pergunta {n}"} for n in range(50)],
                question="E agora?",
            )

        sent = client.calls[0]["messages"]
        self.assertEqual(len(sent), 1 + 20 + 1)
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1]["content"], "E agora?")

    def test_disabled_assistant_raises(self) -> None:
        """Sem `ECHILO_AI_API_KEY` o nível 1 não arranca."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY=""),
            self.assertRaises(AssistantUnavailable),
        ):
            GroqProvider()


class ToolArgumentCoercionTests(TestCase):
    """O modelo devolve tipos imperfeitos; a ferramenta tem de os absorver.

    A Groq rejeita a chamada inteira com HTTP 400 quando um argumento não cumpre o
    schema, por isso o schema aceita `null` e a normalização acontece aqui.
    """

    def test_optional_filters_accept_null(self) -> None:
        """`null` é o valor normal de um filtro que o cliente não pediu."""
        self.assertEqual(_coerce_decimal(None), None)
        self.assertEqual(_coerce_decimal(""), None)
        self.assertEqual(_coerce_int(None), None)

    def test_plain_number_is_kept(self) -> None:
        """Um número simples passa sem alterações."""
        self.assertEqual(_coerce_decimal(450000), Decimal("450000"))
        self.assertEqual(_coerce_int(3), 3)

    def test_formatted_kwanza_is_read_as_angolan_format(self) -> None:
        """`450.000 Kz` são quatrocentos e cinquenta mil, não quatrocentos e cinquenta."""
        self.assertEqual(_coerce_decimal("450.000 Kz"), Decimal("450000"))
        self.assertEqual(_coerce_decimal("450 000"), Decimal("450000"))
        self.assertEqual(_coerce_decimal("450.000,50"), Decimal("450000.50"))

    def test_unusable_price_is_ignored(self) -> None:
        """Um valor sem número não pode partir a pesquisa."""
        self.assertEqual(_coerce_decimal("sob consulta"), None)
        self.assertEqual(_coerce_int("três"), None)

    def test_schema_allows_null_for_optional_arguments(self) -> None:
        """Todos os filtros opcionais aceitam `null` no schema."""
        schema = next(
            item
            for item in TOOL_SCHEMAS
            if item["function"]["name"] == TOOL_SEARCH_PROPERTIES
        )
        properties = schema["function"]["parameters"]["properties"]

        for name, definition in properties.items():
            with self.subTest(argumento=name):
                self.assertIn("null", definition["type"])


class ToolTests(TestCase):
    """As ferramentas só leem imóveis publicados (§2.4)."""

    def setUp(self) -> None:
        cache.clear()
        self.curator = make_user(role=User.Role.CURATOR, email="curador@exemplo.ao")
        self.owner = make_owner(created_by=self.curator)
        self.published = make_property(curator=self.curator, owner=self.owner)
        self.draft = make_property(
            curator=self.curator,
            owner=self.owner,
            status=Property.Status.DRAFT,
            title="Imóvel em curadoria",
        )

    def test_search_only_returns_published(self) -> None:
        """A pesquisa nunca expõe imóveis fora de PUBLISHED."""
        payload = json.loads(run_tool("search_properties", {"purpose": "RENT"}))

        references = [row["reference"] for row in payload["results"]]
        self.assertIn(self.published.reference, references)
        self.assertNotIn(self.draft.reference, references)

    def test_search_formats_price_in_kwanza(self) -> None:
        """O preço é apresentado em Kwanza, com separador de milhares (§2.5)."""
        payload = json.loads(run_tool("search_properties", {"purpose": "RENT"}))
        row = next(r for r in payload["results"] if r["reference"] == self.published.reference)

        self.assertEqual(row["price"], "450.000,00 Kz")

    def test_details_of_draft_are_not_found(self) -> None:
        """Pedir detalhes de um imóvel em curadoria devolve `found: false`."""
        payload = json.loads(
            run_tool("get_property_details", {"reference": self.draft.reference})
        )

        self.assertFalse(payload["found"])
        self.assertIn("message", payload)

    def test_details_hide_exact_address(self) -> None:
        """A morada exacta nunca é entregue à IA (§2.3)."""
        self.published.address_hint = "Rua 12, Bairro Azul, casa 34"
        self.published.save(update_fields=["address_hint"])

        payload = json.loads(
            run_tool("get_property_details", {"reference": self.published.reference})
        )

        self.assertTrue(payload["found"])
        self.assertIn("morada exacta", payload["exact_address_note"])
        self.assertNotIn("Rua 12", json.dumps(payload, ensure_ascii=False))

    def test_compare_limits_to_three(self) -> None:
        """A comparação devolve no máximo três imóveis."""
        payload = json.loads(
            run_tool("compare_properties", {"references": [self.published.reference] * 5})
        )

        self.assertLessEqual(len(payload["found"]), 3)

    def test_unknown_tool_reports_error(self) -> None:
        """Uma ferramenta desconhecida é recusada em JSON."""
        payload = json.loads(run_tool("drop_database", {}))

        self.assertIn("error", payload)


class AskServiceTests(TestCase):
    """`ask` persiste o turno e escala sem duplicar a resposta do modelo."""

    def setUp(self) -> None:
        cache.clear()
        self.user = make_user(email="cliente@exemplo.ao")

    def test_answer_is_persisted_in_the_conversation(self) -> None:
        """A pergunta e a resposta ficam no histórico da conversa."""
        client = FakeGroqClient([text_reply("A renda é 450.000 Kz por mês.")])

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        conversation = Conversation.objects.get(pk=result.conversation_id)
        self.assertEqual(conversation.status, Conversation.Status.OPEN)
        self.assertEqual(conversation.handled_by, Conversation.HandledBy.AI)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(
            list(conversation.messages.values_list("author", flat=True)),
            [Message.Author.CLIENT, Message.Author.ASSISTANT],
        )

    def test_human_matter_escalates_without_calling_the_model(self) -> None:
        """Um pedido de visita não chega ao modelo: é escalado de imediato."""
        client = FakeGroqClient([])

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            result = ask(question="Quero marcar uma visita amanhã", user=self.user)

        self.assertTrue(result.is_escalated)
        self.assertEqual(client.request_count, 0)
        conversation = Conversation.objects.get(pk=result.conversation_id)
        self.assertEqual(conversation.status, Conversation.Status.ESCALATED)
        self.assertEqual(conversation.handled_by, Conversation.HandledBy.HUMAN)

    def test_escalated_conversation_never_calls_the_model_again(self) -> None:
        """Depois de escalada, a IA deixa de responder sozinha (§2.4)."""
        client = FakeGroqClient([])

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            first = ask(question="Quero marcar visita", user=self.user)
            conversation = Conversation.objects.get(pk=first.conversation_id)
            second = ask(question="E o preço?", user=self.user, conversation=conversation)

        self.assertTrue(second.is_escalated)
        self.assertEqual(client.request_count, 0)

    def test_model_failure_degrades_without_escalating(self) -> None:
        """Uma falha do provider degrada a resposta mas não tira o cliente do nível 1."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", side_effect=RuntimeError("ligação recusada")),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        self.assertFalse(result.is_escalated)
        self.assertTrue(result.is_degraded)
        conversation = Conversation.objects.get(pk=result.conversation_id)
        self.assertEqual(conversation.status, Conversation.Status.OPEN)
        self.assertEqual(conversation.handled_by, Conversation.HandledBy.AI)

    def test_um_turno_que_entra_no_proximo_volta_a_chegar_ao_modelo(self) -> None:
        """Depois de uma falha, a conversa não fica condenada a dizer "entregue à equipa"."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", side_effect=RuntimeError("ligação recusada")),
        ):
            primeiro = ask(question="Qual é a renda?", user=self.user)

        client = FakeGroqClient([text_reply("A renda é 450.000 Kz por mês.")])
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            segundo = ask(
                question="E tem tanque de água?",
                user=self.user,
                conversation=Conversation.objects.get(pk=primeiro.conversation_id),
            )

        self.assertFalse(segundo.is_escalated)
        self.assertFalse(segundo.is_degraded)
        self.assertEqual(client.request_count, 1)
        self.assertIn("450.000 Kz", segundo.answer)

    def test_a_falha_fica_gravada_para_a_equipa(self) -> None:
        """A indisponibilidade é um problema nosso, e continua registado como tal."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", side_effect=RuntimeError("ligação recusada")),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        conversation = Conversation.objects.get(pk=result.conversation_id)
        internas = list(
            conversation.messages.filter(is_internal=True).values_list("body", flat=True)
        )
        visiveis = " ".join(
            conversation.messages.filter(is_internal=False).values_list("body", flat=True)
        )
        self.assertIn("ligação recusada", " ".join(internas))
        self.assertNotIn("ligação recusada", visiveis)

    def test_disabled_assistant_is_reported_as_degraded(self) -> None:
        """Com o nível 1 desligado a degradação é explícita, não silenciosa."""
        with override_settings(ECHILO_AI_ENABLED=False):
            result = ask(question="Qual é a renda?", user=self.user)

        self.assertTrue(result.is_degraded)
        self.assertFalse(result.is_escalated)
        conversation = Conversation.objects.get(pk=result.conversation_id)
        self.assertEqual(conversation.status, Conversation.Status.OPEN)

    def test_blank_question_never_touches_the_network(self) -> None:
        """Uma pergunta vazia devolve a saudação e não abre conversa."""
        with override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"):
            result = ask(question="   ", user=self.user)

        self.assertIsNone(result.conversation_id)
        self.assertFalse(Conversation.objects.exists())


class ChatViewTests(TestCase):
    """O widget de chat funciona para visitantes e para clientes com conta."""

    def setUp(self) -> None:
        cache.clear()
        self.url = reverse("assistant:chat_message")
        self.user = make_user(email="cliente@exemplo.ao")

    def test_window_renders_for_anonymous(self) -> None:
        """A janela de chat não exige autenticação."""
        response = self.client.get(reverse("assistant:chat"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chat")

    def test_empty_question_is_bad_request(self) -> None:
        """Uma pergunta vazia devolve 400 em vez de chamar o modelo."""
        response = self.client.post(self.url, {"question": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_anonymous_conversation_is_kept_in_session(self) -> None:
        """A conversa de um visitante fica associada à sua sessão."""
        client = FakeGroqClient([text_reply("A renda é 450.000 Kz por mês.")])

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            response = self.client.post(
                self.url,
                {"question": "Qual é a renda?"},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "450.000 Kz")
        conversation = Conversation.objects.get()
        self.assertIsNone(conversation.user)
        self.assertEqual(self.client.session["assistant.conversation_id"], conversation.pk)

    def test_session_continues_the_same_conversation(self) -> None:
        """A segunda pergunta do visitante continua a conversa anterior."""
        client = FakeGroqClient(
            [text_reply("A renda é 450.000 Kz."), text_reply("Sim, aceita pagamento anual.")]
        )

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            self.client.post(self.url, {"question": "Qual é a renda?"}, HTTP_HX_REQUEST="true")
            self.client.post(
                self.url, {"question": "Aceita anual?"}, HTTP_HX_REQUEST="true"
            )

        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.filter(author=Message.Author.CLIENT).count(), 2)

    def test_one_visitor_cannot_read_another_conversation(self) -> None:
        """Um `conversation_id` de outra pessoa é ignorado."""
        other = make_user(email="outro@exemplo.ao")
        foreign = Conversation.objects.create(user=other)
        Message.objects.create(
            conversation=foreign,
            author=Message.Author.CLIENT,
            body="Pergunta privada de outra pessoa",
        )
        client = FakeGroqClient([text_reply("Resposta nova.")])

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            self.client.force_login(self.user)
            response = self.client.post(
                self.url,
                {"question": "Olá", "conversation_id": str(foreign.pk)},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Pergunta privada")
        self.assertEqual(Message.objects.filter(conversation=foreign).count(), 1)

    def test_rate_limit_returns_429(self) -> None:
        """Exceder o limite de perguntas devolve 429 com aviso."""
        client = FakeGroqClient([text_reply("Resposta.")] * 25)

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            statuses = [
                self.client.post(
                    self.url, {"question": f"Pergunta {n}"}, HTTP_HX_REQUEST="true"
                ).status_code
                for n in range(21)
            ]

        self.assertEqual(statuses[:20], [200] * 20)
        self.assertEqual(statuses[20], 429)
        self.assertEqual(client.request_count, 20)

    def test_rate_limit_says_why_instead_of_greeting_again(self) -> None:
        """O limite de perguntas diz o que aconteceu; a saudação não explica nada."""
        client = FakeGroqClient([text_reply("Resposta.")] * 25)

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            for n in range(20):
                self.client.post(self.url, {"question": f"Pergunta {n}"}, HTTP_HX_REQUEST="true")
            response = self.client.post(
                self.url, {"question": "Mais uma"}, HTTP_HX_REQUEST="true"
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "limite de perguntas", status_code=429)
        self.assertNotContains(response, "Em que posso ajudar?", status_code=429)

    def test_get_is_not_allowed(self) -> None:
        """O endpoint de mensagem só aceita POST."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)


class ChatAppendTests(TestCase):
    """O HTMX acrescenta o turno novo; não volta a desenhar a conversa inteira.

    Trocar o `innerHTML` inteiro de #chat-body por um tecto de mensagens
    fazia a conversa encolher a cada pergunta e deixava a resposta nova fora da
    área visível. Estes testes medem o que o HTMX recebe, que é onde o defeito
    aparecia.
    """

    def setUp(self) -> None:
        cache.clear()
        self.url = reverse("assistant:chat_message")
        self.user = make_user(email="conversa@exemplo.ao")
        self.client.force_login(self.user)
        self.conversation = Conversation.objects.create(user=self.user)

    def _perguntar(self, question: str, *, after: int | None) -> object:
        client = FakeGroqClient([text_reply(f"Resposta a {question}.")])
        dados = {"question": question}
        if after is not None:
            dados["after_message_id"] = str(after)
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            return self.client.post(self.url, dados, HTTP_HX_REQUEST="true")

    def test_o_turno_novo_nao_arrasta_o_historico(self) -> None:
        """A resposta do HTMX traz o turno novo e nada do que já estava lá."""
        primeira = self._perguntar("Qual é a renda?", after=None)
        self.assertEqual(primeira.status_code, 200)
        ultima = Message.objects.order_by("id").last()

        segunda = self._perguntar("Aceita anual?", after=ultima.pk)

        self.assertEqual(segunda.status_code, 200)
        self.assertContains(segunda, "Resposta a Aceita anual?.")
        self.assertNotContains(segunda, "Resposta a Qual é a renda?.")
        self.assertNotContains(segunda, "Qual é a renda?", msg_prefix="")

    def test_a_conversa_nao_encolhe_ao_longo_dos_turnos(self) -> None:
        """Depois de vários turnos a página ainda desenha tudo o que foi dito."""
        anterior = 0
        for n in range(4):
            resposta = self._perguntar(f"Pergunta {n}", after=anterior)
            self.assertEqual(resposta.status_code, 200)
            anterior = Message.objects.order_by("id").last().pk

        janela = self.client.get(reverse("assistant:chat"))

        for n in range(4):
            self.assertContains(janela, f"Resposta a Pergunta {n}.")

    def test_sem_o_id_da_ultima_mensagem_troca_tudo(self) -> None:
        """Sem `after_message_id` o servidor redesenha a janela, e não duplica."""
        self._perguntar("Qual é a renda?", after=None)

        resposta = self._perguntar("Aceita anual?", after=None)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Resposta a Aceita anual?.")
        self.assertContains(resposta, "Resposta a Qual é a renda?.")

    def test_um_id_invalido_nao_da_500(self) -> None:
        """Um campo escrito à mão com lixo não pode deitar a página abaixo."""
        resposta = self._perguntar("Qual é a renda?", after="isto não é um id")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Resposta a Qual é a renda?.")

    def test_a_saudacao_so_aparece_no_primeiro_render(self) -> None:
        """Um apêndice vazio não volta a desenhar a saudação no meio da conversa."""
        ultima = self._perguntar("Qual é a renda?", after=None)
        self.assertNotContains(ultima, "Em que posso ajudar?")

    def test_a_pagina_anuncia_o_ponto_de_retomada(self) -> None:
        """O formulário diz ao servidor o que o cliente já viu."""
        self._perguntar("Qual é a renda?", after=None)

        pagina = self.client.get(reverse("assistant:chat"))
        html = pagina.content.decode()

        ultima = Message.objects.order_by("id").last().pk
        self.assertIn('hx-swap="beforeend"', html)
        self.assertIn(f'value="{ultima}"', html)

    def test_a_bolha_carrega_o_seu_proprio_id(self) -> None:
        """É do DOM que o JavaScript lê o id, logo a bolha tem de o trazer."""
        self._perguntar("Qual é a renda?", after=None)

        pagina = self.client.get(reverse("assistant:chat"))

        for mensagem in Message.objects.all():
            self.assertContains(
                pagina, f'data-message-id="{mensagem.pk}"', html=False
            )

    def test_uma_mensagem_interna_nunca_chega_ao_swap(self) -> None:
        """O motivo da equipa é excluído na consulta, não só escondido no HTML.

        O partial tem também uma guarda, e é ela que esconde a bolha. Se a
        exclusão da consulta se perdesse, a conversa continuava escondendo o
        diagnóstico e o defeito ficava à espera de alguém trocar o template —
        por isso a medição é sobre as linhas devolvidas, não sobre o HTML.
        """
        ultima = self._perguntar("Qual é a renda?", after=None)
        self.assertEqual(ultima.status_code, 200)
        anterior = Message.objects.order_by("id").last().pk
        interna = Message.objects.create(
            conversation=self.conversation,
            author=Message.Author.STAFF,
            body="Erro técnico no assistente.",
            is_escalation_notice=True,
            is_internal=True,
        )
        self._perguntar("E agora?", after=anterior)

        devolvidas = messages_after(self.conversation, after_id=anterior)

        self.assertNotIn(interna.pk, [mensagem.pk for mensagem in devolvidas])
        self.assertTrue(devolvidas, "o turno novo tem de vir mesmo com a interna a meio")


class ConversaNaoSaiDaDivTests(TestCase):
    """A conversa vive dentro de #chat-body e em mais lado nenhum.

    `messages` é o nome que o `base.html` dá aos avisos de flash do Django. Uma
    view que põe a conversa sob esse nome não dá erro: o `base.html` desenha-a
    como uma lista de avisos no topo do `<main>`, e cada mensagem aparece duas
    vezes — uma como caixa azul antes do cabeçalho da secção, outra como bolha.
    """

    def setUp(self) -> None:
        cache.clear()
        self.user = make_user(email="div@exemplo.ao")
        self.client.force_login(self.user)
        self.conversation = Conversation.objects.create(user=self.user)
        Message.objects.create(
            conversation=self.conversation,
            author=Message.Author.CLIENT,
            body="Qual é a renda do T3 no Kilamba?",
        )
        Message.objects.create(
            conversation=self.conversation,
            author=Message.Author.ASSISTANT,
            body="Está a 450.000 Kz por mês.",
        )

    def test_a_mensagem_aparece_uma_vez_so_na_pagina(self) -> None:
        """A conversa não é desenhada também como aviso de flash."""
        pagina = self.client.get(reverse("assistant:chat"))
        html = pagina.content.decode()

        self.assertEqual(html.count("Qual é a renda do T3 no Kilamba?"), 1)
        self.assertEqual(html.count("Está a 450.000 Kz por mês."), 1)

    def test_a_pagina_nao_desenha_caixas_de_aviso(self) -> None:
        """Sem aviso nenhum, o topo do `<main>` fica limpo."""
        pagina = self.client.get(reverse("assistant:chat"))

        self.assertNotContains(pagina, 'class="alert')

    def test_o_flash_continua_a_funcionar_com_conversa_no_ecra(self) -> None:
        """Arranjar o nome da chave não pode custar os avisos de todas as páginas.

        O defeito tinha uma segunda cara: com `messages` ocupado pela conversa,
        um aviso de flash deixava de aparecer. Por isso o teste põe os dois no
        mesmo ecrã.
        """
        request = RequestFactory().get(reverse("assistant:chat"))
        request.user = self.user
        request.session = self.client.session
        MessageMiddleware(lambda _request: None).process_request(request)
        add_message(request, constants.INFO, "Perfil alterado.")

        html = chat_window(request).content.decode()

        self.assertIn("Perfil alterado.", html)
        self.assertIn("Qual é a renda do T3 no Kilamba?", html)
        self.assertIn('class="alert', html)


class InternalEscalationReasonTests(TestCase):
    """O motivo técnico da escalação é da equipa e não entra na conversa do cliente."""

    def setUp(self) -> None:
        cache.clear()
        self.user = make_user(email="escala@exemplo.ao")

    def test_a_falha_do_modelo_nao_e_mostrada_ao_cliente(self) -> None:
        """A degradação avisa o cliente; o diagnóstico fica na base de dados."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", side_effect=RuntimeError("ligação recusada")),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        self.assertTrue(result.is_degraded)
        conversation = Conversation.objects.get(pk=result.conversation_id)
        visiveis = " ".join(
            conversation.messages.filter(is_internal=False).values_list("body", flat=True)
        )
        internas = list(
            conversation.messages.filter(is_internal=True).values_list("body", flat=True)
        )
        self.assertIn("Nível 1 indisponível", " ".join(internas))
        self.assertIn("momentaneamente indisponível", visiveis)

    def test_a_degradacao_nao_promete_a_equipa(self) -> None:
        """Ninguém foi avisado, por isso a resposta não diz que a equipa foi avisada."""
        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", side_effect=RuntimeError("ligação recusada")),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        self.assertNotIn("equipa", result.answer.lower())

    def test_um_403_do_fornecedor_e_indisponibilidade_prevista(self) -> None:
        """O `403` da Groq é o caminho previsto, não um erro técnico."""
        client = FakeRefusingClient(PermissionError("Access denied. Please check your network settings."))

        with (
            override_settings(ECHILO_AI_ENABLED=True, ECHILO_AI_API_KEY="test-key"),
            patch("groq.Groq", return_value=client),
        ):
            result = ask(question="Qual é a renda?", user=self.user)

        self.assertTrue(result.is_degraded)
        self.assertFalse(result.is_escalated)
        self.assertEqual(client.request_count, 1)
        conversation = Conversation.objects.get(pk=result.conversation_id)
        self.assertEqual(conversation.status, Conversation.Status.OPEN)
        internas = " ".join(
            conversation.messages.filter(is_internal=True).values_list("body", flat=True)
        )
        self.assertIn("Access denied", internas)

    def test_a_mensagem_do_assistente_desenhada_nao_traz_o_diagnostico(self) -> None:
        """O partial da conversa não desenha uma mensagem interna."""
        conversation = Conversation.objects.create(user=self.user)
        Message.objects.create(
            conversation=conversation,
            author=Message.Author.STAFF,
            body="Erro técnico no assistente.",
            is_escalation_notice=True,
            is_internal=True,
        )
        Message.objects.create(
            conversation=conversation,
            author=Message.Author.ASSISTANT,
            body="Resposta que o cliente deve ler.",
        )

        html = render_to_string(
            "assistant/_messages.html",
            {"chat_messages": list(conversation.messages.all())},
        )

        self.assertIn("Resposta que o cliente deve ler.", html)
        self.assertNotIn("Erro técnico no assistente.", html)

    def test_o_historico_enviado_ao_modelo_nao_traz_mensagens_internas(self) -> None:
        """O modelo não aprende a responder com o texto escrito para a equipa."""
        conversation = Conversation.objects.create(user=self.user)
        Message.objects.create(
            conversation=conversation,
            author=Message.Author.STAFF,
            body="Erro técnico no assistente.",
            is_escalation_notice=True,
            is_internal=True,
        )
        Message.objects.create(
            conversation=conversation,
            author=Message.Author.CLIENT,
            body="Qual é a renda?",
        )

        historico = _history_for(conversation)

        conteudos = [mensagem["content"] for mensagem in historico]
        self.assertNotIn("Erro técnico no assistente.", conteudos)
        self.assertEqual(conteudos, ["Qual é a renda?"])

    def test_a_escalacao_mantem_o_motivo_e_o_aviso_separados(self) -> None:
        """A conversa já escalada avisa o cliente e deixa o motivo à equipa."""
        conversation = Conversation.objects.create(user=self.user)

        with override_settings(ECHILO_AI_ENABLED=False):
            primeira = ask(
                question="Quero marcar uma visita para amanhã",
                user=self.user,
                conversation=conversation,
            )
        self.assertTrue(primeira.is_escalated)

        with override_settings(ECHILO_AI_ENABLED=False):
            segunda = ask(question="suprou", user=self.user, conversation=conversation)
        self.assertTrue(segunda.is_escalated)

        visiveis = list(
            conversation.messages.filter(is_internal=False).values_list("body", flat=True)
        )
        internas = list(
            conversation.messages.filter(is_internal=True).values_list("body", flat=True)
        )
        self.assertIn("A tua mensagem foi entregue à equipa do Echilo.", " ".join(visiveis))
        self.assertIn(
            "Mensagem recebida numa conversa já em acompanhamento humano.", internas
        )
