"""Limitação de tentativas baseada em cache, sem dependências externas."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.http import HttpRequest

DEFAULT_LIMIT = 5
DEFAULT_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class RateLimitResult:
    """Decisão do limitador sobre um pedido concreto."""

    allowed: bool
    remaining: int
    retry_after: int


def client_ip(request: HttpRequest) -> str:
    """Extrai o endereço do cliente, respeitando o proxy de confiança."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "desconhecido")


def check_rate_limit(
    request: HttpRequest,
    *,
    scope: str,
    limit: int = DEFAULT_LIMIT,
    window: int = DEFAULT_WINDOW_SECONDS,
) -> RateLimitResult:
    """Conta pedidos no intervalo e informa se o limite já foi atingido."""
    key = f"ratelimit:{scope}:{client_ip(request)}"
    attempts = cache.get(key, 0)
    if attempts >= limit:
        return RateLimitResult(allowed=False, remaining=0, retry_after=window)
    cache.set(key, attempts + 1, window)
    return RateLimitResult(allowed=True, remaining=limit - attempts - 1, retry_after=0)


def reset_rate_limit(request: HttpRequest, *, scope: str) -> None:
    """Limpa o contador depois de um pedido bem sucede do utilizador legítimo."""
    cache.delete(f"ratelimit:{scope}:{client_ip(request)}")
