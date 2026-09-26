"""Motor do atendimento de nível 1, com persistência no histórico de conversas."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.concierge.models import Conversation, Message
from apps.concierge.services import (
    escalate_conversation,
    get_or_create_conversation,
    recent_messages,
)
from apps.properties.models import Property

from .prompts import FALLBACK_UNKNOWN, GREETING, build_system_prompt
from .provider import AssistantReply, AssistantUnavailable, GroqProvider, should_escalate

logger = logging.getLogger(__name__)

User = get_user_model()

AI_RATE_SCOPE = "assistant.message"
MAX_TURNS = 20


@dataclass(frozen=True)
class TurnResult:
    """Resultado de um turno de conversa para apresentação na interface."""

    answer: str
    conversation_id: int | None
    is_escalated: bool
    is_degraded: bool = False


def _history_for(conversation: Conversation | None) -> list[dict[str, str]]:
    """Converte o histórico guardado no formato de mensagens exigido pelo modelo."""
    if conversation is None:
        return []
    mapping = {
        Message.Author.CLIENT: "user",
        Message.Author.STAFF: "assistant",
        Message.Author.ASSISTANT: "assistant",
    }
    return [
        {"role": mapping[message.author], "content": message.body}
        for message in recent_messages(conversation, limit=MAX_TURNS)
    ]


def ask(
    *,
    question: str,
    user: User | None = None,
    property_interest: Property | None = None,
    conversation: Conversation | None = None,
) -> TurnResult:
    """Processa uma pergunta do cliente e persiste o turno na conversa."""
    question = question.strip()
    if not question:
        return TurnResult(answer=GREETING, conversation_id=None, is_escalated=False)

    if conversation is None:
        conversation = get_or_create_conversation(
            user=user,
            property_interest=property_interest,
        )

    Message.objects.create(conversation=conversation, author=Message.Author.CLIENT, body=question)

    if conversation.status == Conversation.Status.ESCALATED:
        escalate_conversation(
            conversation=conversation,
            reason=(
                "A tua mensagem foi entregue à equipa do Echilo. Como o assunto já está "
                "em acompanhamento humano, a partir daqui és atendido por eles."
            ),
            agent=None,
        )
        return TurnResult(
            answer=(
                "A tua mensagem foi entregue à equipa do Echilo. Como o assunto já está "
                "em acompanhamento humano, a partir daqui és atendido por eles."
            ),
            conversation_id=conversation.pk,
            is_escalated=True,
        )

    direct_escalation, reason = should_escalate(question)
    if direct_escalation:
        return _persist_escalation(conversation=conversation, reason=reason)

    try:
        reply = GroqProvider().complete(
            system_prompt=build_system_prompt(),
            history=_history_for(conversation),
            question=question,
        )
    except AssistantUnavailable:
        logger.info("Assistente indisponível: a usar resposta de recurso.")
        return _persist_reply(
            conversation=conversation,
            content=FALLBACK_UNKNOWN,
            is_escalated=True,
            is_degraded=True,
            reason="Assistente temporariamente indisponível.",
        )
    except Exception:  # noqa: BLE001 — nunca deixar o chat quebrar por erro do provider.
        logger.exception("Falha inesperada ao contactar o modelo.")
        return _persist_reply(
            conversation=conversation,
            content=(
                "Tive um problema técnico a responder. Já avisei a equipa, que pode "
                "continuar o atendimento contigo."
            ),
            is_escalated=True,
            is_degraded=True,
            reason="Erro técnico no assistente.",
        )

    return _persist_reply(
        conversation=conversation,
        content=reply.content or FALLBACK_UNKNOWN,
        is_escalated=reply.should_escalate,
        reason=reply.escalated_reason,
    )


@transaction.atomic
def _persist_reply(
    *,
    conversation: Conversation,
    content: str,
    is_escalated: bool,
    reason: str,
    is_degraded: bool = False,
) -> TurnResult:
    """Grava a resposta do assistente e, se necessário, escala para a equipa."""
    Message.objects.create(
        conversation=conversation,
        author=Message.Author.ASSISTANT,
        body=content,
    )
    if is_escalated:
        escalate_conversation(
            conversation=conversation,
            reason=reason or "Assunto encaminhado para a equipa do Echilo.",
            agent=None,
        )
    return TurnResult(
        answer=content,
        conversation_id=conversation.pk,
        is_escalated=is_escalated,
        is_degraded=is_degraded,
    )


@transaction.atomic
def _persist_escalation(*, conversation: Conversation, reason: str) -> TurnResult:
    """Regista a escalação imediata sem chamar o modelo."""
    message = (
        "Esse assunto é tratado directamente pela equipa do Echilo, que entra em "
        "contacto contigo para resolver. Já encaminhei a tua mensagem."
    )
    Message.objects.create(
        conversation=conversation,
        author=Message.Author.ASSISTANT,
        body=message,
    )
    escalate_conversation(
        conversation=conversation,
        reason=reason or "Pedido de acompanhamento humano.",
        agent=None,
    )
    return TurnResult(answer=message, conversation_id=conversation.pk, is_escalated=True)
