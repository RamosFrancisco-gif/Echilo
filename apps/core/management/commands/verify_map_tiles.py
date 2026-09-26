"""Diz se os tiles configurados são mesmo um mapa.

Um fornecedor pode recusar a chave e responder HTTP 200 com uma imagem de aviso
em vez do mapa. O browser não distingue os dois casos: mostra os pixels, o
Leaflet conta o tile como carregado, e o filtro por área parece funcionar
enquanto o mapa é um cartaz de "API KEY REQUIRED".

Foi o que o CARTO passou a fazer, e quase passou despercebido porque o ficheiro
era um PNG válido com as dimensões certas.

O critério não é o estado HTTP, nem o tamanho, nem a variedade de tons: um
basemap minimalista é legitimamente pequeno e quase liso, e um tile de oceano é
quase liso por definição. O que separa um mapa de um cartaz é o conteúdo, e a
maneira segura de o ler é comparar: dois tiles de sítios muito distantes têm de
ser imagens diferentes. Um aviso devolve sempre a mesma.
"""

from __future__ import annotations

import hashlib
import io
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageStat

from apps.core.maps import area_map_config

USER_AGENT = "Echilo/1.0 (verificacao de tiles)"

# Dois pontos com terra firme, longe um do outro e do centro do mapa. Um
# oceano não serve de comparação: um tile de mar é quase liso por definição, e
# um fornecedor pode devolver um vazio em vez de uma imagem.
LUGARES_LONGE = ((-1.29, 36.82), (-23.55, -46.63))


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Converte coordenadas em índices de tile, como o Leaflet faz."""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


@dataclass(frozen=True)
class TileReport:
    """O que um tile devolveu e se isso é um mapa."""

    url: str
    tamanho: int
    cores: int
    desvio: float
    digest: str

    def _linha(self) -> str:
        return (
            f"{self.tamanho:,} bytes, {self.cores:,} cores, desvio {self.desvio:.1f}"
        )


class Command(BaseCommand):
    """Verifica os tiles configurados e explica o resultado."""

    help = "Confirma que os tiles configurados devolvem um mapa e não um aviso."

    def handle(self, *args: object, **options: object) -> None:
        config = area_map_config()
        erro_config = config.get("configError")
        if erro_config:
            raise CommandError(str(erro_config))

        url = str(config["tileUrl"])
        centro = self._ler(url, float(config["centerLat"]), float(config["centerLon"]), int(config["zoom"]))

        self.stdout.write(f"URL      : {url}")
        self.stdout.write(f"Centro   : {centro.url}")
        self.stdout.write(f"           {centro._linha()}")

        for indice, (lat, lon) in enumerate(LUGARES_LONGE, 1):
            longe = self._ler(url, lat, lon, int(config["zoom"]))
            self.stdout.write(f"Comparacao {indice}: {longe._linha()}")

            if longe.digest == centro.digest:
                raise CommandError(
                    f"O fornecedor devolveu a MESMA imagem para Luanda e para as "
                    f"coordenadas {lat}, {lon}. Isso e um cartaz, nao um mapa: "
                    f"falta ECHILO_MAP_TILE_KEY, ou o plano gratuito obrigou a "
                    f"registo. No mapa aparece como uma imagem com texto, e o "
                    f"Leaflet conta-a como tile carregado."
                )

        self.stdout.write(self.style.SUCCESS("OK: os tiles sao um mapa."))

    def _ler(self, url: str, lat: float, lon: float, zoom: int) -> TileReport:
        """Descarrega um tile e mede o que voltou.

        A substituição é por nome de placeholder, e não por posição: o Esri usa
        `{z}/{y}/{x}` e o Leaflet escreve `{z}/{x}/{y}`. Substituir por posição
        trocava as coordenadas e dava um tile do outro lado do mundo.
        """
        x, y = _tile_xy(lat, lon, zoom)
        amostra = (
            url.replace("{s}", "a")
            .replace("{z}", str(zoom))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
            .replace("{r}", "")
        )
        request = urllib.request.Request(amostra, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as resposta:  # noqa: S310
                bruto = resposta.read()
        except urllib.error.HTTPError as erro:
            raise CommandError(
                f"O fornecedor respondeu HTTP {erro.code} a {amostra}. "
                f"Se for 403, falta ECHILO_MAP_TILE_KEY ou o tráfego foi "
                f"recusado. Se for 401, a chave está errada."
            ) from erro
        except urllib.error.URLError as erro:
            raise CommandError(f"Não foi possível pedir {amostra}: {erro.reason}") from erro

        with Image.open(io.BytesIO(bruto)) as imagem:
            rgb = imagem.convert("RGB")
            cores = len(rgb.getcolors(maxcolors=100_000) or [])
            desvio = max(ImageStat.Stat(rgb).stddev)

        return TileReport(
            url=amostra,
            tamanho=len(bruto),
            cores=cores,
            desvio=desvio,
            digest=hashlib.sha256(bruto).hexdigest(),
        )
