"""Factories partilhadas pelos testes, sem dados reais e sem acesso à rede."""

from __future__ import annotations

import re
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.properties.models import (
    OwnerProfile,
    Property,
    PropertyDocument,
    PropertyImage,
    PropertySubmission,
)
from apps.properties.services import next_reference

User = get_user_model()


def make_user(*, role: str = User.Role.CLIENT, email: str = "cliente@exemplo.ao", **kwargs: object) -> object:
    """Cria um utilizador com palavra-passe conhecida para os testes."""
    defaults: dict[str, object] = {
        "full_name": "Ana Maria dos Santos",
        "email": email,
        "phone": "+244 923 456 789",
        "role": role,
        "password": "Tchapo-forte-2026",
    }
    defaults.update(kwargs)
    password = str(defaults.pop("password"))
    user = User(**defaults)
    user.set_password(password)
    user.save()
    return user


def make_owner(
    *,
    created_by: object,
    full_name: str = "Joaquim Ferreira",
    phone: str = "+244 912 345 678",
) -> OwnerProfile:
    """Cria o perfil de proprietário exigido no cadastro gerido."""
    return OwnerProfile.objects.create(
        full_name=full_name,
        phone=phone,
        id_document_type="BI",
        id_document_number="004512389LA041",
        created_by=created_by,
    )


def make_property(
    *,
    curator: object,
    owner: object,
    status: str = Property.Status.PUBLISHED,
    purpose: str = Property.Purpose.RENT,
    type: str = Property.Type.APARTMENT,
    price: str = "450000.00",
    title: str = "T3 no Kilamba com quintal",
    municipality: str = "Talatona",
    province_ref: str = "LUANDA",
    bedrooms: int = 3,
    **kwargs: object,
) -> Property:
    """Cria um imóvel completo, já com coordenadas verificadas pela equipa."""
    defaults: dict[str, object] = {
        "reference": next_reference(province_code=province_ref),
        "owner": owner,
        "curated_by": curator,
        "title": title,
        "description": "T3 arejado, com quintal e parque fechado.",
        "type": type,
        "purpose": purpose,
        "price": Decimal(price),
        "currency": "AOA",
        "lease_term_months": 12 if purpose == Property.Purpose.RENT else None,
        "province_ref": province_ref,
        "municipality": municipality,
        "locality": "Kilamba",
        "latitude": Decimal("-8.918430"),
        "longitude": Decimal("13.184700"),
        "location_accuracy_m": 25,
        "location_verified_at": timezone.now(),
        "map_reference": "-8.918430,13.184700",
        "area_m2": 120,
        "bedrooms": bedrooms,
        "bathrooms": 2,
        "has_water_tank": True,
        "has_generator": True,
        "status": status,
    }
    defaults.update(kwargs)
    prop = Property(**defaults)
    prop.full_clean()
    prop.save()
    return prop


class _IdsInside(HTMLParser):
    """Apanha os `id` que estão dentro de um certo contentor.

    Um `<div>` com filhos não se extrai com `re`: o primeiro `</div>` fecha um
    filho e o resto do contentor fica do lado de fora. A pergunta "o botão está
    sobre o mapa ou no painel ao lado" é de estrutura, e a estrutura é o que o
    parser sabe ler.
    """

    def __init__(self, container_id: str) -> None:
        super().__init__()
        self.alvo = container_id
        self.profundidade = 0
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        identificador = atributos.get("id")
        if identificador == self.alvo:
            self.profundidade = 1
            return
        if self.profundidade:
            self.profundidade += 1
            if identificador:
                self.ids.append(identificador)

    def handle_endtag(self, tag: str) -> None:
        if self.profundidade:
            self.profundidade -= 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        atributos = dict(attrs)
        if self.profundidade and atributos.get("id"):
            self.ids.append(atributos["id"])


def ids_dentro_de(html: str, container_id: str) -> list[str]:
    """Os `id` que estão dentro do elemento com este `id`, por ordem de leitura."""
    parser = _IdsInside(container_id)
    parser.feed(html)
    return parser.ids


def select_options(html: str, field_id: str) -> list[str]:
    """Os valores que um `<select>` oferece, e nada mais.

    Medir a página inteira mede o que não é o campo: o formulário de registo tem
    três `<select>` e o do tipo de documento escreve `BI` e `PASSPORT`, que não
    são províncias. Ir pelo `id` do campo é o que impede um teste de passar por
    coincidência enquanto mede outra coisa.
    """
    bloco = re.search(
        rf'<select[^>]*\bid="{re.escape(field_id)}"[^>]*>(.*?)</select>', html, re.DOTALL
    )
    if bloco is None:
        return []
    return re.findall(r'value="([^"]*)"', bloco.group(1))


def jpeg_bytes(colour: tuple[int, int, int] = (40, 32, 22)) -> bytes:
    """Gera um JPEG real, para que Pillow aceite o ficheiro nos formulários."""
    from apps.core.images import solid_colour_jpeg

    return solid_colour_jpeg(colour)


def make_image(prop: Property, *, caption: str = "Sala") -> PropertyImage:
    """Anexa uma fotografia válida, mínima, para satisfazer a regra da capa."""
    return PropertyImage.objects.create(
        property=prop,
        image=SimpleUploadedFile(
            f"{prop.reference}-{caption.lower()}.jpg",
            jpeg_bytes(),
            content_type="image/jpeg",
        ),
        caption=caption,
        sort_order=0,
    )


def make_verified_documents(prop: Property) -> None:
    """Marca como verificados os dois documentos que §2.7 exige para publicar."""
    for doc_type, name in PropertyDocument.REQUIRED_FOR_PUBLISHING:
        PropertyDocument.objects.create(
            property=prop,
            document_type=doc_type,
            file=SimpleUploadedFile(f"{prop.reference}-{doc_type}.pdf", b"%PDF-1.4 stub", content_type="application/pdf"),
            status=PropertyDocument.Status.VERIFIED,
            verified_at=timezone.now(),
        )


def make_submission(prop: Property, *, triaged_by: object, ready: bool = True) -> PropertySubmission:
    """Regista o dossiê de captação, completo ou com pendências."""
    return PropertySubmission.objects.create(
        property=prop,
        owner_name_declared=prop.owner.full_name,
        owner_phone_declared=prop.owner.phone,
        source=PropertySubmission.Source.WEB_FORM,
        photos_confirmed=ready,
        location_confirmed=ready,
        price_confirmed=ready,
        legal_documents_confirmed=ready,
        owner_id_confirmed=ready,
        triaged_by=triaged_by if ready else None,
        triaged_at=timezone.now() if ready else None,
    )
