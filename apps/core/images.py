"""Utilitários de imagem partilhados, sem dependência de testes."""

from __future__ import annotations

import io

from PIL import Image


def solid_colour_jpeg(colour: tuple[int, int, int] = (40, 32, 22)) -> bytes:
    """Devolve um JPEG real de uma cor sólida, para capas de demonstração.

    O upload tem de ser um ficheiro que o Pillow consiga abrir, por isso um bloco
    de bytes arbitrários não serve.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (640, 420), colour).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()
