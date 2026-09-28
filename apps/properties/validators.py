"""Regras das fotografias do imóvel (§2.1), testadas isoladamente do resto.

O número vive aqui e não em cada lado que o usa. Estava escrito em três —
o `is_ready_for_review`, o formulário de carregamento e o texto que a equipa lê
na ficha — e um limite de 30 com 15 num deles não dá erro: dá três respostas
diferentes à mesma pergunta, das quais a que se lê no ecrã é a errada.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.core.storage import LADO_RETRATO_PX

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
# plataforma — o porquê do limite de 4,5 MB e da margem está em
# `apps.core.validators`, que é onde vive, porque é o mesmo para todos os pedidos.
#
# Uma validação que nunca chega a correr não protege ninguém, e fingir que protege
# é o que dá a certeza de que o limite do servidor é o do Django: a equipa escolhe
# quinze fotografias, carrega no botão e lê «Content Too Large», que não diz o que
# fazer nem qual o limite.
#
# Os 3,5 MB deixam ~230 KB por fotografia com o lote cheio — o suficiente para os
# 1600 px de `LADO_MAXIMO_CLIENTE`, que é mais do que a ficha mostra.
#
# Fica abaixo de `LIMITE_UPLOAD_MB` de propósito: este é o orçamento que o browser
# persegue, e a diferença para o tecto é a folga para quando a divisão por quinze
# não dá a cada fotografia o que a conta promete. Um orçamento escrito com o mesmo
# número do tecto é um orçamento que o primeiro ficheiro a mais estraga.
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


# O retrato é um ficheiro só, e por isso o orçamento não é dividido: é o peso
# máximo que um `POST` de perfil pode levar. Meio megabyte é uma ordem de grandeza
# abaixo do limite do pedido, o que deixa o formulário inteiro — dados,
# identificação e o token de CSRF — a caminho de uma folga que não existe.
ORCAMENTO_PERFIL_MB = 0.5

# O lado com que o browser reconstrói o retrato antes de o enviar. É o mesmo número
# que o servidor guarda, e não um parecido: o browser reduzir para 1600 e o
# servidor recusar acima de 8000 produz um retrato entregue a 512 que ainda pesava
# meio megabyte, que é o que a reduzir o pedido existia para evitar.
LADO_MAXIMO_PERFIL_CLIENTE = LADO_RETRATO_PX


def config_perfil() -> dict[str, float]:
    """Publica para o JavaScript os números com que o retrato é reduzido.

    Mesma política do `upload_config`, com uma diferença que a forma impõe: o
    `name` do input também é escrito no formulário, porque o redutor de lotes
    procura `images` e este campo chama-se `photo`. Um `name` escrito no template
    ao lado do `data-` que o acompanha é um nome que se pode trocar sem o `data-`,
    e o redutor deixa de armar sem dar erro.
    """
    return {
        "orcamento_perfil_mb": ORCAMENTO_PERFIL_MB,
        "lado_maximo_perfil_cliente": LADO_MAXIMO_PERFIL_CLIENTE,
    }
