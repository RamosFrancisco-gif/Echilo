"""Formatação de dinheiro em Kwanza, fora dos templates.

O valor de um imóvel aparece no cartão, na ficha, no rótulo de um pino no mapa
e no texto de e-mail. São quatro sítios, uma só verdade: se o mapa formatar o
preço com as próprias regras, um dia o pino escreve `85000000` e o cartão
escreve `85 M Kz`. Por isso a formatação vive aqui e os templates limiting-se a
importá-la.
"""

from __future__ import annotations

from decimal import Decimal

CURRENCY_SYMBOL = "Kz"

# Marcador temporário para inverter os separadores: "1,234.50" → "1.234,50".
_SWAP = "\x00"


def angolan_decimal(amount: Decimal) -> str:
    """Aplica o formato angolano: ponto de milhares, vírgula decimal."""
    grouped = f"{amount:,.2f}"
    return grouped.replace(",", _SWAP).replace(".", ",").replace(_SWAP, ".")


def angolan_ratio(value: Decimal, places: int) -> str:
    """Formata uma razão com precisão e sem zeros à direita que não informam.

    Dinheiro tem sempre duas casas; uma distância não. `2,50 km` é um número
    verdadeiro, mas o zero não acrescenta nada e rouba a atenção de quem lê.
    """
    text = f"{value:,.{places}f}"
    text = text.replace(",", _SWAP).replace(".", ",").replace(_SWAP, ".")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def trim_zeros(text: str) -> str:
    """Remove o zero decimal de valores redondos como `1,0` ou `1,5`."""
    return text[:-2] if text.endswith(",0") else text


def kwanza(value: Decimal | int | str | None) -> str:
    """Formata um valor em Kwanza com separador de milhares e vírgula decimal."""
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return "—"
    return f"{angolan_decimal(amount)} {CURRENCY_SYMBOL}"


def kwanza_compact(value: Decimal | int | str | None) -> str:
    """Formata Kwanza abreviando milhares e milhões para cartões e pinos."""
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return "—"
    quantised = amount.quantize(Decimal("1"))
    if quantised >= Decimal("1000000"):
        millions = angolan_decimal(quantised / Decimal("1000000")).removesuffix(",00")
        return f"{trim_zeros(millions)} M {CURRENCY_SYMBOL}"
    if quantised >= Decimal("1000"):
        thousands = angolan_decimal(quantised / Decimal("1000")).removesuffix(",00")
        return f"{trim_zeros(thousands)} mil {CURRENCY_SYMBOL}"
    return f"{quantised:,.0f} {CURRENCY_SYMBOL}"
