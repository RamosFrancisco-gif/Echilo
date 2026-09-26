"""Gera as fronteiras administrativas de Angola usadas pelo mapa.

Os dados não são escritos à mão. Vêm do geoBoundaries `gbOpen`, licença
CC BY 4.0, num commit fixo, e o comando grava em `static/vendor/geo/`. Vendorizar
é a regra do projecto para tudo o que de terceiro entra na página: nada de CDN,
nada que mude com o deploy.

    python manage.py build_admin_boundaries
    python manage.py build_admin_boundaries --check   # só verifica deriva

Três decisões, todas deliberadas:

- **Simplificação, e não em igual nas duas camadas.** A versão original tem
  6,7 MB e 149 mil vértices para 158 municípios. Arredondar coordenadas não
  chega, porque não remove vértices colineares; a Douglas-Peucker remove. Nos
  municípios vai a 223 m e o ficheiro desce para cerca de meio megabyte. Nas
  províncias vai a 1,1 km, porque uma província tem 200 km de lado e 1,1 km de
  desvio não se vê. São contornos de referência, não registos cadastrais.
- **Municípios à parte das províncias.** A lista dos municípios é cinco vezes
  maior que a das províncias. Vai num pedido separado, feito quando o mapa
  atinge `ZOOM_MUNICIPIOS` ou quando o utilizador desenha um círculo, para uma
  vista de país não pagar por 157 municípios que não se leem a essa escala.
- **Coordenadas em `[lon, lat]`, com uma excepção que é regra.** O
  `geometry.coordinates` sai em ordem GeoJSON, que é o que o `L.geoJSON` lê ao
  desenhar. O `properties.ponto` sai ao contrário, em `[lat, lon]`, porque é o
  `L.circleMarker` que o recebe. O comando raciocina em `[lat, lon]` do
  principio ao fim, e a troca acontece em `geometria()`; desfaz-se em
  `aneis_de()`. Trocar um dos dois não dá erro nenhum: o mapa desenha Angola
  no Atlântico a oeste, ou escreve os nomes no mar, e o DOM continua cheio de
  `path`.

O `ADM2` do geoBoundaries não diz a que província pertence cada município, por
isso a atribuição é espacial: o centro de cada município é testado contra os
contornos provinciais, a 5,5 m, bem mais fino do que a simplificação, porque aí
um erro põe o município na província errada. Um município que caia em zero ou
duas províncias faz o comando falhar. Um ficheiro com um município na província
errada é pior do que não ter o ficheiro.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.management.utils import exige_desenvolvimento
from apps.properties.reference import (
    MUNICIPALITIES_BY_PROVINCE,
    PROVINCE_BOUNDARY_CODES,
    provinces_without_boundary,
)

# Commit fixo: o geoBoundaries republica e renomeia ficheiros, e um URL solto
# mudava o conteúdo sem ninguém se aperceber.
COMMIT = "9469f09"
FONTE = "geoBoundaries gbOpen"
LICENCA = "CC BY 4.0"
BASE = f"https://github.com/wmgeolab/geoBoundaries/raw/{COMMIT}/releaseData/gbOpen/AGO"

# 0,002 graus ≈ 223 m. Ver a nota de simplificação no topo do módulo.
TOLERANCIA = 0.002
CASAS = 4

# As províncias vão sempre no primeiro carregamento, e uma província tem
# 200 km de lado: 1,1 km de desvio não se vê e o ficheiro cai de 160 KB para
# 71 KB. É por isso que o ponto-em-polígono do `municipios()` não usa este
# número, e sim o de baixo.
TOLERANCIA_PROVINCIAS = 0.01

# Só para decidir a que província pertence um município. Fino de propósito: aqui
# um erro põe o município no sítio errado, e um píxel de fronteira decide.
TOLERANCIA_TESTE = 0.0005

# Factos sobre a fonte, não sobre Angola. `Cuito` é um erro de digitação do
# geoBoundaries no sítio onde o município se chama Kuito, um C por um K, e por
# isso não pode sair de uma comparação automática. `Amboim (Gabela)` é o mesmo
# município escrito de duas maneiras: a fonte desambigua com o nome da cidade
# e a lista do projecto tem só `Amboim`. Ambos vivem aqui porque `reference.py`
# descreve o país e esta tabela descreve quem o mapeia.
CORRECOES_FONTE: Final[dict[str, str]] = {
    "Cuito": "Kuito",
    "Amboim (Gabela)": "Amboim",
}

DESTINO = Path(settings.BASE_DIR) / "static" / "vendor" / "geo"
FICHEIRO_PROVINCIAS = DESTINO / "angola-provincias.json"
FICHEIRO_MUNICIPIOS = DESTINO / "angola-municipios.json"


def sem_acentos(texto: str) -> str:
    """Reduz a um identificador comparável: `Huíla` e `HUILA` têm de casar."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).upper()


