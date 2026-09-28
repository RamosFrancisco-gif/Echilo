"""Utilitários de imagem partilhados, sem dependência de testes."""

from __future__ import annotations

import io
import uuid

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .storage import FORMATOS_IMAGEM, LADO_RETRATO_PX

# `quality: 82` num JPEG de 512 px dá um retrato sem artifactos visíveis a 72 px com
# cerca de 60 KB. Baixar mais custaria qualidade a toda a gente; subir mais só
# pagava bytes que ninguém vê.
QUALIDADE_RETRATO = 82


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


def prepara_retrato(uploaded: object, *, lado: int = LADO_RETRATO_PX) -> ContentFile:
    """Devolve o retrato já reduzido, quadrado e recodificado, pronto a guardar.

    O §6 manda nunca servir um ficheiro tal como o utilizador o enviou, e um
    retrato de telemóvel é um JPG de 4000 px com a orientação da câmara e
    metadados EXIF. Guardado como veio, isso é três vezes o que a interface pede e
    a orientação errada para quem não a sabe inverter.

    A redução é um quadrado com corte ao centro porque o CSS já desenha um círculo:
    cortar aqui e cortar lá é o mesmo corte feito duas vezes, e o do servidor é o
    único que sobrevive a uma página sem folha de estilos. Ao centro e não pelo
    rosto porque o Pillow não sabe onde está o rosto, e adivinhar o centro de um
    retrato alinhado ao topo corta a testa.

    Nunca amplia, e o alvo é o **lado mais curto** da fotografia que não é um
    retrato. `ImageOps.fit` reduz ou amplia para cobrir o quadrado pedido, e um
    retrato deitado (2000×1000) tem de ser quadrado a partir do lado que não
    sobra: mandar 512 é reduzir, e 512 cabe nos dois lados. Um retrato pequeno e
    deitado (200×100) é o caso em que isto decide: o lado mais curto dá 100, e
    `fit` corta sem inventar pixels. `min()` do lado maior dava 200 — o dobro, com
    a largura interpolada e nenhuma informação nova. Um retrato de 200 px é
    pequeno demais para o cabeçalho de um ecrã de densidade alta, mas ampliar não
    traz a fotografia que não está lá, só a mesma interpolação em mais bytes.
    """
    seek_inicio(uploaded)
    with Image.open(uploaded) as imagem:  # type: ignore[arg-type]
        corrigida = ImageOps.exif_transpose(imagem)
        if corrigida.mode in ("RGBA", "LA", "PA") or (
            corrigida.mode == "P" and "transparency" in corrigida.info
        ):
            # Um PNG com canal alfa e uma paleta com transparência são dois
            # ficheiros que o JPEG não sabe escrever. O passo que os torna um é
            # achatá-los sobre branco, e o branco é o mesmo que o JavaScript pinta
            # antes de exportar: sem ele, o que era transparente sai preto.
            rgba = corrigida.convert("RGBA")
            fundo = Image.new("RGB", rgba.size, (255, 255, 255))
            fundo.paste(rgba, mask=rgba.split()[-1])
            corrigida = fundo
        elif corrigida.mode != "RGB":
            # Cinza, CMYK de câmara e os formatos de 16 bits chegam aqui, e todos
            # se escrevem como RGB sem perda visível a 72 px.
            corrigida = corrigida.convert("RGB")
        alvo = min(lado, min(corrigida.size))
        quadrada = ImageOps.fit(
            corrigida,
            (alvo, alvo),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    buffer = io.BytesIO()
    quadrada.save(buffer, format="JPEG", quality=QUALIDADE_RETRATO, optimize=True)
    # O nome diz JPEG porque os bytes são JPEG, e a storage procura o ficheiro pelo
    # nome: um `retrato.png` com bytes JPEG passa a validação da extensão e sai
    # como um ficheiro que ninguém consegue abrir.
    #
    # O nome é um `uuid4` e não o que a pessoa enviou, por duas razões que se
    # sustentam uma à outra. Não publica o nome do ficheiro dela — o mesmo motivo
    # de `_nome_opaco`, e o que o `§6` pede. E nunca colide: um nome fixo
    # (`retrato.jpg`) escrevia sempre no mesmo sítio, o novoficheiro apagava o
    # antigo pelo caminho e a comparação `antigo != actual` nunca disparava, que é
    # a comparação que apaga o retrato que ficou para trás. A nuvem já inventaria o
    # nome; aqui o nome também é inventado, para os dois backends se comportarem
    # igual em vez de o disco acertar por acaso.
    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.jpg")


def seek_inicio(uploaded: object) -> None:
    """Volta o ficheiro ao início, porque quem o abriu antes deixou-o a meio.

    `motivo_recusa_imagem` abre a imagem para lhe ler o tamanho, e a leitura deixa
    o cursor onde a imagem acabou. Sem este `seek`, a redução seguinte recebia o
    resto do ficheiro e falhava num `UnidentifiedImageError` que não nomeava a
    imagem como causa.
    """
    seek = getattr(uploaded, "seek", None)
    if callable(seek):
        seek(0)


def solid_colour_jpeg(colour: tuple[int, int, int] = (40, 32, 22)) -> bytes:
    """Devolve um JPEG real de uma cor sólida, para capas de demonstração.

    O upload tem de ser um ficheiro que o Pillow consiga abrir, por isso um bloco
    de bytes arbitrários não serve.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (640, 420), colour).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()
