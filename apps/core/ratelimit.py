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
    """Conta pedidos no intervalo e informa se o limite já foi atingido.

    A contagem é um `incr` e não um `get` seguido de um `set`. A leitura e a
    escrita separadas deixavam dois pedidos simultâneos ler o mesmo número e
    gravar o mesmo número a seguir, e a segunda contagem desaparecia: em
    produção, onde as funções da Vercel correm em paralelo, o limite aceitaria
    o dobro do que diz. `incr` é um `UPDATE` de cada backend, e a excepção de
    chave em falta é o que o transformava no primeiro incremento.
    """
    key = f"ratelimit:{scope}:{client_ip(request)}"
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        attempts = 1
    if attempts > limit:
        return RateLimitResult(allowed=False, remaining=0, retry_after=window)
    return RateLimitResult(allowed=True, remaining=limit - attempts, retry_after=0)


def reset_rate_limit(request: HttpRequest, *, scope: str) -> None:
    """Limpa o contador depois de um pedido bem sucede do utilizador legítimo."""
    cache.delete(f"ratelimit:{scope}:{client_ip(request)}")
