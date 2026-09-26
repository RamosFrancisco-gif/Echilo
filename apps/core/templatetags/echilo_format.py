"""Filtros de formatação de interface, alinhados com §2.5 do steering."""

from __future__ import annotations

from decimal import Decimal

from django import template

from apps.core.money import angolan_ratio as _angolan_ratio
from apps.core.money import kwanza as _kwanza
from apps.core.money import kwanza_compact as _kwanza_compact

register = template.Library()

# O dinheiro é formatado em `apps.core.money` para que o mapa, os e-mails e os
# cartões não tenham cada um as suas regras. Aqui só se regista como filtro.
kwanza = register.filter("kwanza", _kwanza)
kwanza_compact = register.filter("kwanza_compact", _kwanza_compact)


@register.filter
def is_client_role(user: object) -> bool:
    """Diz se o utilizador tem o perfil de cliente final."""
    return getattr(user, "role", None) == "CLIENT"


@register.filter
def distance(value: object) -> str:
    """Formata um raio em metros, passando a quilómetros a partir de 1 km.

    O raio viaja em metros porque é assim que vai no URL e no mapa; a leitura é
    outra. Um raio de 50 km escrito em metros não parece um raio.
    """
    if value is None or value == "":
        return "—"
    try:
        metres = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return "—"
    if metres < 1000:
        return f"{metres:.0f} m"
    return f"{_angolan_ratio(metres / Decimal('1000'), 1)} km"


@register.filter
def coordinate(value: object) -> str:
    """Escreve uma coordenada ou um raio com ponto decimal, sempre.

    A interface é `pt-AO`, e o Django localiza `-8.918430` para `-8,918430`.
    Serve para a leitura, não para um campo de formulário: aí a coordenada
    voltaria ao servidor como texto que `Decimal` não lê, e a área desenhada
    desapareceria ao primeiro filtro que o utilizador mexesse. Um valor que
    viaja tem de sair invariante.
    """
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return ""
    # `normalize` tira os zeros à direita; `f` impede que voltem em notação
    # científica, que o mapa não aceita.
    return format(number.normalize(), "f")


@register.filter
def repeat(value: object) -> list[None]:
    """Repete um número de vezes, para desenhar esqueletos de carregamento."""
    try:
        total = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    return [None] * max(0, min(total, 12))
