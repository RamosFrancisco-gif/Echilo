"""Configuração do mapa de pesquisa por área, lida das settings (§7).

Fica fora da view para que a view só peça a configuração em vez de a montar, e
para que o teste possa ver exactamente o que o JavaScript vai receber.
"""

from __future__ import annotations

from django.conf import settings
from django.templatetags.static import static

from .validators import MAX_SEARCH_RADIUS_M, MIN_SEARCH_RADIUS_M

# A que distância a camada de municípios entra. A 9 vê-se Angola inteiro e
# desenhar 157 contornos só suja a imagem; a partir de 10 os municípios já são
# o que se está a olhar.
ZOOM_MUNICIPIOS = 10


def area_map_config() -> dict[str, object]:
    """Devolve a configuração do mapa em JavaScript, já com a chave aplicada.

    As chaves são `camelCase` porque são lidas como objecto por `echilo.js`.
    O raio máximo vem dos validadores e não das settings: é uma regra de negócio
    (§2.3), e a única fonte de verdade tem de ser a mesma no servidor e no
    formulário.
    """
    tile_url = settings.ECHILO_MAP_TILE_URL
    if settings.ECHILO_MAP_TILE_KEY:
        tile_url = tile_url.replace("{key}", settings.ECHILO_MAP_TILE_KEY)

    config: dict[str, object] = {
        "tileUrl": tile_url,
        "attribution": settings.ECHILO_MAP_TILE_ATTRIBUTION,
        "centerLat": settings.ECHILO_MAP_CENTER_LAT,
        "centerLon": settings.ECHILO_MAP_CENTER_LON,
        "zoom": settings.ECHILO_MAP_DEFAULT_ZOOM,
        "minRadiusM": MIN_SEARCH_RADIUS_M,
        "maxRadiusM": MAX_SEARCH_RADIUS_M,
        # Só os tiles escuros precisam de inversão; invertê-los deixaria claros.
        "invertTiles": settings.ECHILO_MAP_INVERT_TILES,
        # Os limites vêm de ficheiros versionados, nunca de um CDN: são dados de
        # terceiro com licença e data de corte, e uma URL solta mudava o mapa sem
        # ninguém se aperceber. O município vai noutro ficheiro porque é cinco
        # vezes maior e só interessa quando o utilizador se aproxima.
        "provinciasUrl": static("vendor/geo/angola-provincias.json"),
        "municipiosUrl": static("vendor/geo/angola-municipios.json"),
        "zoomMunicipios": ZOOM_MUNICIPIOS,
    }

    if "{key}" in tile_url:
        # Um `.env` em branco é o caso comum, não a excepção. Deixar o `{key}` na
        # URL dá um 403 ou 404 que não explica nada ao utilizador, e parece um
        # bug do mapa. Dizer a falha é mais útil, e o catálogo não pode depender
        # do mapa para continuar a funcionar.
        config["configError"] = (
            "Falta ECHILO_MAP_TILE_KEY: o URL dos tiles ainda tem {key} por "
            "substituir. O mapa fica sem imagem, o filtro continua a funcionar."
        )

    return config
