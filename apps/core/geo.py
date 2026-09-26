"""Geometria da Terra ao serviço da pesquisa por área (§2.3).

São funções puras: nenhum modelo, nenhuma consulta, nenhum I/O. É essa
puraza que as torna testáveis sem base de dados e sem rede.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import asin, cos, pi, radians, sin, sqrt

# Raio médio terrestre (IUGG). Um elipsóide WGS84 daria a mesma resposta com
# muito mais código, para um erro de poucos metros num raio de 50 km.
EARTH_RADIUS_M = 6_371_008.8

# Um grau de latitude na mesma esfera com que a distância é medida. As duas
# constantes têm de ser coerentes entre si: se a caixa usasse os 111 320 m do
# meridiano WGS84, ela ficaria 125 m por grau mais estreita do que o círculo que
# diz conter, e o imóvel que estivesse mesmo na borda desaparecia da pesquisa.
METRES_PER_DEGREE_LATITUDE = 2 * pi * EARTH_RADIUS_M / 360

# Perto dos polos o círculo já toca num eixo, o denominador tende para zero e a
# caixa explodiria. O limite é generoso porque Angola está longe dos polos.
MINIMUM_COSINE_LATITUDE = 0.01

# As coordenadas de `Property` têm seis casas decimais; a caixa arredonda para
# a mesma escala, para o filtro cair no mesmo tipo de dado que o índice guarda.
SIX_PLACES = Decimal("0.000001")


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros entre dois pontos, pela fórmula de haversine."""
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    half_sine = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(half_sine))


def circle_bounding_box(
    lat: float, lon: float, radius_m: float
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Caixa que contém o círculo, por ordem `(lat_min, lat_max, lon_min, lon_max)`.

    Serve para reduzir candidatos em SQL com um índice antes de se pagar a
    distância exacta em Python. Tem de ser uma superserquisa e nunca menos: por
    isso os limites arredondam para fora, para que nenhum ponto do círculo
    caiba entre a caixa e o `DecimalField` onde o foi guardar.
    """
    lat_delta = radius_m / METRES_PER_DEGREE_LATITUDE
    cos_lat = max(abs(cos(radians(lat))), MINIMUM_COSINE_LATITUDE)
    lon_delta = min(radius_m / (METRES_PER_DEGREE_LATITUDE * cos_lat), 180.0)

    return (
        _not_below(max(lat - lat_delta, -90.0)),
        _not_above(min(lat + lat_delta, 90.0)),
        _not_below(max(lon - lon_delta, -180.0)),
        _not_above(min(lon + lon_delta, 180.0)),
    )


def _not_below(value: float) -> Decimal:
    """Arredonda para baixo, para que o limite mínimo não corte o círculo."""
    return Decimal(value).quantize(SIX_PLACES, rounding=ROUND_FLOOR)


def _not_above(value: float) -> Decimal:
    """Arredonda para cima, para que o limite máximo não corte o círculo."""
    return Decimal(value).quantize(SIX_PLACES, rounding=ROUND_CEILING)