def chave(nome: str) -> str:
    """Reduz um nome de município à forma de comparação, sem pontuação.

    `Baía Farta`, `Baia Farta` e `BAIA-FARTA` são o mesmo município. A fonte
    escreve hifens e apóstrofos onde o projecto não põe, e essa diferença não
    pode traduzir-se num município sem nome no mapa.
    """
    return re.sub(r"[^A-Z0-9]", "", sem_acentos(nome))


def variantes(nome: str) -> set[str]:
    """O nome da fonte e as suas metades, quando vem `Nome (Alternativo)`.

    O geoBoundaries escreve `Amboim (Gabela)` e `Sumbe (Ngangula)`: em Angola o
    município foi renomeado, e a fonte põe o nome novo com o antigo entre
    parênteses. Qual dos dois o projecto prefere é uma decisão do projecto, e a
    lista de municípios sabe qual é.
    """
    nomes = {nome}
    if "(" in nome and nome.endswith(")"):
        cabeca, _, cauda = nome.partition("(")
        nomes.add(cabeca.strip())
        nomes.add(cauda.rstrip(") ").strip())
    if "-" in nome:
        nomes.add(nome.split("-", 1)[0].strip())
    return nomes


def distancia_ao_segmento(
    ponto: tuple[float, float],
    inicio: tuple[float, float],
    fim: tuple[float, float],
) -> float:
    """Distância perpendicular de um ponto ao segmento `inicio`–`fim`."""
    (x0, y0), (x1, y1), (px, py) = inicio, fim, ponto
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def douglas_peucker(
    pontos: list[tuple[float, float]],
    tolerancia: float,
) -> list[tuple[float, float]]:
    """Corta os vértices que não mudam a forma da linha dentro da tolerância."""
    if len(pontos) < 3:
        return list(pontos)
    manter = [False] * len(pontos)
    manter[0] = manter[-1] = True
    pilha = [(0, len(pontos) - 1)]
    while pilha:
        primeiro, ultimo = pilha.pop()
        melhor, indice = -1.0, -1
        for i in range(primeiro + 1, ultimo):
            dist = distancia_ao_segmento(
                pontos[i], pontos[primeiro], pontos[ultimo]
            )
            if dist > melhor:
                melhor, indice = dist, i
        if melhor > tolerancia and indice > 0:
            manter[indice] = True
            pilha.append((primeiro, indice))
            pilha.append((indice, ultimo))
    return [p for p, k in zip(pontos, manter) if k]


def aneis(geometria: dict[str, Any]) -> list[list[list[float]]]:
    """Devolve os anéis exteriores de um Polygon ou MultiPolygon."""
    if geometria["type"] == "Polygon":
        return list(geometria["coordinates"])
    return [anel for polygon in geometria["coordinates"] for anel in polygon]


def limites(geometria: dict[str, Any], tolerancia: float) -> list[list[list[float]]]:
    """Simplifica e converte para a ordem interna `[lat, lon]`."""
    partes: list[list[list[float]]] = []
    for anel in aneis(geometria):
        original = [tuple(p) for p in anel]
        simplificado = douglas_peucker(original, tolerancia)
        if len(simplificado) < 4:
            # A tolerância é um tecto de desvio, não uma licença para apagar a
            # divisão. Um anel mais pequeno que a tolerância encolhe para dois
            # pontos e desaparece do mapa, e um município que desapareceu é pior
            # do que um município desenhado com os vértices que a fonte deu.
            #
            # Daqui resulta uma contagem de vértices que não desce com a
            # tolerância: ao subir, mais anéis caem abaixo de quatro pontos e
            # voltam ao original. É o preço da garantia, e vale mais do que uns
            # kilobytes.
            simplificado = original
        pontos: list[list[float]] = []
        anterior: tuple[float, float] | None = None
        for lon, lat in simplificado:
            arredondado = (round(lon, CASAS), round(lat, CASAS))
            if arredondado != anterior:
                pontos.append([arredondado[1], arredondado[0]])
                anterior = arredondado
        if len(pontos) >= 4:
            partes.append(pontos)
    return partes


