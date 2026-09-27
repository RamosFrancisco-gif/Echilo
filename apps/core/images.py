"""Utilitários de imagem partilhados, sem dependência de testes."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from .storage import FORMATOS_IMAGEM


def motivo_recusa_imagem(
    uploaded: object,
    *,
    limite_mb: int,
    lado_maximo: int = 8000,
) -> str | None:
    """Devolve a razão pela qual a imagem não entra, ou `None` se entra.

    Vive aqui e não em cada formulário porque é a mesma pergunta em dois sítios — a
    fotografia do imóvel e o retrato do perfil — e duas respostas a ela divergem
    sem dar erro: o retrato aceita um SVG que o catálogo recusa, e ninguém sabe
    qual dos dois está certo.

    O `ImageField` do Django valida o cabeçalho, não a imagem: um executável
    renomeado a `.jpg` passa, e abrir com Pillow é o que separa uma fotografia de
    um ficheiro que diz ser uma.
    """
    nome = str(getattr(uploaded, "name", "ficheiro"))
    tamanho = int(getattr(uploaded, "size", 0) or 0)
    if tamanho > limite_mb * 1024 * 1024:
        return f"{nome} excede {limite_mb} MB."

    try:
        with Image.open(uploaded) as imagem:  # type: ignore[arg-type]
            largura, altura = imagem.size
            formato = (imagem.format or "").lower()
    except (UnidentifiedImageError, OSError, ValueError):
        return f"{nome} não é uma imagem válida."

    if formato not in FORMATOS_IMAGEM:
        return f"{nome} está em {formato or 'formato desconhecido'}. Use JPEG, PNG ou WebP."
    if max(largura, altura) > lado_maximo:
        return f"{nome} tem mais de {lado_maximo} px de lado."
    return None


def solid_colour_jpeg(colour: tuple[int, int, int] = (40, 32, 22)) -> bytes:
    """Devolve um JPEG real de uma cor sólida, para capas de demonstração.

    O upload tem de ser um ficheiro que o Pillow consiga abrir, por isso um bloco
    de bytes arbitrários não serve.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (640, 420), colour).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()
