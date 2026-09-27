"""Views do assistente: widget de chat com HTMX e a resposta parcial."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.concierge.models import Conversation
from apps.concierge.services import messages_after, recent_messages
from apps.core.ratelimit import check_rate_limit

from .prompts import GREETING
from .services import ask
from .tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

AI_RATE_SCOPE = "assistant.message"
SESSION_CONVERSATION_KEY = "assistant.conversation_id"

# Quantas mensagens a janela inicial desenha. É o mesmo número que o histórico
# enviado ao modelo em MAX_TURNS, e é o que fica no ecrã: a partir daqui o HTMX
# acrescenta, por isso uma janela mais pequena que MAX_TURNS cortava conversa
# que o próprio assistente ainda sabe.
HISTORY_WINDOW = 40


def _active_conversation(request: HttpRequest) -> Conversation | None:
    """Recupera a conversa do visitante, autenticado ou não, sem vazar a de outro."""
    if request.user.is_authenticated:
        return (
            Conversation.objects.filter(
                user=request.user,
                status__in=[Conversation.Status.OPEN, Conversation.Status.ESCALATED],
            )
            .order_by("-updated_at")
            .first()
        )

    stored_id = request.session.get(SESSION_CONVERSATION_KEY)
    if not stored_id:
        return None
    return Conversation.objects.filter(
        pk=stored_id,
        user__isnull=True,
        status__in=[Conversation.Status.OPEN, Conversation.Status.ESCALATED],
    ).first()


def _posted_id(raw: str | None) -> int | None:
    """Lê um id vindo do formulário, devolvendo `None` ao que não é um id.

    O campo é escrito pelo nosso JavaScript, mas um `POST` feito à mão não passa
    por ele. Deixar o `int()` levantar dá um 500 por causa de um campo de
    formulário, e o caminho que o resolve é voltar ao render completo.
    """
    try:
        value = int(raw or "")
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def chat_window(request: HttpRequest) -> HttpResponse:
    """Renderiza a janela inicial do chat com a saudação do assistente."""
    conversation = _active_conversation(request)
    if conversation is not None and not request.user.is_authenticated:
        request.session[SESSION_CONVERSATION_KEY] = conversation.pk
    messages = (
        recent_messages(conversation, limit=HISTORY_WINDOW) if conversation is not None else []
    )
    return render(
        request,
        "assistant/chat.html",
        {
            "greeting": str(GREETING),
            "conversation": conversation,
            "history_window": HISTORY_WINDOW,
            "show_greeting": True,
            "messages": messages,
            "last_message_id": messages[-1].pk if messages else None,
            "is_escalated": bool(
                conversation and conversation.status == Conversation.Status.ESCALATED
            ),
        },
    )


@require_POST
def chat_message(request: HttpRequest) -> HttpResponse:
    """Recebe uma pergunta e devolve apenas o trecho novo da conversa (HTMX)."""
    question = (request.POST.get("question") or "").strip()
    if not question:
        return HttpResponseBadRequest("Indique uma pergunta.")

    limit = check_rate_limit(request, scope=AI_RATE_SCOPE, limit=20, window=300)
    if not limit.allowed:
        return render(
            request,
            "assistant/_messages.html",
            {
                "messages": [],
                "show_greeting": False,
                "notice": _(
                    "Chegámos ao limite de perguntas por alguns minutos. "
                    "Contacta a equipa pelo WhatsApp se for urgente."
                ),
            },
            status=429,
        )

    conversation = None
    conversation_id = request.POST.get("conversation_id")
    if conversation_id and request.user.is_authenticated:
        conversation = Conversation.objects.filter(
            pk=conversation_id, user=request.user
        ).first()
    elif not request.user.is_authenticated:
        conversation = _active_conversation(request)

    user = request.user if request.user.is_authenticated else None
    # O que o cliente já viu. É o que separa o que se acrescenta do que já está
    # desenhado; sem ele, cada resposta reescrevia as 40 mensagens da página
    # pelas 6 últimas e a conversa perdia-se a cada turno.
    after_id = _posted_id(request.POST.get("after_message_id"))
    result = ask(question=question, user=user, conversation=conversation)

    conversation = Conversation.objects.filter(pk=result.conversation_id).first()
    if conversation is not None and not request.user.is_authenticated:
        request.session[SESSION_CONVERSATION_KEY] = conversation.pk

    if request.headers.get("HX-Request"):
        if conversation is None:
            return JsonResponse(
                {"error": "Não foi possível recuperar a conversa."},
                status=503,
                json_dumps_params={"ensure_ascii": False},
            )
        if after_id is None:
            # O HTMX não disse o que já viu — cliente antigo, ou o campo a
            # primeira vez. Trocar tudo seria repetir o defeito; acrescentar a
            # conversa inteira sobre o que já está no ecrã duplica as mensagens.
            return render(
                request,
                "assistant/_messages.html",
                {
                    "messages": recent_messages(conversation, limit=HISTORY_WINDOW),
                    "show_greeting": False,
                    "conversation_id": result.conversation_id,
                    "is_escalated": result.is_escalated,
                },
            )
        return render(
            request,
            "assistant/_messages.html",
            {
                "messages": messages_after(conversation, after_id=after_id),
                "show_greeting": False,
                "conversation_id": result.conversation_id,
                "is_escalated": result.is_escalated,
            },
        )

    payload = {
        "answer": result.answer,
        "conversation_id": result.conversation_id,
        "is_escalated": result.is_escalated,
        "is_degraded": result.is_degraded,
    }
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


def chat_health(request: HttpRequest) -> HttpResponse:
    """Indica se o assistente está operacional, para diagnóstico operacional."""
    from django.conf import settings

    return JsonResponse(
        {
            "enabled": settings.ECHILO_AI_ENABLED,
            "model": settings.ECHILO_AI_MODEL,
            "tools": [tool["function"]["name"] for tool in TOOL_SCHEMAS],
        }
    )