def centro(contornos: list[list[list[float]]]) -> list[float]:
    """Ponto representativo de uma divisão, para o nome aparecer no mapa.

    A média dos vértices do maior anel é o ponto óbvio, e é o melhor quando a
    divisão é convexa. Num município recortado ela cai fora do próprio contorno
    — em Angola, `Quela` é um deles — e o nome aparecia ao lado, o que é pior do
    que não aparecer: passa a ser um nome no sítio errado.

    Quando isso acontece, procura-se um ponto dentro do contorno, o mais longe
    possível da margem. É a aproximação barata ao polo de inacessibilidade: dá
    um ponto no meio da divisão, que é onde um rótulo se deve ler, e não exige
    a triangulação que o cálculo exacto faria.
    """
    anel = max(contornos, key=len)
    pontos = [(p[0], p[1]) for p in anel]
    medio = _media(pontos)
    if dentro(list(medio), contornos):
        return [round(medio[0], CASAS), round(medio[1], CASAS)]

    candidatos = []
    salto = max(1, len(pontos) // 250)
    for i in range(0, len(pontos) - 1, salto):
        a, b = pontos[i], pontos[i + 1]
        candidatos.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    dentro_dos = [c for c in candidatos if dentro([c[0], c[1]], contornos)]
    if not dentro_dos:
        return [round(medio[0], CASAS), round(medio[1], CASAS)]

    margem = _margem(contornos)
    melhor = max(
        dentro_dos,
        key=lambda c: min(math.hypot(c[0] - m[0], c[1] - m[1]) for m in margem),
    )
    return [round(melhor[0], CASAS), round(melhor[1], CASAS)]


def _media(pontos: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(p[0] for p in pontos) / len(pontos),
        sum(p[1] for p in pontos) / len(pontos),
    )


def _margem(contornos: list[list[list[float]]], alvo: int = 400) -> list[tuple[float, float]]:
    """Amostra da margem de uma divisão, para medir distância sem custar O(n²)."""
    todos = [tuple(p) for anel in contornos for p in anel]
    if len(todos) <= alvo:
        return todos
    salto = len(todos) // alvo + 1
    return todos[::salto]


def dentro(ponto: list[float], contornos: list[list[list[float]]]) -> bool:
    """Diz se um ponto cai dentro de um contorno, sobre coordenadas `[lat, lon]`.

    Conta quantas vezes uma linha horizontal que sai do ponto atravessa as
    margens. Par é dentro, ímpar é fora. Os anéis entram todos: a fonte tem
    multipartes, e a soma tem de ser par sobre o conjunto todo para o ponto
    não aparecer dentro de uma ilha a quarenta quilómetros do continente.
    """
    x, y = ponto
    dentro_de_algum = False
    for anel in contornos:
        anterior = anel[-1]
        for actual in anel:
            if (actual[1] > y) != (anterior[1] > y):
                corte = (anterior[0] - actual[0]) * (y - actual[1]) / (
                    anterior[1] - actual[1]
                ) + actual[0]
                if x < corte:
                    dentro_de_algum = not dentro_de_algum
            anterior = actual
    return dentro_de_algum


def geometria(contornos: list[list[list[float]]]) -> dict[str, Any]:
    """Um `MultiPolygon` GeoJSON a partir de uma lista plana de anéis.

    A forma é a do GeoJSON a sério — lista de polígonos, cada um com os seus
    anéis — e não a lista plana de anéis que circula dentro do comando. A
    diferença não é de estilo: `L.geoJSON` só aceita `FeatureCollection`, e um
    ficheiro com uma chave à escolha não é desenhado por nada, nem pelo Leaflet
    nem por outra ferramenta.

    As coordenadas saem em `[lon, lat]`, que é a ordem do formato e a que o
    Leaflet lê ao desenhar. Dentro do comando o costume é o contrário: `[lat,
    lon]`, porque é a ordem de `centro()` e de `dentro()` e a que
    `L.circleMarker` recebe. A troca acontece aqui, na fronteira. Sem ela o mapa
    desenhava Angola no oceano a oeste, e o browser não dizia nada: o `lat`
    lia-se como longitude e a longitude como latitude.
    """
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [[[ponto[1], ponto[0]] for ponto in anel]] for anel in contornos
        ],
    }


