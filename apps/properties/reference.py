"""Listas fechadas de referência geográfica de Angola.

A divisão administrativa de Angola mudou nos últimos anos e não pára: há
províncias criadas que o geoBoundaries ainda não conhece. As duas coisas são
verdade ao mesmo tempo, e o projecto precisa de ambas:

- **A lista do projecto** é o que o cliente vê nos filtros. Vinte e uma
  províncias, com os municípios de cada uma, e é ela que manda.
- **A camada de contornos** vem do geoBoundaries, que tem dezoito. Onde não há
  contorno, o mapa não desenha a divisão; onde a lista do projecto dá nomes a
  duas entradas que a fonte tem como uma, o comando usa o mesmo contorno duas
  vezes em vez de falhar. Ver `PROVINCE_BOUNDARY_CODES`.

`municipality` no modelo aceita texto livre e não é validado contra a lista da
província: a equipa introduz o imóvel, e uma lista fechada que bloqueia a
operação por um município em falta serve mal. O formulário é que oferece as
opções.
"""

from __future__ import annotations

from typing import Final

# Vinte e uma províncias. O código é interno e estável; o rótulo é o que o
# cliente lê. `CUANDO` e `CUBANGO` são, na lista do projecto, duas entradas
# separadas, e partilham o mesmo contorno: a fonte tem um só. Ver
# `PROVINCE_BOUNDARY_CODES`.
ANGOLA_PROVINCES: Final[tuple[tuple[str, str], ...]] = (
    ("BENGO", "Bengo"),
    ("BENGUELA", "Benguela"),
    ("BIE", "Bié"),
    ("CABINDA", "Cabinda"),
    ("CUANDO", "Cuando"),
    ("CUBANGO", "Cubango"),
    ("CUANZA_NORTE", "Cuanza Norte"),
    ("CUANZA_SUL", "Cuanza Sul"),
    ("CUNENE", "Cunene"),
    ("HUAMBO", "Huambo"),
    ("HUILA", "Huíla"),
    ("ICOLO_E_BENGO", "Icolo e Bengo"),
    ("LUANDA", "Luanda"),
    ("LUNDA_NORTE", "Lunda Norte"),
    ("LUNDA_SUL", "Lunda Sul"),
    ("MALANJE", "Malanje"),
    ("MOXICO", "Moxico"),
    ("MOXICO_LESTE", "Moxico Leste"),
    ("NAMIBE", "Namibe"),
    ("UIGE", "Uíge"),
    ("ZAIRE", "Zaire"),
)

ANGOLA_PROVINCE_LABELS: Final[dict[str, str]] = dict(ANGOLA_PROVINCES)

