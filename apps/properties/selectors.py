"""Serviço público de consulta de imóveis.

É o único ponto de entrada que o app `assistant` usa para ler o catálogo, o que
cumpre a regra de isolamento entre contextos descrita em §4 do steering.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, QuerySet

from apps.core.geo import circle_bounding_box, haversine_metres
from apps.core.validators import to_decimal_or_none, validate_center, validate_search_radius_m

from .models import Property, PropertyImage
from .reference import municipalities_for

# Um mapa com milhares de pinos deixa de ser um mapa: o browser passa a desenhar
# o mesmo círculo sobrepostas vezes. Acima do limite mostramos os mais recentes e
# dizemos quantos ficaram de fora.
MAP_MARKER_LIMIT = 250


@dataclass(frozen=True)
class PropertyFilters:
    """Filtros de pesquisa suportados pela navegação e pelo assistente."""

    purpose: str | None = None
    property_type: str | None = None
    province: str | None = None
    municipality: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None
    min_area_m2: int | None = None
    max_area_m2: int | None = None
    has_water_tank: bool | None = None
    has_generator: bool | None = None
    is_furnished: bool | None = None
    keyword: str | None = None
    center_lat: Decimal | None = None
    center_lon: Decimal | None = None
    radius_m: int | None = None

    def has_area(self) -> bool:
        """Diz se o círculo desenhado no mapa tem centro e raio utilizáveis."""
        return None not in (self.center_lat, self.center_lon, self.radius_m)

    def as_query_params(self) -> dict[str, str]:
        """Devolve os parâmetros no mesmo vocabulário que o formulário publica."""
        params = {
            "purpose": self.purpose or "",
            "type": self.property_type or "",
            "province": self.province or "",
            "municipality": self.municipality or "",
            "min_price": str(self.min_price) if self.min_price is not None else "",
            "max_price": str(self.max_price) if self.max_price is not None else "",
            "min_bedrooms": str(self.min_bedrooms) if self.min_bedrooms is not None else "",
            "min_area_m2": str(self.min_area_m2) if self.min_area_m2 is not None else "",
            "has_water_tank": "on" if self.has_water_tank else "",
            "has_generator": "on" if self.has_generator else "",
            "is_furnished": "on" if self.is_furnished else "",
            "keyword": self.keyword or "",
            "center_lat": f"{self.center_lat:.6f}" if self.center_lat is not None else "",
            "center_lon": f"{self.center_lon:.6f}" if self.center_lon is not None else "",
            "radius_m": str(self.radius_m) if self.radius_m is not None else "",
        }
        return {key: value for key, value in params.items() if value}

    def chip_query(self, **overrides: str) -> str:
        """Devolve a query string dos separadores, mantendo os outros filtros.

        Trocar de separador não pode apagar o resto da pesquisa.
        """
        params = self.as_query_params()
        for key, value in overrides.items():
            if value:
                params[key] = value
            else:
                params.pop(key, None)
        return urlencode(params)

    @classmethod
    def from_query(cls, query: object) -> "PropertyFilters":
        """Interpreta a query string pública, ignorando valores malformados."""
        get = getattr(query, "get", None)
        if get is None:
            return cls()

        def text(key: str) -> str | None:
            value = get(key)
            return value.strip() or None if isinstance(value, str) else None

        def flag(key: str) -> bool | None:
            value = get(key)
            return None if value is None else str(value) in {"on", "true", "1"}

        return cls(
            purpose=text("purpose"),
            property_type=text("type"),
            province=text("province"),
            municipality=text("municipality"),
            min_price=_to_decimal(get("min_price")),
            max_price=_to_decimal(get("max_price")),
            min_bedrooms=_to_int(get("min_bedrooms")),
            min_area_m2=_to_int(get("min_area_m2")),
            has_water_tank=flag("has_water_tank"),
            has_generator=flag("has_generator"),
            is_furnished=flag("is_furnished"),
            keyword=text("keyword"),
            **_area_from_query(get),
        )


def _area_from_query(get: object) -> dict[str, object]:
    """Lê a área desenhada no mapa, ou não devolve área nenhuma.

    Meio círculo não é uma pesquisa: se faltar o centro ou o raio, ou se algum
    dos três for malformado, a área é descartada e o catálogo volta ao normal.
    """
    if not callable(get):
        return {}

    latitude = to_decimal_or_none(get("center_lat"))
    longitude = to_decimal_or_none(get("center_lon"))
    radius = _to_int(get("radius_m"))
    if latitude is None or longitude is None or radius is None:
        return {}

    try:
        validate_center(latitude, longitude)
        validate_search_radius_m(radius)
    except ValidationError:
        return {}

    return {"center_lat": latitude, "center_lon": longitude, "radius_m": radius}


def _to_int(value: object) -> int | None:
    """Converte um parâmetro de URL em inteiro, ignorando valores inválidos."""
    try:
        return int(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_decimal(value: object) -> Decimal | None:
    """Converte um parâmetro de URL em Decimal, ignorando valores inválidos."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