def aneis_de(geojson: dict[str, Any]) -> list[list[list[float]]]:
    """Os anéis exteriores de uma feature, na ordem interna `[lat, lon]`.

    O ficheiro está em `[lon, lat]`. Os testes de pertença daqui para fora
    trabalham em `[lat, lon]`, e esta função é a fronteira entre os dois: recebe
    a geometria como o browser a lê e devolve-a como o comando a raciocina.
    """
    return [
        [[ponto[1], ponto[0]] for ponto in anel]
        for poligono in geojson["geometry"]["coordinates"]
        for anel in poligono
    ]


def feature_divisao(
    propriedades: dict[str, Any], contornos: list[list[list[float]]]
) -> dict[str, Any]:
    """Uma `Feature` GeoJSON, com os contornos já simplificados."""
    return {
        "type": "Feature",
        "properties": propriedades,
        "geometry": geometria(contornos),
    }


@lru_cache(maxsize=None)
def descarrega(ficheiro: str) -> dict[str, Any]:
    """Descarrega uma camada do geoBoundaries, presa ao commit fixo.

    Pede a camada cheia e não a `simplified`. A simplificação do geoBoundaries é
    arbitrária e, em Angola, tem divisões que encolhem para quatro vértices; se a
    tolerância é nossa, ela é a mesma para toda a gente e o comando pode
    garantir o mínimo de pontos.

    Fica em cache porque as províncias são pedidas duas vezes: uma para gravar e
    outra para o teste de pertença, e são 19 MB.

    O `requests` é importado aqui e não no topo do ficheiro. Este comando é a
    única coisa do projecto que fala com a rede, e recusa correr fora de
    desenvolvimento — logo, o `requests` é uma dependência de desenvolvimento e
    não pertence ao `requirements.txt`. Importado no topo, fazia o módulo
    depender de um pacote que a Vercel não instala, e a consequência foi
    silenciosa: os testes de `apps.properties` deixavam de ser importados e
    desaparecia 128 testes da suite sem que a contagem desse isso por si.
    """

    try:
        import requests
    except ImportError as erro:
        # O pacote em falta é o caso mais provável de todos, e o mais mal
        # explicado: um `ModuleNotFoundError` a meio de uma traceback não diz que
        # se trata de uma dependência de desenvolvimento. Ver o `requirements.txt`.
        raise CommandError(
            "Falta o pacote `requests`, que é uma dependência de desenvolvimento e "
            "por isso não está no requirements.txt. Instala-o com "
            "`pip install requests` e volta a correr."
        ) from erro

    url = f"{BASE}/{ficheiro}/geoBoundaries-AGO-{ficheiro}.geojson"
    try:
        resposta = requests.get(url, timeout=600)
        resposta.raise_for_status()
    except requests.RequestException as erro:
        # Traduzido aqui, e não em `verifica_deriva`, porque a rede é chamada nos
        # dois caminhos — gerar e verificar. Traduzir só num deles deixava o outro a
        # responder com uma traceback em vez de uma mensagem que diz o que falta.
        raise CommandError(
            f"Não consegui falar com a fonte ({erro}). O comando precisa de rede, e "
            "sem rede não dá para verificar a deriva."
        ) from erro
    return resposta.json()


def metadados() -> dict[str, str]:
    """O que o mapa tem de mostrar sobre a origem dos dados.

    A CC BY 4.0 exige atribuição, e a atribuição que se esconde num `README` não
    cumpre a licença. Vai no ficheiro, e o mapa escreve-a no painel.
    """
    return {
        "nome": FONTE,
        "licenca": LICENCA,
        "url": "https://www.geoboundaries.org/",
        "nota": (
            "Fronteiras simplificadas para referência. "
            "Não são um registo cadastral."
        ),
    }


