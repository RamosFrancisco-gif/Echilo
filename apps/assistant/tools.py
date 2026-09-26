"""Ferramentas que dão à IA acesso de leitura ao catálogo real de imóveis.

Cada função devolve apenas dados publicados e verificados, em JSON simples, para
que o modelo nunca invente Attributes. Nenhuma função escreve na base de dados.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.properties.models import Property
from apps.properties.reference import ANGOLA_PROVINCE_LABELS
from apps.properties.selectors import PropertyFilters, PropertyQueryService

TOOL_SEARCH_PROPERTIES = "search_properties"
TOOL_GET_PROPERTY_DETAILS = "get_property_details"
TOOL_COMPARE_PROPERTIES = "compare_properties"

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH_PROPERTIES,
            "description": (
                "Procura imóveis publicados na plataforma. Usa esta ferramenta sempre que "
                "o cliente pedir imóveis por zona, tipo, preço ou características."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": ["string", "null"],
                        "enum": ["RENT", "SALE", None],
                        "description": "RENT para arrendar, SALE para vender.",
                    },
                    "property_type": {
                        "type": ["string", "null"],
                        "enum": [value for value, _ in Property.Type.choices] + [None],
                        "description": "HOUSE, APARTMENT, LAND, OFFICE, SHOP ou WAREHOUSE.",
                    },
                    "province": {
                        "type": ["string", "null"],
                        "enum": list(ANGOLA_PROVINCE_LABELS) + [None],
                        "description": "Código da província, por exemplo LUANDA.",
                    },
                    "municipality": {
                        "type": ["string", "null"],
                        "description": "Nome do município ou localidade, por exemplo Talatona.",
                    },
                    "min_price": {
                        "type": ["number", "string", "null"],
                        "description": (
                            "Preço mínimo em Kwanza, só números. Usa null quando não houver "
                            "limite. O cliente diz 'até 500 mil', escreve 500000."
                        ),
                    },
                    "max_price": {
                        "type": ["number", "string", "null"],
                        "description": "Preço máximo em Kwanza, só números, ou null.",
                    },
                    "min_bedrooms": {
                        "type": ["integer", "string", "null"],
                        "description": "Número mínimo de quartos, ou null.",
                    },
                    "has_water_tank": {"type": ["boolean", "null"]},
                    "has_generator": {"type": ["boolean", "null"]},
                    "is_furnished": {"type": ["boolean", "null"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_GET_PROPERTY_DETAILS,
            "description": (
                "Devolve todos os detalhes confirmados de um imóvel pela sua referência, "
                "por exemplo ECH-LU-0142. Usar antes de afirmar preço ou características."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "Referência interna do imóvel, por exemplo ECH-LU-0142.",
                    }
                },
                "required": ["reference"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_COMPARE_PROPERTIES,
            "description": "Compara até três imóveis publicados lado a lado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de referências, com máximo de três.",
                    }
                },
                "required": ["references"],
            },
        },
    },
]


def _format_price(amount: Decimal | None) -> str:
    """Formata o preço em Kwanza segundo §2.5 do steering."""
    if amount is None:
        return "sob consulta"
    formatted = f"{amount:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{formatted} Kz"


def _summarise(prop: Property) -> dict[str, Any]:
    """Resumo compacto para resultados de pesquisa."""
    return {
        "reference": prop.reference,
        "title": prop.title,
        "type": prop.get_type_display(),
        "purpose": prop.get_purpose_display(),
        "province": prop.get_province_ref_display(),
        "municipality": prop.municipality,
        "locality": prop.locality,
        "price": _format_price(prop.price),
        "bedrooms": prop.bedrooms,
        "bathrooms": prop.bathrooms,
        "area_m2": prop.area_m2,
        "land_area_m2": prop.land_area_m2,
        "url": prop.get_absolute_url(),
    }


def _details(prop: Property) -> dict[str, Any]:
    """Ficha completa com as características confirmadas pela equipa."""
    features = [
        label
        for label, active in (
            ("tanque de água", prop.has_water_tank),
            ("gerador", prop.has_generator),
            ("mobiliado", prop.is_furnished),
            ("quintal", prop.has_garden),
            ("piscina", prop.has_pool),
            ("parque de estacionamento", prop.has_parking),
        )
        if active
    ]
    return {
        **_summarise(prop),
        "description": prop.description,
        "accepts_annual_payment": prop.accepts_annual_payment,
        "lease_term_months": prop.lease_term_months,
        "features": features,
        "documentation_status": (
            "Documentação legal verificada pela equipa."
            if not prop.missing_verified_documents()
            else "Documentação legal em verificação."
        ),
        "exact_address_note": (
            "A morada exacta só é prestada depois de a equipa confirmar uma visita."
        ),
    }


def _coerce_decimal(value: object) -> Decimal | None:
    """Converte um argumento do modelo em Decimal, ignorando valores inválidos.

    O modelo escreve por vezes "450.000 Kz" ou "450 000". Em Angola o ponto separa
    milhares e a vírgula separa decimais, por isso a limpeza inverte essa leitura.
    """
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        cleaned = value.replace("\u00a0", " ").strip().lower()
        for symbol in ("kz", "kzs", "aoa", "kz/m", "/mês", "/mes"):
            cleaned = cleaned.replace(symbol, "")
        cleaned = cleaned.replace(" ", "")
        # Só é ponto de milhar se não houver vírgula a marcar decimais.
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(".", "")
        if not cleaned:
            return None
        value = cleaned
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    """Converte um argumento do modelo em inteiro, ignorando valores inválidos."""
    number = _coerce_decimal(value)
    if number is None:
        return None
    try:
        return int(number)
    except (ValueError, OverflowError):
        return None


def run_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Executa uma ferramenta e devolve o resultado em JSON legível pelo modelo."""
    args = arguments or {}
    match name:
        case "search_properties":
            filters = PropertyFilters(
                purpose=args.get("purpose") or None,
                property_type=args.get("property_type") or None,
                province=args.get("province") or None,
                municipality=args.get("municipality") or None,
                min_price=_coerce_decimal(args.get("min_price")),
                max_price=_coerce_decimal(args.get("max_price")),
                min_bedrooms=_coerce_int(args.get("min_bedrooms")),
                has_water_tank=args.get("has_water_tank") if isinstance(args.get("has_water_tank"), bool) else None,
                has_generator=args.get("has_generator") if isinstance(args.get("has_generator"), bool) else None,
                is_furnished=args.get("is_furnished") if isinstance(args.get("is_furnished"), bool) else None,
            )
            results = [_summarise(prop) for prop in PropertyQueryService.search(filters)[:8]]
            return json.dumps(
                {
                    "total_found": len(results),
                    "results": results,
                    "note": "Todos os imóveis estão publicados e validados pela equipa."
                    if results
                    else "Não há imóveis publicados com estes critérios.",
                },
                ensure_ascii=False,
            )

        case "get_property_details":
            reference = str(args.get("reference", "")).strip()
            prop = PropertyQueryService.get_published(reference=reference)
            if prop is None:
                return json.dumps(
                    {
                        "found": False,
                        "message": "Não existe nenhum imóvel publicado com esta referência.",
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"found": True, **(_details(prop))}, ensure_ascii=False)

        case "compare_properties":
            references = [str(ref).strip() for ref in (args.get("references") or [])][:3]
            properties = PropertyQueryService.by_ids(references)
            return json.dumps(
                {
                    "requested": references,
                    "found": [_details(prop) for prop in properties],
                    "note": "Imóveis pedidos que não estão publicados não aparecem acima.",
                },
                ensure_ascii=False,
            )

    return json.dumps(
        {"error": f"Ferramenta desconhecida: {name}"},
        ensure_ascii=False,
    )
