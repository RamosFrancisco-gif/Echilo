"""Cliente Groq e ciclo de tool-use. Ponto único de contacto com a rede."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from .tools import TOOL_SCHEMAS, run_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_HISTORY_MESSAGES = 20


class AssistantUnavailable(RuntimeError):
    """Sinaliza que o nível 1 não está operacional neste momento."""


@dataclass
class AssistantReply:
    """Resposta do assistente já pronta a persistir e a apresentar."""

    content: str
    should_escalate: bool
    escalated_reason: str = ""
    tool_references: list[str] = field(default_factory=list)


ESCALATION_MARKERS = (
    "agendar uma visita",
    "marcar uma visita",
    "agendar visita",
    "marcar visita",
    "fazer uma proposta",
    "enviar uma proposta",
    "fechar o contrato",
    "assinar o contrato",
    "escritura",
    "iur",
    "certidao de registo",
    "certidão de registo",
    "reclamacao",
    "reclamação",
    "retirar o anuncio",
    "retirar o anúncio",
    "remover o imovel",
    "remover o imóvel",
)

# Negociação de preço é sempre humana (§2.4). O padrão exige intenção de
# renegociar, para que "aceita pagamento anual?" continue a ser resposta do nível 1.
NEGOTIATION_PATTERN = re.compile(
    r"(negocia\w+|desconto|baix\w*\s+(o\s+)?pre[çc]o|reduz\w*\s+(o\s+)?pre[çc]o"
    r"|diminu\w*\s+(o\s+)?pre[çc]o|aceitam?\s+\d)",
    re.IGNORECASE,
)


# Retirar um imóvel do site é uma decisão humana (§2.4, ponto 5).
REMOVAL_PATTERN = re.compile(
    r"(retirar|remover|tirar|ocultar|descartar)\s+"
    r"((o|um|a|meu|minha)\s+){0,2}"
    r"(an[úu]nci\w*|im[óo]vel\w*|propriedade\w*|listing\w*)",
    re.IGNORECASE,
)

# Disputas e queixas também são sempre humanas (§2.4). O padrão cobre a
# conjugação verbal, porque o cliente escreve "quero reclamar" e não "reclamação".
COMPLAINT_PATTERN = re.compile(
    r"(reclam\w+|queix\w+|denunci\w+|anúnci\w*\s+(falso|enganos\w+)|propaganda\s+enganos\w+)"
    r"|falso",
    re.IGNORECASE,
)


def should_escalate(text: str) -> tuple[bool, str]:
    """Detecta pedidos que só a equipa humana pode tratar (§2.4)."""
    if NEGOTIATION_PATTERN.search(text):
        return True, "negociação de preço"
    if REMOVAL_PATTERN.search(text):
        return True, "pedido de remoção de imóvel"
    if COMPLAINT_PATTERN.search(text):
        return True, "queixa ou disputa"
    lowered = text.lower()
    for marker in ESCALATION_MARKERS:
        if marker in lowered:
            return True, marker
    return False, ""


class GroqProvider:
    """Invólucra o SDK da Groq e mantém o loop de chamadas de ferramentas."""

    def __init__(self) -> None:
        if not settings.ECHILO_AI_ENABLED:
            raise AssistantUnavailable("O assistente está desligado nas definições.")
        if not settings.ECHILO_AI_API_KEY:
            raise AssistantUnavailable("Falta configurar ECHILO_AI_API_KEY.")
        from groq import Groq

        self._client = Groq(
            api_key=settings.ECHILO_AI_API_KEY,
            max_retries=settings.ECHILO_AI_MAX_RETRIES,
            timeout=settings.ECHILO_AI_TIMEOUT,
        )
        self.model = settings.ECHILO_AI_MODEL

    def complete(
        self,
        *,
        system_prompt: str,
        history: list[dict[str, str]],
        question: str,
    ) -> AssistantReply:
        """Executa o turno de conversa, resolvendo chamadas de ferramentas."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for entry in history[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": entry["role"], "content": entry["content"]})
        messages.append({"role": "user", "content": question})

        references: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=700,
            )
            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                content = (message.content or "").strip()
                # A escalação é decidida pela pergunta do cliente, antes de chamar o
                # modelo (§2.4). Analisar a resposta faria escalar conversas cujo
                # único motivo é o modelo sugerir "marcar uma visita".
                return AssistantReply(
                    content=content,
                    should_escalate=False,
                    escalated_reason="",
                    tool_references=references,
                )

            messages.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                references.extend(_extract_references(call.function.arguments))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": run_tool(call.function.name, _safe_arguments(call.function.arguments)),
                    }
                )

        return AssistantReply(
            content=(
                "Já tenho dados suficientes sobre o que procuras, mas a consulta está a "
                "demorar. Deixo o assunto com a equipa do Echilo, que te responde em breve."
            ),
            should_escalate=True,
            escalated_reason="Consulta excedeu o tempo previsto.",
            tool_references=references,
        )


def _safe_arguments(raw: str) -> dict[str, Any]:
    """Converte o JSON de argumentos da ferramenta, tolerando payload inválido."""
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        logger.warning("Argumentos de ferramenta inválidos: %s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_references(raw: str) -> list[str]:
    """Recolhe referências de imóvel presentes nos argumentos da ferramenta."""
    payload = _safe_arguments(raw)
    found: list[str] = []
    if payload.get("reference"):
        found.append(str(payload["reference"]))
    for item in payload.get("references") or []:
        found.append(str(item))
    return found
