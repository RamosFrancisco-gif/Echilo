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


def recent_messages(conversation: Conversation, *, limit: int) -> list[Message]:
    """Devolve as últimas mensagens por ordem cronológica.

    Django não aceita indexação negativa em QuerySets, por isso o corte é feito
    pela ordenação inversa e revertido no fim.
    """
    messages = list(conversation.messages.order_by("-id")[:limit])
    messages.reverse()
    return messages


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
) -> Conversation:
    """Move o atendimento para a equipa humana e regista o aviso na conversa (§2.4)."""
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
    )
    return conversation
