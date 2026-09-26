"""Semeia um catálogo de demonstração para avaliar a interface fora de produção."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.images import solid_colour_jpeg
from apps.properties.models import (
    OwnerProfile,
    Property,
    PropertyDocument,
    PropertyImage,
    PropertySubmission,
)
from apps.properties.services import next_reference

User = get_user_model()

DEMO_PASSWORD = "Tchapo-forte-2026"

CATALOGUE = [
    {
        "title": "T3 no Kilamba com quintal e tanque",
        "type": Property.Type.APARTMENT,
        "purpose": Property.Purpose.RENT,
        "price": Decimal("450000.00"),
        "province_ref": "LUANDA",
        "municipality": "Talatona",
        "locality": "Kilamba",
        "bedrooms": 3,
        "bathrooms": 2,
        "area_m2": 120,
        "latitude": Decimal("-8.918430"),
        "longitude": Decimal("13.184700"),
        "description": (
            "T3 arejado com quintal, parque fechado e água da rede. A cinco minutos do "
            "Rangel, com segurança durante a noite."
        ),
    },
    {
        "title": "Apartamento T1 mobiliado no Ingombota",
        "type": Property.Type.APARTMENT,
        "purpose": Property.Purpose.RENT,
        "price": Decimal("320000.00"),
        "province_ref": "LUANDA",
        "municipality": "Luanda",
        "locality": "Ingombota",
        "bedrooms": 1,
        "bathrooms": 1,
        "area_m2": 62,
        "latitude": Decimal("-8.836000"),
        "longitude": Decimal("13.234400"),
        "accepts_annual_payment": True,
        "description": "T1 pronto a habitar, com ar-condicionado e gerador incluído no valor.",
    },
    {
        "title": "Moradia com piscina em Talatona",
        "type": Property.Type.HOUSE,
        "purpose": Property.Purpose.SALE,
        "price": Decimal("85000000.00"),
        "province_ref": "LUANDA",
        "municipality": "Talatona",
        "locality": "Camama",
        "bedrooms": 5,
        "bathrooms": 4,
        "area_m2": 320,
        "land_area_m2": 800,
        "latitude": Decimal("-8.901100"),
        "longitude": Decimal("13.198700"),
        "description": (
            "Moradia de dois pisos com piscina, jardim e garagem para duas viaturas. "
            "Escritura e certidão de registo predial verificadas."
        ),
    },
    {
        "title": "Terreno para construir em Viana",
        "type": Property.Type.LAND,
        "purpose": Property.Purpose.SALE,
        "price": Decimal("12500000.00"),
        "province_ref": "LUANDA",
        "municipality": "Viana",
        "locality": "Bela Vista",
        "area_m2": None,
        "land_area_m2": 1000,
        "latitude": Decimal("-8.839000"),
        "longitude": Decimal("13.383300"),
        "description": (
            "Terreno de 1.000 m² com acesso pela estrada principal, água e energia na rua."
        ),
    },
    {
        "title": "Escritório no centro de Luanda",
        "type": Property.Type.OFFICE,
        "purpose": Property.Purpose.RENT,
        "price": Decimal("950000.00"),
        "province_ref": "LUANDA",
        "municipality": "Luanda",
        "locality": "Maianga",
        "area_m2": 180,
        "latitude": Decimal("-8.839800"),
        "longitude": Decimal("13.244700"),
        "description": "Escritório com duas divisões, receção e parque privativo no centro.",
    },
    {
        "title": "T4 em urbanização nova no Benfica",
        "type": Property.Type.APARTMENT,
        "purpose": Property.Purpose.RENT,
        "price": Decimal("680000.00"),
        "province_ref": "LUANDA",
        "municipality": "Talatona",
        "locality": "Benfica",
        "bedrooms": 4,
        "bathrooms": 3,
        "area_m2": 190,
        "latitude": Decimal("-8.926500"),
        "longitude": Decimal("13.171200"),
        "accepts_annual_payment": True,
        "description": "T4 em urbanização nova, com condomínio, gerador e parque subterrâneo.",
    },
]


class Command(BaseCommand):
    """Cria equipa, proprietários e imóveis publicados para demonstração."""

    help = "Semeia dados de demonstração: equipa, proprietários e catálogo publicado."

    def add_arguments(self, parser: object) -> None:
        """Permite limitar a quantidade de imóveis criados."""
        parser.add_argument(
            "--limit",
            type=int,
            default=len(CATALOGUE),
            help="Número de imóveis a criar (por omissão, o catálogo completo).",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Remove imóveis e contacts de demonstração antes de criar.",
        )

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        """Cria os dados e imprime um resumo do que ficou no catálogo."""
        if options["flush"]:
            self._flush()

        curator = self._team_user("Carlos Pembele", "curador@echilo.ao", User.Role.CURATOR)
        agent = self._team_user("Bruno Mateus", "agente@echilo.ao", User.Role.AGENT)
        self._team_user("Ana Quissanga", "admin@echilo.ao", User.Role.ADMIN)

        limit = int(options["limit"])
        created = 0
        for index, spec in enumerate(CATALOGUE[:limit], start=1):
            reference = next_reference(province_code=str(spec["province_ref"]))
            if Property.objects.filter(reference=reference).exists():
                continue
            prop = self._property(reference=reference, spec=spec, curator=curator, owner_index=index)
            self._publish(prop=prop, agent=agent, curator=curator)
            created += 1
            self.stdout.write(f"  {prop.reference}  {prop.title}")

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} imóvel(is) publicado(s). Equipa: curador@echilo.ao, "
                f"agente@echilo.ao, admin@echilo.ao (palavra-passe: {DEMO_PASSWORD})."
            )
        )

    def _flush(self) -> None:
        """Remove o que a última execução criou, para o comando ser repetível."""
        for model in (PropertyImage, PropertyDocument, PropertySubmission, Property):
            deleted, _ = model.objects.all().delete()
            if deleted:
                self.stdout.write(f"  removidos {deleted} registos de {model.__name__}")

    def _team_user(self, full_name: str, email: str, role: str) -> User:
        """Cria ou actualiza um membro da equipa com palavra-passe conhecida."""
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"full_name": full_name, "phone": "+244 923 000 000", "role": role},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            self.stdout.write(f"  utilizador {email} ({role})")
        return user

    def _owner(self, index: int, curator: User) -> OwnerProfile:
        """Cria o proprietário do imóvel, com documento de identificação."""
        owner, _ = OwnerProfile.objects.get_or_create(
            id_document_number=f"0045123{89 + index:02d}LA041",
            defaults={
                "full_name": f"Proprietário {index:02d}",
                "phone": f"+244 92{index} 000 001",
                "id_document_type": "BI",
                "created_by": curator,
            },
        )
        return owner

    def _property(
        self, *, reference: str, spec: dict[str, object], curator: User, owner_index: int
    ) -> Property:
        """Cria o imóvel já com coordenadas verificadas pela equipa."""
        values: dict[str, object] = {
            "reference": reference,
            "owner": self._owner(owner_index, curator),
            "curated_by": curator,
            "title": spec["title"],
            "description": spec["description"],
            "type": spec["type"],
            "purpose": spec["purpose"],
            "price": spec["price"],
            "currency": "AOA",
            "province_ref": spec["province_ref"],
            "municipality": spec["municipality"],
            "locality": spec["locality"],
            "latitude": spec["latitude"],
            "longitude": spec["longitude"],
            "location_accuracy_m": 25,
            "location_verified_at": timezone.now(),
            "map_reference": f"{spec['latitude']},{spec['longitude']}",
            "bedrooms": spec.get("bedrooms") or 0,
            "bathrooms": spec.get("bathrooms") or 0,
            "area_m2": spec.get("area_m2"),
            "land_area_m2": spec.get("land_area_m2"),
            "lease_term_months": 12 if spec["purpose"] == Property.Purpose.RENT else None,
            "accepts_annual_payment": bool(spec.get("accepts_annual_payment")),
            "has_water_tank": True,
            "has_generator": spec["type"] != Property.Type.LAND,
            "has_garden": spec["type"] in {Property.Type.HOUSE, Property.Type.LAND},
            "has_pool": spec["type"] == Property.Type.HOUSE,
            "has_parking": True,
            "status": Property.Status.UNDER_VALIDATION,
        }
        prop = Property(**values)
        prop.full_clean()
        prop.save()

        for position in range(5):
            PropertyImage.objects.create(
                property=prop,
                image=SimpleUploadedFile(
                    f"{reference}-{position}.jpg",
                    solid_colour_jpeg(colour=(30 + position * 12, 26, 18)),
                    content_type="image/jpeg",
                ),
                caption=("Capa", "Sala", "Cozinha", "Quarto", "Exterior")[position],
                sort_order=position,
            )

        PropertySubmission.objects.create(
            property=prop,
            owner_name_declared=str(values["owner"].full_name),
            owner_phone_declared=str(values["owner"].phone),
            source=PropertySubmission.Source.WHATSAPP,
            photos_confirmed=True,
            location_confirmed=True,
            price_confirmed=True,
            legal_documents_confirmed=True,
            owner_id_confirmed=True,
            triaged_by=curator,
            triaged_at=timezone.now(),
        )
        return prop

    def _publish(self, *, prop: Property, agent: User, curator: User) -> None:
        """Verifica a documentação e percorre o fluxo até PUBLISHED."""
        for document_type, _label in PropertyDocument.REQUIRED_FOR_PUBLISHING:
            PropertyDocument.objects.create(
                property=prop,
                document_type=document_type,
                file=SimpleUploadedFile(
                    f"{prop.reference}-{document_type}.pdf",
                    b"%PDF-1.4 documento de demonstracao",
                    content_type="application/pdf",
                ),
                status=PropertyDocument.Status.VERIFIED,
                verified_by=agent,
                verified_at=timezone.now(),
            )
        prop.transition_to(
            Property.Status.PUBLISHED, actor=agent, reason="Publicacao de demonstracao"
        )
