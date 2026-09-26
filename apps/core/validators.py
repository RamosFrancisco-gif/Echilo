"""Validações transversais reutilizadas pelos formulários."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

ANGOLAN_PHONE_RE = re.compile(r"^\+?244\s?9\d{2}[\s-]?\d{3}[\s-]?\d{3}$")

# NIF angolano: 9 dígitos, 2 letras do serviço de emissão e 3 dígitos (§1).
NIF_RE = re.compile(r"^\d{9}[A-Z]{2}\d{3}$")

# O registo é um contrato de mediação, não um cadastro aberto: só maiores de idade.
MINIMUM_ACCOUNT_AGE = 18

# Acima desta idade a data é um erro de digitação, não uma data de nascimento.
MAXIMUM_PLAUSIBLE_AGE = 120

# Limites da pesquisa por área (§2.3). Vêm do público e travam tanto o abuso
# numa pesquisa repetida como a caixa que o MySQL tem de varrer. Não descrevem
# a geografia de Angola: Angola cabe folgadamente neste raio.
MIN_SEARCH_RADIUS_M = 100
MAX_SEARCH_RADIUS_M = 50_000


def validate_angolan_phone(value: str) -> None:
    """Aceita apenas telefones angolanos no formato +244 9XX XXX XXX (§1)."""
    normalized = re.sub(r"\s+", " ", (value or "").strip())
    if normalized and not ANGOLAN_PHONE_RE.match(normalized):
        raise ValidationError("Introduza um telefone angolano válido (ex.: +244 923 456 789).")


def normalize_nif(value: str) -> str:
    """Retira separadores e uniformiza em maiúsculas para comparar e guardar."""
    return re.sub(r"[\s-]+", "", (value or "")).upper()


def validate_nif(value: str) -> None:
    """Aceita apenas o NIF angolano no formato 009671373HA093."""
    if not NIF_RE.match(normalize_nif(value)):
        raise ValidationError(
            "Introduza um NIF válido: 9 dígitos, 2 letras e 3 dígitos (ex.: 009671373HA093)."
        )


def age_in_years(date_of_birth: date, *, today: date | None = None) -> int:
    """Conta anos completos, para que o dia do aniversário conte no próprio dia."""
    reference = today or timezone.now().date()
    years = reference.year - date_of_birth.year
    if (reference.month, reference.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def validate_adult(value: date, *, today: date | None = None) -> None:
    """Recusa menores de idade, datas futuras e idades que não são plausíveis."""
    reference = today or timezone.now().date()
    if value > reference:
        raise ValidationError("A data de nascimento não pode estar no futuro.")
    age = age_in_years(value, today=reference)
    if age < MINIMUM_ACCOUNT_AGE:
        raise ValidationError(
            f"É preciso ter pelo menos {MINIMUM_ACCOUNT_AGE} anos para criar uma conta."
        )
    if age > MAXIMUM_PLAUSIBLE_AGE:
        raise ValidationError("Verifique a data de nascimento indicada.")


def to_decimal_or_none(value: object) -> Decimal | None:
    """Converte para Decimal, devolvendo `None` ao que não é número.

    Rejeita `NaN` e `Infinity`: ambos passariam por `Decimal` e rebentariam mais
    tarde numa comparação, dentro do Django e sem contexto nenhum.
    """
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return None if not number.is_finite() else number


def validate_latitude(value: object) -> None:
    """Recusa latitudes que não existem no planeta."""
    number = to_decimal_or_none(value)
    if number is None or not Decimal("-90") <= number <= Decimal("90"):
        raise ValidationError("A latitude tem de estar entre -90 e 90 graus.")


def validate_longitude(value: object) -> None:
    """Recusa longitudes que não existem no planeta."""
    number = to_decimal_or_none(value)
    if number is None or not Decimal("-180") <= number <= Decimal("180"):
        raise ValidationError("A longitude tem de estar entre -180 e 180 graus.")


def validate_search_radius_m(value: object) -> None:
    """Recusa raios inúteis ou grandes demais para varrer a base de dados."""
    number = to_decimal_or_none(value)
    if number is None or not MIN_SEARCH_RADIUS_M <= number <= MAX_SEARCH_RADIUS_M:
        raise ValidationError(
            f"O raio da área tem de estar entre {MIN_SEARCH_RADIUS_M} m "
            f"e {MAX_SEARCH_RADIUS_M // 1000} km."
        )


def validate_center(latitude: object, longitude: object) -> None:
    """Um centro de círculo só existe com as duas coordenadas válidas."""
    validate_latitude(latitude)
    validate_longitude(longitude)
