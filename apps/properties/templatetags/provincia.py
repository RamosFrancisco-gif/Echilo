"""Filtros das listas de referência de Angola.

Vivem em `properties` e não em `echilo_format` porque traduzem `ANGOLA_PROVINCES`,
que é deste app: o `core` não importa `properties`, e um filtro que o fizer para
traduzir uma sigla resolve-se com uma etiqueta da vista.
"""

from __future__ import annotations

from django import template

from ..reference import province_label as _province_label

register = template.Library()


@register.filter
def province_label(value: object) -> str:
    """Escreve o nome da província a partir do código, e o código se não houver nome."""
    return _province_label(str(value))
