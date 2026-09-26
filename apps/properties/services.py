"""Regras sobre imóveis: escritas da equipa e preparação dos dados do mapa."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.money import kwanza_compact

from .models import OwnerProfile, Property, PropertyDocument, PropertyImage, PropertySubmission
from .reference import ANGOLA_PROVINCES
from .selectors import MAP_MARKER_LIMIT, PropertyFilters, PropertyQueryService

User = get_user_model()

REFERENCE_PREFIX = "ECH"

PROVINCE_NAMES = dict(ANGOLA_PROVINCES)


def build_map_payload(
    filters: PropertyFilters, *, limit: int = MAP_MARKER_LIMIT
) -> dict[str, object]:
    """Prepara os pinos que o mapa desenha e a caixa onde a vista abre.

    Sai tudo já formatado. O browser não sabe escrever Kwanza nem montar a URL
    de um imóvel: se um dia o pino disser `85000000` e o cartão disser `85 M Kz`,
    foi porque alguém formatou o preço só de um lado.
    """
    marked, total = PropertyQueryService.map_markers(filters, limit=limit)
    items = [
        {
            "id": prop.id,
            "titulo": prop.title,
            "preco": kwanza_compact(prop.price),
            "por_mes": prop.purpose == Property.Purpose.RENT,
            "local": _local_label(prop),
            "url": reverse("properties:property_detail", kwargs={"reference": prop.reference}),
            "lat": float(prop.latitude),
            "lon": float(prop.longitude),
        }
        for prop in marked
    ]
    return {
        "items": items,
        "total": total,
        "mostrados": len(items),
        # Um mapa com pinos a menos do que o número anunciado faz o utilizador
        # procurar um imóvel que "não está lá". Melhor dizer que ficou de fora.
        "incompletos": total > len(items),
        "caixa": _caixa(items),
    }


def _local_label(prop: Property) -> str:
    """Nome curto do sítio onde está o imóvel, para o rótulo do pino.

    Ojelista de mais para menos: quem procura em Angola diz o bairro antes da
    província. O pino precisa de duas palavras, não de três.
    """
    return (
        prop.locality.strip()
        or prop.municipality.strip()
        or PROVINCE_NAMES.get(prop.province_ref, "")
    )


def _caixa(items: list[dict[str, object]]) -> list[list[float]] | None:
    """Caixa que contém todos os pinos, para a vista abrir enquadrada neles.

    Um mapa de Angola inteiro não é um mapa: a 11 de zoom o país inteiro cabe
    no ecrã e nenhum imóvel se distingue do resto. Abrir sobre o catálogo é a
    diferença entre uma imagem de fundo e uma ferramenta.
    """
    if not items:
        return None
    lats = [item["lat"] for item in items]
    lons = [item["lon"] for item in items]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def next_reference(*, province_code: str) -> str:
    """Gera uma referência legível e única com o formato ECH-XX-0000."""
    tail = Property.objects.count() + 1
    while True:
        candidate = f"{REFERENCE_PREFIX}-{province_code[:2].upper()}-{tail:04d}"
        if not Property.objects.filter(reference=candidate).exists():
            return candidate
        tail += 1


@transaction.atomic
def resolve_owner(
    *,
    curator: User,
    full_name: str,
    phone: str,
    id_document_number: str,
) -> OwnerProfile:
    """Reutiliza o proprietário já validado ou cria a ficha a partir da captação."""
    existing = (
        OwnerProfile.objects.filter(full_name=full_name, phone=phone)
        .order_by("created_at")
        .first()
    )
    if existing is not None:
        return existing
    owner = OwnerProfile(
        full_name=full_name,
        phone=phone,
        id_document_type="BI",
        id_document_number=id_document_number or "PENDENTE",
        created_by=curator,
    )
    owner.save()
    return owner


@transaction.atomic
def create_property(
    *,
    curator: User,
    owner: OwnerProfile,
    submission_source: str,
    **fields: object,
) -> Property:
    """Regista um imóvel já curado e abre o dossiê de triagem (§2.1 etapa 3)."""
    province_ref = str(fields.get("province_ref") or "LUANDA")
    prop = Property(
        reference=next_reference(province_code=province_ref),
        owner=owner,
        curated_by=curator,
        **fields,  # type: ignore[arg-type]
    )
    prop.full_clean(exclude=["lease_term_months"] if prop.purpose != Property.Purpose.RENT else None)
    prop.save()
    PropertySubmission.objects.create(
        property=prop,
        source=submission_source,
        owner_name_declared=owner.full_name,
        owner_phone_declared=owner.phone,
        triaged_by=curator,
        triaged_at=timezone.now(),
    )
    return prop


@transaction.atomic
def confirm_submission(
    submission: PropertySubmission,
    *,
    curator: User,
    **confirmations: bool,
) -> PropertySubmission:
    """Actualiza o checklist de triagem e data a confirmação."""
    for field, value in confirmations.items():
        setattr(submission, field, bool(value))
    submission.triaged_by = curator
    submission.triaged_at = timezone.now()
    submission.save()
    return submission


def add_image(*, prop: Property, image: PropertyImage) -> PropertyImage:
    """Acrescenta uma fotografia e atribui a capa quando ainda não existe nenhuma."""
    if not prop.images.exists():
        image.sort_order = 0
    else:
        image.sort_order = prop.images.count()
    image.property = prop
    image.save()
    return image


def verify_document(
    document: PropertyDocument,
    *,
    agent: User,
    reason: str = "",
) -> PropertyDocument:
    """Marca o documento como verificado ou recusado, com registo de auditoria."""
    if document.status == PropertyDocument.Status.REJECTED and not reason.strip():
        raise ValidationError("Indique o motivo da recusa do documento.")
    document.status = (
        PropertyDocument.Status.REJECTED
        if reason.strip()
        else PropertyDocument.Status.VERIFIED
    )
    document.rejection_reason = reason.strip()
    document.verified_by = agent
    document.verified_at = timezone.now()
    document.save()
    return document


def publish_property(*, prop: Property, agent: User, reason: str) -> Property:
    """Publica o imóvel depois de confirmar triagem completa e documentação legal."""
    submission = getattr(prop, "submission", None)
    if submission is None or not submission.is_ready_for_review():
        raise ValidationError("A triagem ainda não está completa para publicação.")
    prop.full_clean()
    prop.transition_to(Property.Status.PUBLISHED, actor=agent, reason=reason)
    return prop


def normalise_price(raw: object) -> Decimal:
    """Converte texto ou número em Decimal, recusando valores não positivos."""
    try:
        value = Decimal(str(raw).replace(" ", "").replace(".", "").replace(",", "."))
    except (ArithmeticError, ValueError) as exc:
        raise ValidationError("Indique um preço válido em Kwanza.") from exc
    if value <= 0:
        raise ValidationError("O preço tem de ser maior do que zero.")
    return value.quantize(Decimal("0.01"))
