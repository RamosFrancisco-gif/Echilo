"""Serviços do atendimento: captação, visitas, ofertas e escalação."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.money import kwanza
from apps.core.ratelimit import check_rate_limit
from apps.core.tempo import quando_para_o_cliente
from apps.properties.models import Property

from .models import Conversation, Lead, Message, Offer, VisitRequest

User = get_user_model()

INTAKE_RATE_SCOPE = "concierge.owner_intake"

# Quantas mensagens o HTMX acrescenta de uma vez. É um tecto de segurança contra
# um cliente com o `after_id` atrasado a puxar a conversa inteira, não o número
# de mensagens de um turno normal, que são sempre duas ou três.
HISTORY_APPEND_LIMIT = 20

# O que o cliente lê quando a equipa decide uma visita. `NO_SHOW` não tem frase:
# é um registo de que o cliente não apareceu, e o cliente sabe que não apareceu.
# Dizer-lho por escrito é dizê-lo da pior maneira possível, e deixa na conversa
# uma frase que a equipa vai ter de explicar ao telefone.
VISIT_NOTICE: dict[str, str] = {
    VisitRequest.Status.CONFIRMED: (
        "A sua visita está confirmada para {quando}. A morada exacta segue pelo "
        "canal que combinámos."
    ),
    VisitRequest.Status.DECLINED: (
        "Não foi possível confirmar a visita de {quando}. Motivo: {motivo}"
    ),
    VisitRequest.Status.COMPLETED: (
        "A visita de {quando} ficou registada como realizada. Obrigado por ter "
        "passado por nós."
    ),
}

# A pergunta que segue a visita, e só a imóveis à venda: uma contraproposta sobre
# um imóvel para arrendar não tem o que pedir, e o `submit_offer()` recusa-a.
OFFER_QUESTION = (
    "Se gostou do imóvel, pode enviar uma proposta formal de preço. Diga-nos o "
    "valor que pretende e o motivo — responde um agente da equipa, e a negociação "
    "nunca é automática."
)

# O lembrete que o cliente leva antes da visita, e o que a equipa recebe quando o
# cliente não respondeu. As janelas vivem aqui e não em cada consulta pelo mesmo
# motivo de `LIMITE_PEDIDO_MB`: a janela é uma decisão de produto, e um número
# repetido diverge sem dar erro.
VISIT_REMINDER_HOURS = 24
RECONFIRMATION_WINDOW_HOURS = 48


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

    conversation = get_or_create_conversation(user=user, property_interest=prop)
    if conversation.status != Conversation.Status.ESCALATED:
        # A conversa pode já estar aberta por uma pergunta ao assistente. Um pedido
        # de visita é assunto de equipa por definição, e deixá-la no nível 1 fazia o
        # cliente receber a confirmação no nível 1 — a dizer que a equipa ia tratar
        # de uma coisa que o nível 1 já julgava estar a tratar.
        escalate_conversation(
            conversation=conversation,
            reason=(
                f"Pedido de visita para {prop.reference}, "
                f"{quando_para_o_cliente(scheduled_for)}."
            ),
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
    # A proposta precisa de resposta humana (§2.5) e a resposta não tem onde
    # chegar sem conversa: antes disto o cliente submetia e ficava sem nenhuma
    # confirmação de que alguém tinha lido aquilo. A conversa que se abre é a
    # escala, e é a mesma que a contraproposta e o aceite vão usar.
    escalate_conversation(
        conversation=get_or_create_conversation(user=user, property_interest=prop),
        reason=(
            f"Proposta formal de {kwanza(amount)} para {prop.reference}, "
            "aguarda análise de um agente."
        ),
        notice=(
            f"Recebemos a sua proposta de {kwanza(amount)}. Um agente da equipa "
            "vai analisar e responde-lhe — a negociação nunca é automática."
        ),
    )
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


def note_conversation(*, conversation: Conversation, note: str) -> Message:
    """Deixa um registo interno sem mexer no estado da conversa.

    Existe porque uma falha nossa não é assunto da equipa humana. Escalar por
    causa de um `403` do fornecedor punia quem perguntou: a conversa passava a
    responder "entregue à equipa" para sempre, mesmo depois de o fornecedor
    voltar, e a fila da equipa enchia-se de conversas cujo único conteúdo era
    "oi". A nota fica gravada para quem abrir a conversa e para o registo, e o
    cliente continua no nível 1.
    """
    return Message.objects.create(
        conversation=conversation,
        author=Message.Author.STAFF,
        body=note,
        is_internal=True,
    )


# ---------------------------------------------------------------------------
# As acções da equipa
# ---------------------------------------------------------------------------

# O que o cliente lê quando a equipa decide uma proposta. `UNDER_REVIEW` não tem
# frase: passar a proposta a "em análise" é o agente a organizar-se, e dizer isso ao
# cliente é dizer que ele está à espera sem lhe dar nada. `COUNTERED` também não
# vem por aqui — quem escreve a contraproposta é `counter_offer()`, e ela tem de
# escrever o valor que a equipa propôs, que é a única coisa que importa.
OFFER_NOTICE: dict[str, str] = {
    Offer.Status.ACCEPTED: "A sua proposta de {valor} foi aceite. Entraremos em contacto.",
    Offer.Status.REJECTED: "A sua proposta de {valor} não foi aceite. Motivo: {motivo}",
    Offer.Status.WITHDRAWN: "A sua proposta de {valor} ficou registada como retirada.",
}

# O lembrete que o cliente recebe antes da visita. É uma frase própria e não a de
# confirmação: quem já foi confirmado e é lembrado na véspera precisa de saber que
# a visita continua de pé, e repetir a confirmação faz o cliente desconfiar de que
# alguma coisa mudou.
VISIT_REMINDER = (
    "Lembrete: a sua visita está marcada para {quando}. Se precisar de remarcar, "
    "diga-nos por aqui ou ligue."
)


def _notify_client(*, user: User, prop: Property, body: str) -> Message:
    """Escreve o aviso do sistema na conversa do cliente sobre aquele imóvel.

    Reaproveita a conversa aberta em vez de criar outra. `request_visit()` abria uma
    nova sem olhar, e dois pedidos do mesmo cliente sobre o mesmo imóvel ficavam
    em duas conversas: o aviso da confirmação ia para uma e o aviso da segunda
    visita para a outra, e o cliente via metade do que a equipa lhe disse.
    """
    conversation = get_or_create_conversation(user=user, property_interest=prop)
    return Message.objects.create(
        conversation=conversation,
        author=Message.Author.STAFF,
        body=body,
        is_escalation_notice=True,
    )


@transaction.atomic
def decide_visit(
    *,
    visit: VisitRequest,
    target: str,
    actor: User,
    reason: str = "",
) -> VisitRequest:
    """Aplica a decisão da equipa sobre uma visita e avisa o cliente (§2.10).

    O `reason` é obrigatório a recusar e é o que o cliente lê; nas outras
    decisões é a nota interna da equipa. O modelo só grava o motivo em
    `decline_reason` quando recusa: um campo chamado "motivo da recusa" com
    notas de conclusão é um campo a mentir sobre o que guarda.
    """
    visit.transition_to(target, actor=actor, reason=reason)

    frase = VISIT_NOTICE.get(target)
    if frase is not None:
        corpo = frase.format(
            quando=quando_para_o_cliente(visit.scheduled_for),
            motivo=visit.decline_reason,
        )
        if target == VisitRequest.Status.COMPLETED and (
            visit.property.purpose == Property.Purpose.SALE
        ):
            corpo = f"{corpo}\n\n{OFFER_QUESTION}"
        _notify_client(
            user=visit.requested_by, prop=visit.property, body=corpo
        )
    if reason.strip() and target != VisitRequest.Status.DECLINED:
        note_conversation(
            conversation=get_or_create_conversation(
                user=visit.requested_by, property_interest=visit.property
            ),
            note=f"Visita — {reason.strip()}",
        )
    return visit


@transaction.atomic
def counter_offer(
    *,
    offer: Offer,
    amount: Decimal,
    actor: User,
    message: str = "",
) -> Offer:
    """Regista a contraproposta da equipa e leva a original a `COUNTERED`.

    Uma contraproposta é uma proposta nova e não um estado novo: o valor que a equipa
    propôs tem de existir como registo, e o estado sozinho não o guarda. Por isso
    `answer_offer()` recusa `COUNTERED` como destino — chegar lá por uma
    transição deixaria a negociação com duas propostas e nenhum preço da equipa.
    """
    if offer.is_counter():
        raise ValidationError("Uma contraproposta não leva outra contraproposta.")
    if not offer.is_active():
        raise ValidationError("Esta proposta já está encerrada.")

    contra = Offer(
        property=offer.property,
        submitted_by=actor,
        parent=offer,
        amount=amount,
        currency=offer.currency,
        message=message.strip(),
    )
    contra.full_clean()
    contra.save()
    offer.transition_to(Offer.Status.COUNTERED, actor=actor, notes=message)
    _notify_client(
        user=offer.submitted_by,
        prop=offer.property,
        body=(
            f"A sua proposta de {kwanza(offer.amount)} foi contraposta: "
            f"{kwanza(contra.amount)}. Resposta: {contra.message or 'sem justificação'}."
        ),
    )
    return contra


@transaction.atomic
def answer_offer(
    *,
    offer: Offer,
    target: str,
    actor: User,
    notes: str = "",
) -> Offer:
    """Aplica a resposta da equipa a uma proposta e avisa o cliente (§2.5).

    O aviso vai para quem a proposta está a negociar — `negotiation_with()`, com
    parêntesis. Sem eles o que viajava era o método, e o aviso ia parar a uma
    conversa de um utilizador que não existe. `COUNTERED` não entra por aqui:
    chegar lá por transição deixava a negociação sem o preço da equipa, que é o
    que `counter_offer()` existe para registar.
    """
    if target == Offer.Status.COUNTERED:
        raise ValidationError("Uma contraproposta cria-se com `counter_offer()`.")
    offer.transition_to(target, actor=actor, notes=notes)

    frase = OFFER_NOTICE.get(target)
    if frase is None:
        return offer
    _notify_client(
        user=offer.negotiation_with(),
        prop=offer.property,
        body=frase.format(
            valor=kwanza(offer.amount),
            motivo=(offer.response_notes or "a equipa não detalhou").strip(),
        ),
    )
    return offer


@transaction.atomic
def advance_lead(*, lead: Lead, target: str, actor: User) -> Lead:
    """Move o contacto na máquina de estados e deixa-lhe um responsável (§2.9).

    O responsável só é posto se ainda não houver nenhum. Um contacto que volta de
    `DISQUALIFIED` para `NEW` já tem quem o tratou, e trocar-lhe o responsável a
    cada passagem apaga quem andava a tratar daquilo.
    """
    if lead.assigned_to_id is None:
        lead.assigned_to = actor
        lead.save(update_fields=["assigned_to", "updated_at"])
    lead.transition_to(target)
    return lead


# ---------------------------------------------------------------------------
# O trabalho de fundo
# ---------------------------------------------------------------------------


def visits_needing_reminder(
    *,
    now: datetime | None = None,
    hours: int = VISIT_REMINDER_HOURS,
) -> QuerySet[VisitRequest]:
    """Devolve as visitas confirmadas que entram na janela e ainda não foram lembradas.

    O filtro `reminded_at__isnull=True` é o que torna isto idempotente: o comando
    que acorda sozinho vai ser corrido quantas vezes o agendador quiser, e sem o
    campo o cliente levava o mesmo lembrete a cada hora até a visita deixar de estar
    marcada.
    """
    agora = now or timezone.now()
    return (
        VisitRequest.objects.filter(
            status=VisitRequest.Status.CONFIRMED,
            reminded_at__isnull=True,
            scheduled_for__gt=agora,
            scheduled_for__lte=agora + timedelta(hours=hours),
        )
        .select_related("property", "requested_by")
        .order_by("scheduled_for")
    )


def visits_needing_reconfirmation(
    *,
    now: datetime | None = None,
    hours: int = RECONFIRMATION_WINDOW_HOURS,
) -> QuerySet[VisitRequest]:
    """Devolve os pedidos por confirmar cuja data já está perto e ninguém tratou.

    O filtro é `handled_at__isnull=True` e não `reconfirmation_flagged_at__isnull`:
    o que retira o pedido da fila é a equipa responder, e a marca do que o trabalho
    de fundo já viu é um registo, não o filtro. Se a marca fosse o filtro, um pedido
    que a equipa tratou voltava a aparecer à fila assim que o carimbo não
    coincidisse com o estado — e a fila ensinaria a equipa a ignorar o que aparece.
    """
    agora = now or timezone.now()
    return (
        VisitRequest.objects.filter(
            status=VisitRequest.Status.PENDING,
            handled_at__isnull=True,
            scheduled_for__lte=agora + timedelta(hours=hours),
        )
        .select_related("property", "requested_by")
        .order_by("scheduled_for")
    )


@transaction.atomic
def due_for_scheduling(
    *,
    now: datetime | None = None,
    reminder_hours: int = VISIT_REMINDER_HOURS,
    reconfirmation_hours: int = RECONFIRMATION_WINDOW_HOURS,
) -> dict[str, int]:
    """Avisa os clientes e marca a reconfirmação; devolve o que fez.

    Idempotente por construção, e é o requisito de um comando que um agendador
    externo vai correr sem que ninguém saiba quantas vezes. Correr duas vezes seguidas
    avisa uma vez e devolve zero na segunda — o que o teste mede, porque um comando
    que duplica lembretes é pior do que um comando que não existe.
    """
    agora = now or timezone.now()
    lembretes = 0
    for visita in list(visits_needing_reminder(now=agora, hours=reminder_hours)):
        _notify_client(
            user=visita.requested_by,
            prop=visita.property,
            body=VISIT_REMINDER.format(
                quando=quando_para_o_cliente(visita.scheduled_for, agora=agora)
            ),
        )
        visita.reminded_at = agora
        visita.save(update_fields=["reminded_at"])
        lembretes += 1

    reconfirmacoes = 0
    for visita in list(
        visits_needing_reconfirmation(now=agora, hours=reconfirmation_hours)
    ):
        visita.reconfirmation_flagged_at = agora
        visita.save(update_fields=["reconfirmation_flagged_at"])
        reconfirmacoes += 1

    return {"lembretes": lembretes, "reconfirmacoes": reconfirmacoes}
