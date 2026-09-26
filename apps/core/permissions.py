"""Regras de acesso reutilizadas pelas views e pelo admin."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

User = get_user_model()


def is_team_member(user: object) -> bool:
    """Diz se o utilizador pertence à equipa interna do Echilo."""
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "role", None) != User.Role.CLIENT
    )


def require_team_member(request: HttpRequest) -> None:
    """Bloqueia o pedido quando o utilizador não pertence à equipa."""
    if not is_team_member(request.user):
        raise PermissionDenied("Esta área é reservada à equipa Echilo.")


def require_document_access(request: HttpRequest) -> None:
    """Restringe a documentação legal a AGENT e ADMIN conforme §6 do steering."""
    if not is_team_member(request.user) or request.user.role not in {
        User.Role.AGENT,
        User.Role.ADMIN,
    }:
        raise PermissionDenied("A documentação legal exige permissão de agente.")
