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

# O orçamento do lote é o que o pedido inteiro pode pesar, e não é a mesma
# pergunta que o `LIMITE_GB`: aquele é o que UMA fotografia pode pesar, este é o
# que as quinze pesam em conjunto. São dois tectos, e o que os liga é a
# plataforma.
#
# A Vercel recusa corpos de pedido acima de 4,5 MB na edge, antes de o Django ver
# a requisição, e o limite não é configurável — nem por `vercel.json`, nem por
# settings, nem por plano. A resposta é um 413 terso, sem página nem traceback: a
# equipa escolhe quinze fotografias, carrega no botão e lê «Content Too Large»,
# que não diz o que fazer nem qual o limite.
#
# Por isso o `DATA_UPLOAD_MAX_MEMORY_SIZE` do Django é irrelevante para este
# problema, e é maior do que o orçamento: o pedido morre na plataforma antes de
# o tecto do Django ter ordem para disparar. Uma validação que nunca chega a
# correr não protege ninguém, e fingir que protege é o que dá a certeza de que o
# limite do servidor é o do Django.
#
# Os 3,5 MB deixam margem para as fronteiras do multipart e para o resto do
# formulário, e dão ~230 KB por fotografia com o lote cheio — o suficiente para
# os 1600 px de `LADO_MAXIMO_CLIENTE`, que é mais do que a ficha mostra.
ORCAMENTO_LOTE_MB = 3.5

# O lado com que o browser reconstrói a fotografia antes de a enviar. O servidor
# aceita até 8000 px, que é o `lado_maximo` de `motivo_recusa_imagem`, mas uma
# fotografia de telemóvel a 4000 px não é uma fotografia melhor neste catálogo: é
# dezassete vezes mais pixels para o mesmo ecrã, e o orçamento é dividido por
# quinze. 1600 px serve a ficha num monitor grande.
LADO_MAXIMO_CLIENTE = 1600


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


def upload_config() -> dict[str, float]:
    """Publica para o JavaScript os números com que o lote é reduzido.

    É a mesma política do `map_config`: o número que o browser usa é o número que
    o Python define, e o `data-` do formulário é o contrato entre os dois. Um
    segundo valor em JavaScript seria uma segunda verdade, e a segunda é a que
    ninguém actualiza — o `accept` e o `name` do input já vivem assim, e é
    precisamente por isso que os dois campos concordam.
    """
    return {
        "orcamento_lote_mb": ORCAMENTO_LOTE_MB,
        "lado_maximo_cliente": LADO_MAXIMO_CLIENTE,
    }
