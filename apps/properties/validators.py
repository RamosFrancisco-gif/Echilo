"""Regras das fotografias do imóvel (§2.1), testadas isoladamente do resto.

O número vive aqui e não em cada lado que o usa. Estava escrito em três —
o `is_ready_for_review`, o formulário de carregamento e o texto que a equipa lê
na ficha — e um limite de 30 com 15 num deles não dá erro: dá três respostas
diferentes à mesma pergunta, das quais a que se lê no ecrã é a errada.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

# Menos do que isto e a ficha não é uma ficha: é um anúncio sem percurso. Mais
# do que isto e o peso da página passa a custar mais ao cliente do que o imóvel
# lhe rende, e ninguém faz dezasseis Photographs para arrendar um quarto.
MIN_FOTOS = 5
MAX_FOTOS = 15

# O que a ficha mostra quando ainda há espaço. É o número que evita a equipa
# carregar vinte fotografias e descobrir que quinze foram recusadas.
LIMITE_GB = 5


def validate_photo_count(count: int) -> None:
    """Impede um imóvel com zero fotografias, ou com mais do que as permitidas."""
    if count < 0:
        raise ValidationError("O número de fotografias não pode ser negativo.")
    if count > MAX_FOTOS:
        raise ValidationError(f"Um imóvel não pode ter mais de {MAX_FOTOS} fotografias.")


def remaining_photo_slots(existing: int) -> int:
    """Devolve quantas fotografias ainda cabem, sem passar do tecto.

    Existe para a ficha mostrar o espaço que resta. O formulário recusa o
    excesso, e a equipa precisa de o saber *antes* de escolher os ficheiros:
    descobrir que três de quinze foram recusadas depois de as escolher obriga a
    outra volta ao mesmo sítio.
    """
    return max(0, MAX_FOTOS - max(0, existing))
