"""Serviços do atendimento: captação, visitas, ofertas e escalação."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.ratelimit import check_rate_limit
from apps.properties.models import Property

from .models import Conversation, Lead, Message, Offer, VisitRequest

User = get_user_model()

INTAKE_RATE_SCOPE = "concierge.owner_intake"

# Quantas mensagens o HTMX acrescenta de uma vez. É um tecto de segurança contra
# um cliente com o `after_id` atrasado a puxar a conversa inteira, não o número
# de mensagens de um turno normal, que são sempre duas ou três.
HISTORY_APPEND_LIMIT = 20


def recent_messages(
    conversation: Conversation,
    *,
    limit: int,
    include_internal: bool = False,
) -> list[Message]:
    """Devolve as últimas mensagens por ordem cronológica.

    Django não aceita indexação negativa em QuerySets, por isso o corte é feito
    pela ordenação inversa e revertido no fim. As mensagens internas ficam de
    fora por omissão: quem lê isto é o cliente, e o motivo técnico da escalação
    não é assunto dele.
    """
    messages = conversation.messages.order_by("-id")
    if not include_internal:
        messages = messages.filter(is_internal=False)
    messages = list(messages[:limit])
    messages.reverse()
    return messages


def messages_after(
    conversation: Conversation,
    *,
    after_id: int,
    limit: int = HISTORY_APPEND_LIMIT,
) -> list[Message]:
    """Devolve o que apareceu depois de um `id`, para acrescentar ao chat.

    O HTMX acrescenta estas mensagens ao fim da conversa em vez de trocar o
    `innerHTML` do contentor inteiro. Sem isto, responder substituía as 40
    mensagens que a página tinha desenhado pelas 6 últimas e a conversa
    perdia-se a cada turno.
    """
    return list(
        conversation.messages.filter(id__gt=after_id, is_internal=False).order_by("id")[:limit]
    )


class IntakeThrottled(ValidationError):
    """Sinaliza que o formulário de adesão foi submetido vezes a mais."""


def create_owner_intake(
    *,
    full_name: str,
    phone: str,
    email: str = "",
    purpose: str = "",
    message: str = "",
    request: object | None = None,
) -> Lead:
    """Etapa 1: transforma um contacto directo num `Lead` de captação (§2.1)."""
    if request is not None:
        limit = check_rate_limit(request, scope=INTAKE_RATE_SCOPE, limit=4, window=1800)
        if not limit.allowed:
            raise IntakeThrottled("Recebemos muitos contactos deste dispositivo. Tente mais tarde.")
    return Lead.objects.create(
        full_name=full_name.strip(),
        phone=phone.strip(),
        email=email.strip().lower(),
        lead_type=Lead.Type.OWNER_INTAKE,
        purpose_interest=purpose,
        message=message.strip(),
    )


@transaction.atomic
def request_visit(
    *,
    prop: Property,
    user: User,
    scheduled_for: datetime,
    notes: str = "",
) -> VisitRequest:
    """Regista o pedido de visita; a confirmação fica sempre com a equipa (§2.10)."""
    if scheduled_for < timezone.now():
        raise ValidationError("Escolha uma data futura para a visita.")
    if prop.status != Property.Status.PUBLISHED:
        raise ValidationError("Este imóvel não está disponível para visitas.")
    visit = VisitRequest(
        property=prop,
        requested_by=user,
        scheduled_for=scheduled_for,
        notes=notes.strip(),
    )
    visit.full_clean(exclude=["lead"])
    visit.save()

    conversation = Conversation.objects.create(
        user=user,
        lead=None,
        property_interest=prop,
        status=Conversation.Status.ESCALATED,
        handled_by=Conversation.HandledBy.HUMAN,
    )
    Message.objects.create(
        conversation=conversation,
        author=Message.Author.STAFF,
        body=(
            "Recebemos o seu pedido de visita. A equipa entra em contacto para confirmar "
            "o dia e a hora."
        ),
        is_escalation_notice=True,
    )
    return visit


@transaction.atomic
def submit_offer(
    *,
    prop: Property,
    user: User,
    amount: Decimal,
    message: str = "",
) -> Offer:
    """Regista a proposta formal e escala-a para análise humana (§2.5)."""
    if prop.purpose != Property.Purpose.SALE:
        raise ValidationError("Apenas imóveis à venda aceitam proposta.")
    offer = Offer(
        property=prop,
        submitted_by=user,
        amount=amount,
        currency=prop.currency,
        message=message.strip(),
    )
    offer.full_clean()
    offer.save()
    return offer


@transaction.atomic
def get_or_create_conversation(
    *,
    user: User | None,
    property_interest: Property | None = None,
) -> Conversation:
    """Reutiliza a conversa aberta do cliente para não fragmentar o histórico."""
    existing = (
        Conversation.objects.filter(
            user=user,
            property_interest=property_interest,
            status__in=[Conversation.Status.OPEN, Conversation.Status.ESCALATED],
        )
        .order_by("-updated_at")
        .first()
        if user is not None
        else None
    )
    if existing is not None:
        return existing
    return Conversation.objects.create(
        user=user,
        property_interest=property_interest,
    )


def escalate_conversation(
    *,
    conversation: Conversation,
    reason: str,
    agent: User | None = None,
    notice: str | None = None,
) -> Conversation:
    """Move o atendimento para a equipa humana e regista o motivo (§2.4).

    O `reason` é para a equipa e fica interno; o `notice`, quando existe, é a
    frase que o cliente lê. São coisas diferentes, e usar um só campo escrevia o
    diagnóstico dentro da conversa.
    """
    if conversation.status == Conversation.Status.RESOLVED:
        raise ValidationError("Esta conversa já foi encerrada.")
    conversation.status = Conversation.Status.ESCALATED
    conversation.handled_by = Conversation.HandledBy.HUMAN
    if agent is not None:
        conversation.assigned_to = agent
    conversation.save(update_fields=["status", "handled_by", "assigned_to", "updated_at"])
    Message.objects.create(
        conversation=conversation,
        author=Message.Author.STAFF,
        body=reason,
        is_escalation_notice=True,
        is_internal=True,
    )
    if notice:
        Message.objects.create(
            conversation=conversation,
            author=Message.Author.STAFF,
            body=notice,
            is_escalation_notice=True,
        )
    return conversation