class PropertyQueryService:
    """Leitura do catálogo sem expor QuerySets ao exterior das apps."""

    @staticmethod
    def base_queryset() -> QuerySet[Property]:
        """Devolve apenas imóveis publicados, já preparado para listagem."""
        return (
            Property.objects.published()
            .select_related("owner", "curated_by")
            .prefetch_related("images")
        )

    @classmethod
    def apply_filters(
        cls, filters: PropertyFilters, *, base: QuerySet[Property] | None = None
    ) -> QuerySet[Property]:
        """Aplica os filtros de pesquisa, deixando a área de fora.

        A área não entra aqui por uma razão visual: o círculo desenha-se por
        cima dos pinos, e se os pinos desaparecerem à medida que o raio aperta,
        desenhar parece não fazer nada.

        `base` existe porque os pinos do mapa não precisam de fotografia. Puxar a
        galeria de todos os imóveis do país para mostrar um ponto seria o
        caminho mais caro para o ganho mais pequeno.
        """
        queryset = cls.base_queryset() if base is None else base
        if filters.purpose:
            queryset = queryset.filter(purpose=filters.purpose)
        if filters.property_type:
            queryset = queryset.filter(type=filters.property_type)
        if filters.province:
            queryset = queryset.filter(province_ref=filters.province)
        if filters.municipality:
            # Em Angola a pesquisa é quase sempre por bairro, por isso o termo é
            # procurado no município e na localidade.
            area = filters.municipality
            queryset = queryset.filter(Q(municipality__iexact=area) | Q(locality__iexact=area))
        if filters.min_price is not None:
            queryset = queryset.filter(price__gte=filters.min_price)
        if filters.max_price is not None:
            queryset = queryset.filter(price__lte=filters.max_price)
        if filters.min_bedrooms is not None:
            queryset = queryset.filter(bedrooms__gte=filters.min_bedrooms)
        if filters.max_bedrooms is not None:
            queryset = queryset.filter(bedrooms__lte=filters.max_bedrooms)
        if filters.min_area_m2 is not None:
            queryset = queryset.filter(Q(area_m2__gte=filters.min_area_m2) | Q(land_area_m2__gte=filters.min_area_m2))
        if filters.max_area_m2 is not None:
            queryset = queryset.filter(Q(area_m2__lte=filters.max_area_m2) | Q(land_area_m2__lte=filters.max_area_m2))
        if filters.has_water_tank is not None:
            queryset = queryset.filter(has_water_tank=filters.has_water_tank)
        if filters.has_generator is not None:
            queryset = queryset.filter(has_generator=filters.has_generator)
        if filters.is_furnished is not None:
            queryset = queryset.filter(is_furnished=filters.is_furnished)
        if filters.keyword:
            term = filters.keyword.strip()
            queryset = queryset.filter(
                Q(title__icontains=term)
                | Q(description__icontains=term)
                | Q(locality__icontains=term)
                | Q(municipality__icontains=term)
                | Q(reference__iexact=term)
            )
        return queryset

    @classmethod
    def search(cls, filters: PropertyFilters) -> QuerySet[Property] | list[Property]:
        """Aplica os filtros de pesquisa sobre o conjunto publicado.

        Sem área devolvemos um `QuerySet` e quem chamou continua a mandar na
        ordenação. Com área, o SQL fica só com a caixa envolvente e a distância
        exacta é calculada aqui: sem PostGIS não há `ST_Distance_Sphere`, e o
        número de imóveis dentro de 50 km cabe na memória sem pensar muito.
        """
        queryset = cls.apply_filters(filters)
        if filters.has_area():
            return cls.within_area(queryset, filters)
        return queryset

    @staticmethod
    def map_base() -> QuerySet[Property]:
        """Base leve para o que não mostra fotografias."""
        return Property.objects.published()

    @classmethod
    def map_markers(
        cls, filters: PropertyFilters, *, limit: int = MAP_MARKER_LIMIT
    ) -> tuple[list[Property], int]:
        """Imóveis a marcar no mapa, e quantos existem no total.

        A área não entra, pelos mesmos motivos que em `apply_filters`. Só entram
        imóveis com coordenadas: sem pin verificado não há nada a marcar, e
        marcá-lo na morada textual seria inventar a localização (§2.3).
        """
        queryset = (
            cls.apply_filters(filters, base=cls.map_base())
            .exclude(latitude__isnull=True)
            .exclude(longitude__isnull=True)
        )
        marked = list(
            queryset.only(
                "id",
                "reference",
                "title",
                "price",
                "purpose",
                "province_ref",
                "municipality",
                # Sem `locality` no `.only()`, cada pino ia buscá-lo à parte: uma
                # consulta por imóvel, só para escrever a localidade no rótulo.
                "locality",
                "latitude",
                "longitude",
                "published_at",
            ).order_by("-published_at")[: limit + 1]
        )
        if len(marked) <= limit:
            return marked, len(marked)
        # Em vez de fingir que o catálogo cabe no ecrã, dizemos quantos ficaram
        # de fora. O mapa dá a vizinhança; a contagem é do servidor.
        return marked[:limit], queryset.count()

    @staticmethod
    def within_area(
        queryset: QuerySet[Property], filters: PropertyFilters
    ) -> list[Property]:
        """Mantém só o que cai dentro do círculo, do mais perto ao mais longe.

        A primeira fase é a caixa envolvente, que o índice absorve; a segunda é
        a distância verdadeira, que decide. O resultado é uma lista porque a
        ordenação passa a ser por distância e o MySQL não sabe ordená-la.
        """
        center_lat = float(filters.center_lat)
        center_lon = float(filters.center_lon)
        radius_m = float(filters.radius_m)
        lat_min, lat_max, lon_min, lon_max = circle_bounding_box(
            center_lat, center_lon, radius_m
        )

        # Imóveis sem coordenadas não passam num `BETWEEN` e ficam de fora sem
        # tratamento especial: sem pin verificado não há onde os procurar.
        candidates = queryset.filter(
            latitude__gte=lat_min,
            latitude__lte=lat_max,
            longitude__gte=lon_min,
            longitude__lte=lon_max,
        )

        measured = [
            (
                haversine_metres(
                    center_lat, center_lon, float(prop.latitude), float(prop.longitude)
                ),
                prop,
            )
            for prop in candidates
        ]
        inside = [(distance, prop) for distance, prop in measured if distance <= radius_m]
        return [prop for _, prop in sorted(inside, key=lambda pair: pair[0])]

    @classmethod
    def get_published(cls, *, reference: str) -> Property | None:
        """Devolve um imóvel publicado pela referência, ou `None`."""
        return cls.base_queryset().filter(reference__iexact=reference.strip()).first()

    @classmethod
    def featured(cls, *, limit: int = 6) -> QuerySet[Property]:
        """Imóveis em destaque para a página inicial, sem custo extra de consultas."""
        return (
            cls.base_queryset()
            .annotate(photo_total=Count("images"))
            .order_by("-published_at", "-photo_total")[:limit]
        )

    @classmethod
    def by_ids(cls, references: list[str]) -> list[Property]:
        """Devolve vários imóveis publicados preservando a ordem pedida."""
        found = {prop.reference: prop for prop in cls.base_queryset().filter(reference__in=references)}
        return [found[ref] for ref in references if ref in found]

    @classmethod
    def cover_image(cls, prop: Property) -> PropertyImage | None:
        """Escolhe a fotografia de capa: a de menor ordem, ou a primeira disponível."""
        prefetched = getattr(prop, "_prefetched_objects_cache", {})
        images = prefetched.get("images") or list(prop.images.all())
        return images[0] if images else None

    @classmethod
    def available_areas(cls, province: str = "") -> list[str]:
        """Municípios e localidades com imóveis publicados, para alimentar os filtros.

        A província é um filtro, não uma condição: sem ela o resultado é o de
        sempre, e é isso que o formulário mostra quando ninguém escolheu uma.
        """
        consulta = cls.base_queryset().exclude(municipality="")
        if province:
            consulta = consulta.filter(province_ref=province)
        return list(
            consulta.values_list("municipality", flat=True).distinct().order_by("municipality")
        )

    @classmethod
    def municipality_suggestions(cls, province: str = "") -> list[str]:
        """O que o campo de município oferece, para a província escolhida.

        São duas fontes e as duas são verdade: a lista de referência, para quem
        filtra por uma província que ainda não tem imóveis, e as áreas que existem
        no catálogo, porque o campo também aceita localidade e `municipality` não
        é a única coluna que a pesquisa lê.

        Vive aqui e não na view porque a vista e o endpoint que responde à mudança
        de província têm de oferecer exactamente a mesma coisa; duas listas
        parecidas divergem na segunda alteração do utilizador.
        """
        return sorted(
            {*municipalities_for(province), *cls.available_areas(province)}
        )
