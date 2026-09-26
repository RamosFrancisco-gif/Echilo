"""Listas fechadas de referência geográfica de Angola.

Fonte primária: divisão administrativa oficial angolana. As listas são
extendíveis sem migração — `municipality` aceita texto livre quando a província
ainda não tem municípios mapeados, para não bloquear a operação.
"""

from __future__ import annotations

from typing import Final

ANGOLA_PROVINCES: Final[tuple[tuple[str, str], ...]] = (
    ("BENGO", "Bengo"),
    ("BENGUELA", "Benguela"),
    ("BIE", "Bié"),
    ("CABINDA", "Cabinda"),
    ("CUANDO_CUBANGO", "Cuando Cubango"),
    ("CUANZA_NORTE", "Cuanza Norte"),
    ("CUANZA_SUL", "Cuanza Sul"),
    ("CUNENE", "Cunene"),
    ("HUAMBO", "Huambo"),
    ("HUILA", "Huíla"),
    ("LUANDA", "Luanda"),
    ("LUNDA_NORTE", "Lunda Norte"),
    ("LUNDA_SUL", "Lunda Sul"),
    ("MALANJE", "Malanje"),
    ("MOXICO", "Moxico"),
    ("NAMIBE", "Namibe"),
    ("UIGE", "Uíge"),
    ("ZAIRE", "Zaire"),
)

ANGOLA_PROVINCE_LABELS: Final[dict[str, str]] = dict(ANGOLA_PROVINCES)

# Cobertura inicial das capitais e municípios com maior procura na plataforma.
MUNICIPALITIES_BY_PROVINCE: Final[dict[str, tuple[str, ...]]] = {
    "LUANDA": ("Luanda", "Belas", "Viana", "Talatona", "Cacuaco", "Cazenga", "Kilamba Kiaxi"),
    "BENGUELA": (
        "Benguela",
        "Lobito",
        "Catumbela",
        "Baía Farta",
        "Balombo",
        "Bocoio",
        "Caimbambo",
        "Cubal",
        "Ganda",
    ),
    "CUNENE": ("Ondjiva", "Cahama", "Namacunde", "Ombadja", "Cuvelai"),
    "CUANZA_SUL": ("Sumbe", "Porto Amboim", "Gabela", "Waku Kungo", "Quibala"),
    "BENGO": ("Caxito", "Ambriz", "Nambuangongo", "Dembos", "Bula Atumba"),
    "CUANZA_NORTE": ("N\'dalatando", "Golungo Alto", "Lucala", "Cambambe", "Samba Caju"),
    "LUNDA_NORTE": ("Dundo", "Lucapa", "Cambulo", "Caungula", "Xá-Muteba"),
    "LUNDA_SUL": ("Saurimo", "Cacolo", "Dala", "Muconda"),
    "CUANDO_CUBANGO": ("Menongue", "Cuito Cuanavale", "Cuchi", "Cuangar", "Dirico"),
    "MOXICO": ("Luena", "Luau", "Camanongue", "Cameia", "Alto Zambeze"),
    "BIE": ("Kuito", "Andulo", "Camacupa", "Catabola", "Chinguar", "Nharea"),
}


def municipalities_for(province: str) -> tuple[str, ...]:
    """Devolve os municípios conhecidos de uma província, ou lista vazia."""
    return MUNICIPALITIES_BY_PROVINCE.get(province, ())


def province_label(value: str) -> str:
    """Traduz o código interno da província para o nome apresentado ao utilizador."""
    return ANGOLA_PROVINCE_LABELS.get(value, value)
