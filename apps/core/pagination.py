"""O tamanho das listagens e a forma de o pedir ao servidor.

O número vive aqui e não em cada view porque estava em três sítios com três
valores: `12` no catálogo, `24` na curadoria e `25` nas contas. Três respostas à
mesma pergunta — quantas linhas cabem numa página — e nenhuma delas é a resposta
certa, que é a mesma em todas as listas.
"""

from __future__ import annotations

from typing import Any

from django.core.paginator import Page, Paginator
from django.http import HttpRequest

PAGINA_PADRAO = 6
"""Quantas linhas uma listagem mostra de cada vez."""


def pagina_de(conteudo: Any, request: HttpRequest, *, por_pagina: int = PAGINA_PADRAO) -> Page:
    """Pagina um queryset ou lista que a view montou à mão.

    As `ListView` do Django fazem isto sozinhas a partir do `paginate_by`. Uma
    view que responde com `render()` não tem onde o pôr, e sem isto a fila de
    contactos fica um `[:100]` mudo: corta sem dizer que cortou, e a equipa vê
    uma lista que acaba a meio sem nunca saber que há mais.
    """
    return Paginator(conteudo, por_pagina).get_page(request.GET.get("page"))