# Os municípios de cada província, na grafia que o projecto adoptou. As listas
# são fechadas e completas para as vinte e uma entradas acima: uma província sem
# lista é uma província em que o formulário não pode oferecer nada, e era esse o
# estado de sete delas.
MUNICIPALITIES_BY_PROVINCE: Final[dict[str, tuple[str, ...]]] = {
    "BENGO": (
        "Ambriz",
        "Bula Atumba",
        "Dande",
        "Dembos",
        "Nambuangongo",
        "Pango Aluquém",
    ),
    "BENGUELA": (
        "Balombo",
        "Baía Farta",
        "Benguela",
        "Bocoio",
        "Caimbambo",
        "Catumbela",
        "Chongorói",
        "Cubal",
        "Ganda",
        "Lobito",
    ),
    "BIE": (
        "Andulo",
        "Camacupa",
        "Catabola",
        "Chinguar",
        "Chitembo",
        "Cuemba",
        "Kuito",
        "Cunhinga",
        "Nharea",
    ),
    "CABINDA": (
        "Belize",
        "Buco-Zau",
        "Cabinda",
        "Cacongo",
    ),
    "CUANDO": (
        "Calai",
        "Dirico",
        "Mavinga",
        "Menongue",
        "Nancova",
        "Rivungo",
    ),
    "CUBANGO": (
        "Caiundo",
        "Calai",
        "Cuangar",
        "Cuchi",
        "Cuito Cuanavale",
        "Menongue",
    ),
    "CUANZA_NORTE": (
        "Ambaca",
        "Banga",
        "Bolongongo",
        "Cambambe",
        "Cazengo",
        "Golungo Alto",
        "Lucala",
        "Quiculungo",
        "Samba Caju",
    ),
    "CUANZA_SUL": (
        "Amboim",
        "Cassongue",
        "Conda",
        "Ebo",
        "Libolo",
        "Mussende",
        "Porto Amboim",
        "Quibala",
        "Quilenda",
        "Seles",
        "Sumbe",
    ),
    "CUNENE": (
        "Cahama",
        "Cuanhama",
        "Curoca",
        "Cuvelai",
        "Namacunde",
        "Ombadja",
    ),
    "HUAMBO": (
        "Bailundo",
        "Caála",
        "Catchiungo",
        "Chicala-Choloanga",
        "Chinjenje",
        "Ecunha",
        "Huambo",
        "Londuimbali",
        "Longonjo",
        "Mungo",
        "Ucuma",
    ),
    "HUILA": (
        "Caconda",
        "Caluquembe",
        "Cacula",
        "Chibia",
        "Chicomba",
        "Chipindo",
        "Cuvango",
        "Gambos",
        "Humpata",
        "Jamba",
        "Lubango",
        "Matala",
        "Quipungo",
    ),
    "ICOLO_E_BENGO": (
        "Catete",
        "Bom Jesus",
        "Cabiri",
        "Calumbo",
        "Cassoneca",
        "Quiminha",
    ),
    "LUANDA": (
        "Ingombota",
        "Kilamba Kiaxi",
        "Maianga",
        "Rangel",
        "Sambizanga",
        "Samba",
        "Talatona",
        "Urbanização Nova Vida",
    ),
    "LUNDA_NORTE": (
        "Cambulo",
        "Capenda-Camulemba",
        "Caungula",
        "Chitato",
        "Cuango",
        "Cuílo",
        "Lubalo",
        "Lucapa",
        "Xá-Muteba",
    ),
    "LUNDA_SUL": (
        "Cacolo",
        "Dala",
        "Muconda",
        "Saurimo",
    ),
    "MALANJE": (
        "Cacuso",
        "Calandula",
        "Cambundi-Catembo",
        "Cangandala",
        "Caombo",
        "Cuaba Nzogo",
        "Cunda-Dia-Baze",
        "Kiwaba Nzoji",
        "Luquembo",
        "Malanje",
        "Marimba",
        "Massango",
        "Mucari",
        "Quela",
        "Quirima",
    ),
    "MOXICO": (
        "Camanongue",
        "Cameia",
        "Léua",
        "Luacano",
        "Luau",
        "Luchazes",
        "Luena",
    ),
    "MOXICO_LESTE": (
        "Cazombo",
        "Lumbala Nguimbo",
        "Macondo",
        "Lóvua",
        "Lutembo",
    ),
    "NAMIBE": (
        "Bibala",
        "Camucuio",
        "Moçâmedes",
        "Tômbwa",
        "Virei",
    ),
    "UIGE": (
        "Alto Cauale",
        "Ambuila",
        "Bembe",
        "Buengas",
        "Bungo",
        "Damba",
        "Maquela do Zombo",
        "Milunga",
        "Mucaba",
        "Negage",
        "Puri",
        "Quimbele",
        "Quitexe",
        "Uíge",
        "Songo",
    ),
    "ZAIRE": (
        "Cuimba",
        "M'banza-Kongo",
        "Nóqui",
        "Nzeto",
        "Soyo",
        "Tomboco",
    ),
}

