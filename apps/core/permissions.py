"""Regras de acesso reutilizadas pelas views e pelo admin."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest


def is_team_member(user: object) -> bool:
    """Diz se o utilizador pertence à equipa interna do Echilo.

    A regra está em `User.is_team_role`, como as outras. Repetir aqui o conjunto de
    perfis dava um segundo sítio a responder à pergunta "quem é da equipa", e a
    divergência entre os dois não daria erro: a guarda deixava passar, e o menu
    escondia a entrada — ou o contrário, que é pior.
    """
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "is_team_role", False)
    )


def require_team_member(request: HttpRequest) -> None:
    """Bloqueia o pedido quando o utilizador não pertence à equipa."""
    if not is_team_member(request.user):
        raise PermissionDenied("Esta área é reservada à equipa Echilo.")


def refuse_team_member(request: HttpRequest) -> None:
    """Recusa actos de cliente a quem é da equipa.

    Pedir visita e fazer proposta são o cliente a pedir; a equipa confirma e
    decide (§2.10, §2.5). Um pedido com `requested_by` de agente é lixo na fila
    — e escondê-lo no template sem o recusar na vista deixava-o à distância de
    um endereço escrito à mão.
    """
    if is_team_member(request.user):
        raise PermissionDenied("Pedir visitas e fazer propostas é acto de cliente.")


def require_admin(request: HttpRequest) -> None:
    """Restringe a gestão de contas ao administrador (§3).

    A regra está em `User.can_manage_users`, como as outras. Escrever `role ==
    "ADMIN"` aqui daria um segundo sítio a responder à pergunta "quem cria
    contas", e a divergência entre os dois não daria erro: apareceria a entrada
    no menu a quem não devia, e a página continuaria a responder 403.
    """
    if not getattr(request.user, "can_manage_users", False):
        raise PermissionDenied("Só a administração pode gerir contas.")


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
