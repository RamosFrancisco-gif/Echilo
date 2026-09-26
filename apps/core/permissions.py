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
    """Restringe a documentação legal a quem pode validar, conforme §6.

    A regra está em `User.can_validate` e não escrita aqui. Repetir o conjunto de
    perfis dava dois sítios a responder à pergunta "quem abre uma escritura", e
    divergir entre eles não dava erro: o `is_staff` do utilizador continua a ser
    verdadeiro para quem já tinha acesso, e o documento sai para a pessoa errada
    sem ninguém notar. A propriedade no modelo é a que também decide o `is_staff`,
    e por isso o admin e esta rota não podem discordar.
    """
    if not getattr(request.user, "can_validate", False):
        raise PermissionDenied("A documentação legal exige permissão de agente.")