class Command(BaseCommand):
    help = "Gera os ficheiros de fronteiras administrativas de Angola para o mapa."

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.conflitos: list[str] = []

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Só verifica se os ficheiros versionados batem com a fonte.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["check"]:
            # Verificar é seguro em qualquer lado: só lê a fonte e os ficheiros
            # versionados, e é assim que se confirma a deriva num build.
            self.verifica_deriva()
            return
        exige_desenvolvimento(debug=settings.DEBUG, comando="build_admin_boundaries")
        self.escreve()

    def provincias(self) -> list[dict[str, Any]]:
        """As divisões da fonte, com o código que o projecto lhes dá.

        A lista do projecto tem vinte e uma entradas e a fonte tem dezoito
        divisões, e as duas não podem ser igualadas: `Cuando` e `Cubango` são uma
        divisão só na fonte, e `Icolo e Bengo` e `Moxico Leste` não têm contorno.
        Cada divisão da fonte é desenhada uma vez, sob o primeiro código que
        aponta para ela; as províncias sem contorno são contadas no relatório,
        não desenhadas a invenção.
        """
        saida = []
        for feature, codigo in self.pares_provincia():
            nome = feature["properties"]["shapeName"]
            partes = limites(feature["geometry"], TOLERANCIA_PROVINCIAS)
            if not partes:
                raise CommandError(f"A província {nome!r} ficou sem contorno.")
            saida.append(
                feature_divisao(
                    {"codigo": codigo, "nome": nome, "ponto": centro(partes)}, partes
                )
            )

        sem_contorno = provinces_without_boundary()
        if sem_contorno:
            self.stdout.write(
                f"  {len(saida)} divisões desenhadas; {len(sem_contorno)} províncias "
                f"sem contorno na fonte: {', '.join(sem_contorno)}."
            )
        return saida

    def pares_provincia(self) -> list[tuple[dict[str, Any], str]]:
        """Cada divisão da fonte, com o código que o projecto lhe dá.

        A correspondência é a de `PROVINCE_BOUNDARY_CODES`, que é escrita à mão
        porque não é dedutível: quando a fonte fundir ou separar uma província,
        a tabela é que fica a dizer qual das duas coisas é. Uma divisão da fonte
        que nenhum código aponte continua a ser erro — se a fonte ganhou uma
        divisão, alguém tem de decidir de que província se trata, e adivinhar
        aqui seria pior.
        """
        por_nome: dict[str, list[str]] = {}
        for codigo, nome in PROVINCE_BOUNDARY_CODES.items():
            por_nome.setdefault(sem_acentos(nome), []).append(codigo)

        pares = []
        for feature in descarrega("ADM1")["features"]:
            nome = feature["properties"]["shapeName"]
            codigos = por_nome.get(sem_acentos(nome))
            if not codigos:
                raise CommandError(
                    f"A fonte traz a divisão {nome!r}, que nenhum código de "
                    f"PROVINCE_BOUNDARY_CODES aponta. Ou a fonte mudou, ou a "
                    f"tabela está errada; adivinhar a correspondência aqui seria pior."
                )
            pares.append((feature, sorted(codigos)[0]))
        return pares

    def municipios(self) -> list[dict[str, Any]]:
        """Os municípios, cada um com a província a que pertence.

        A atribuição é espacial porque a fonte não a declara: o centro do
        município é testado contra os contornos provinciais. O teste usa a
        geometria fina, não a que vai para o ficheiro. A camada gravada é
        simplificada a 1,1 km para ser leve, e um município a quem essa
        simplificação chamasse outra província ficaria atribuído ao sítio
        errado. Em resumo: a divisão do ficheiro e a do raciocínio têm
        tolerâncias diferentes, de propósito.
        """
        finos = {
            codigo: limites(feature["geometry"], TOLERANCIA_TESTE)
            for feature, codigo in self.pares_provincia()
        }

        saida = []
        orphans = []
        resolvidos: set[str] = set()
        for feature in descarrega("ADM2")["features"]:
            nome = feature["properties"]["shapeName"]
            partes = limites(feature["geometry"], TOLERANCIA)
            if not partes:
                orphans.append(f"{nome} (sem contorno)")
                continue
            ponto = centro(partes)
            dono = [codigo for codigo, aneis in finos.items() if dentro(ponto, aneis)]
            if len(dono) != 1:
                orphans.append(f"{nome} (cai em {len(dono)} províncias)")
                continue
            rotulo, origem = self.rotulo(nome, dono[0])
            resolvidos.add(chave(origem))
            saida.append(
                feature_divisao(
                    {
                        "nome": rotulo,
                        "fonte_nome": nome,
                        "provincia": dono[0],
                        "ponto": ponto,
                    },
                    partes,
                )
            )
        if orphans:
            raise CommandError(
                "Sem província atribuível para: "
                + ", ".join(sorted(orphans)[:12])
                + ("…" if len(orphans) > 12 else "")
            )
        self.conflitos = sorted(
            f"{codigo}/{nome}"
            for codigo, lista in MUNICIPALITIES_BY_PROVINCE.items()
            for nome in lista
            if chave(nome) not in resolvidos
        )
        return saida

    def rotulo(self, nome: str, provincia: str) -> tuple[str, str]:
        """O nome que o mapa escreve, e o nome de que ele veio.

        A lista de municípios do projecto manda na ortografia, porque é ela que
        aparece nos filtros e nos registos dos imóveis. A fonte dá o mesmo
        município como `Cuito`, `N'harea`, `Baia Farta` ou `Amboim (Gabela)`, e
        um mapa que escreve `Cuito` ao lado de um filtro que diz `Kuito` obriga
        o utilizador a saber que são a mesma coisa.
        """
        nome = CORRECOES_FONTE.get(nome, nome)
        alvos = {chave(v) for v in variantes(nome)}
        for candidato in MUNICIPALITIES_BY_PROVINCE.get(provincia, []):
            if chave(candidato) in alvos:
                return candidato, candidato
        return nome, nome

    def documentos(self) -> dict[Path, dict[str, Any]]:
        """Os dois ficheiros a gravar, já com os metadados de origem.

        A chave do topo é `features` porque é a que o formato define. Um
        `{"provincias": [...]}` seria lido por um humano com a mesma facilidade,
        mas por um programa apenas como um pacote vazio: a camada não se
        desenhava e o mapa ficava sem nomes sem dizer porquê.
        """
        provincias = self.provincias()
        municipios = self.municipios()
        return {
            FICHEIRO_PROVINCIAS: {
                "type": "FeatureCollection",
                "fonte": metadados(),
                "features": provincias,
            },
            FICHEIRO_MUNICIPIOS: {
                "type": "FeatureCollection",
                "fonte": metadados(),
                "features": municipios,
            },
        }

    def escreve(self) -> None:
        DESTINO.mkdir(parents=True, exist_ok=True)
        for caminho, conteudo in self.documentos().items():
            caminho.write_text(
                json.dumps(conteudo, separators=(",", ":"), ensure_ascii=False),
                encoding="utf-8",
            )
            self.stdout.write(
                f"{caminho.name}: {caminho.stat().st_size / 1024:.0f} KB, "
                f"{len(conteudo['features'])} divisões"
            )
        self.avisa_conflitos()

    def avisa_conflitos(self) -> None:
        """Aponta os municípios da lista do projecto que a fonte não conhece.

        Não é um erro: a lista de municípios é do projecto e a fonte é de outra
        gente, e há divergências legítimas. Mas cada uma delas é um município
        que o utilizador pode filtrar e que o mapa não vai saber nomear, e isso
        tem de se ver. O geoBoundaries não tem os municípios urbanos de Luanda,
        e `Caxito`, `Ondjiva`, `N'dalatando` e `Dundo` são sedes de município,
        não municípios: decidir o que fazer com eles é do produto.
        """
        if not self.conflitos:
            return
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"{len(self.conflitos)} municípios da lista local não têm "
                f"contorno na fonte, e ficam sem nome no mapa:"
            )
        )
        for linha in self.conflitos:
            self.stdout.write(f"  - {linha}")

    def verifica_deriva(self) -> None:
        """Falha se os ficheiros versionados já não batem com a fonte."""
        documentos = self.documentos()

        divergentes = []
        for caminho, esperado in documentos.items():
            if not caminho.exists():
                divergentes.append(f"{caminho.name} não existe")
                continue
            if json.loads(caminho.read_text(encoding="utf-8")) != esperado:
                divergentes.append(caminho.name)
        if divergentes:
            raise CommandError(
                "Estes ficheiros não batem com a fonte: "
                + ", ".join(divergentes)
                + ". Corre `python manage.py build_admin_boundaries`."
            )
        self.stdout.write("As fronteiras versionadas batem com a fonte.")