# A camada de contornos tem dezoito divisões e a lista do projecto tem vinte e
# uma entradas. São coisas diferentes, e o mapa não pode fingir que são a mesma:
# esta tabela diz que divisão da fonte — pelo nome que a fonte lhe dá — serve de
# contorno a cada código, e a ausência de uma entrada é uma resposta: aquela
# divisão não tem contorno.
#
# `CUANDO` e `CUBANGO` apontam para a mesma divisão, que a fonte chama
# "Cuando Cubango": são duas entradas da lista do projecto e um só contorno, e
# este é o único sítio onde isso é verdade. `ICOLO_E_BENGO` e `MOXICO_LESTE` não
# têm entrada porque a fonte não tem o contorno delas, e apontá-las para o
# contorno de outra província desenharia uma mentira.
PROVINCE_BOUNDARY_CODES: Final[dict[str, str]] = {
    "BENGO": "Bengo",
    "BENGUELA": "Benguela",
    "BIE": "Bié",
    "CABINDA": "Cabinda",
    "CUANDO": "Cuando Cubango",
    "CUBANGO": "Cuando Cubango",
    "CUANZA_NORTE": "Cuanza Norte",
    "CUANZA_SUL": "Cuanza Sul",
    "CUNENE": "Cunene",
    "HUAMBO": "Huambo",
    "HUILA": "Huíla",
    "LUANDA": "Luanda",
    "LUNDA_NORTE": "Lunda Norte",
    "LUNDA_SUL": "Lunda Sul",
    "MALANJE": "Malanje",
    "MOXICO": "Moxico",
    "NAMIBE": "Namibe",
    "UIGE": "Uíge",
    "ZAIRE": "Zaire",
}


def boundary_name_for(province: str) -> str | None:
    """Devolve o nome que a fonte dá à divisão que contorna a província, ou `None`."""
    return PROVINCE_BOUNDARY_CODES.get(province)


def provinces_without_boundary() -> tuple[str, ...]:
    """As províncias do projecto que a fonte de mapas ainda não desenha."""
    return tuple(
        rotulo for codigo, rotulo in ANGOLA_PROVINCES if codigo not in PROVINCE_BOUNDARY_CODES
    )


def province_codes_by_boundary() -> dict[str, tuple[str, ...]]:
    """Devolve, por nome de contorno, os códigos de província que ele cobre.

    É o caminho inverso de `PROVINCE_BOUNDARY_CODES`, e existe porque quem pergunta
    é o mapa, que conhece o nome que a fonte deu, e quem responde é o formulário,
    cujas opções são os códigos da lista do projecto. Os dois lados precisam de
    traduzir, e a tradução feita aqui é a mesma que valida o filtro.

    O valor é uma lista e não um código porque `CUANDO` e `CUBANGO` são duas
    entradas do projecto para um só contorno. Escolher uma delas sem saber qual é
    a certa seria inventar a resposta, e o que o mapa sabe é o contorno.
    """
    por_nome: dict[str, list[str]] = {}
    for codigo, nome in PROVINCE_BOUNDARY_CODES.items():
        por_nome.setdefault(nome, []).append(codigo)
    return {nome: tuple(sorted(codigos)) for nome, codigos in por_nome.items()}


# Os municípios todos, sem repetir os que aparecem em duas listas. `Calai` e
# `Menongue` estão em `CUANDO` e em `CUBANGO`, e o formulário de "Todas as
# províncias" não pode oferecer o mesmo nome duas vezes.
ALL_MUNICIPALITIES: Final[tuple[str, ...]] = tuple(
    sorted({municipio for lista in MUNICIPALITIES_BY_PROVINCE.values() for municipio in lista})
)


def municipalities_for(province: str) -> tuple[str, ...]:
    """Devolve os municípios de uma província; todos, se a província vier vazia.

    O contrato é deliberadamente largo porque é o que os dois consumidores
    precisam: o filtro com "Todas as províncias" e o formulário de curadoria com
    a província por escolher precisam ambos da lista completa, e nenhum dos dois
    tem nada a ganhar com um `if` seu.
    """
    if not province:
        return ALL_MUNICIPALITIES
    return MUNICIPALITIES_BY_PROVINCE.get(province, ())


def province_label(value: str) -> str:
    """Traduz o código interno da província para o nome apresentado ao utilizador."""
    return ANGOLA_PROVINCE_LABELS.get(value, value)
