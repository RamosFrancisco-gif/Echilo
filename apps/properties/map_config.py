"""Configuração do mapa da ficha de curadoria (§2.3).

O catálogo e a ficha de curadoria mostram o mesmo mapa e fazem perguntas
diferentes ao mesmo ponto. O catálogo quer uma frase — "província de Luanda" — e
para isso chega o nome que a fonte deu ao contorno. A ficha quer preencher um
`select` cujas opções são os códigos de `ANGOLA_PROVINCES`, e esse nome não é
sempre uma opção: a fonte chama "Cuando Cubango" ao que o projecto divide em
`CUANDO` e `CUBANGO`.

Por isso a correspondência entra no JavaScript, e entra escrita pelo servidor a
partir de `PROVINCE_BOUNDARY_CODES` — a mesma tabela que gera os ficheiros
versionados e que valida o filtro. Um segundo dicionário em JavaScript seria uma
segunda verdade, e a segunda é sempre a que ninguém actualiza.
"""

from __future__ import annotations

from apps.core.maps import area_map_config

from .reference import province_codes_by_boundary


def curation_map_config() -> dict[str, object]:
    """Devolve a configuração do seletor de pin, com o que o catálogo não precisa.

    As chaves são as de `area_map_config()` — os dois mapas partilham tiles,
    centro e ficheiros de fronteira — mais a correspondência que só a ficha usa.
    """
    config = area_map_config()
    config["provinciasPorFronteira"] = {
        nome: list(codigos) for nome, codigos in province_codes_by_boundary().items()
    }
    return config
