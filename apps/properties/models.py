"""Modelos do domínio de imóveis: proprietários, imóveis, media e documentos."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.validators import validate_angolan_phone

from .reference import ANGOLA_PROVINCES, municipalities_for


class OwnerProfile(models.Model):
    """Proprietário validado pela equipa, criado na conversão do `Lead`."""

    full_name = models.CharField("nome completo", max_length=150)
    phone = models.CharField("telefone", max_length=20, validators=[validate_angolan_phone])
    email = models.EmailField("e-mail", blank=True)
    whatsapp = models.CharField(
        "WhatsApp",
        max_length=20,
        blank=True,
        validators=[validate_angolan_phone],
    )
    id_document_type = models.CharField(
        "tipo de documento",
        max_length=20,
        choices=[
            ("BI", "Bilhete de identidade"),
            ("PASSPORT", "Passaporte"),
        ],
    )
    id_document_number = models.CharField("número do documento", max_length=40)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="registado por",
        on_delete=models.PROTECT,
        related_name="owners_registered",
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "proprietário"
        verbose_name_plural = "proprietários"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name


class PropertyQuerySet(models.QuerySet):
    """Consultas reutilizáveis sobre imóveis, com escopo público por omissão."""

    def published(self) -> "PropertyQuerySet":
        """Restringe ao que é visível ao público (§2.2)."""
        return self.filter(status=Property.Status.PUBLISHED)

    def for_listing(self) -> "PropertyQuerySet":
        """Prepara a listagem com o mínimo de colunas e sem N+1."""
        return self.select_related("owner", "curated_by").prefetch_related("images")


class Property(models.Model):
    """Imóvel curado pela equipa, com ciclo de vida auditado (§2.8)."""

    class Type(models.TextChoices):
        """Tipologia do imóvel usada na navegação e na pesquisa."""

        HOUSE = "HOUSE", "Casa"
        APARTMENT = "APARTMENT", "Apartamento"
        LAND = "LAND", "Terreno"
        OFFICE = "OFFICE", "Escritório"
        SHOP = "SHOP", "Loja"
        WAREHOUSE = "WAREHOUSE", "Armazém"

    class Purpose(models.TextChoices):
        """Tipo de contrato activo do imóvel (§2.6)."""

        RENT = "RENT", "Arrendar"
        SALE = "SALE", "Vender"

    class Status(models.TextChoices):
        """Estados do ciclo de vida. Só `PUBLISHED` é público."""

        DRAFT = "DRAFT", "Rascunho"
        IN_REVIEW = "IN_REVIEW", "Em revisão"
        UNDER_VALIDATION = "UNDER_VALIDATION", "Em validação"
        CHANGES_REQUESTED = "CHANGES_REQUESTED", "Pedido de alterações"
        PUBLISHED = "PUBLISHED", "Publicado"
        ARCHIVED = "ARCHIVED", "Arquivado"
        REJECTED = "REJECTED", "Recusado"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        Status.DRAFT: {Status.IN_REVIEW, Status.REJECTED},
        Status.IN_REVIEW: {Status.UNDER_VALIDATION, Status.CHANGES_REQUESTED, Status.REJECTED},
        Status.UNDER_VALIDATION: {Status.PUBLISHED, Status.CHANGES_REQUESTED, Status.REJECTED},
        Status.CHANGES_REQUESTED: {Status.IN_REVIEW, Status.UNDER_VALIDATION, Status.REJECTED},
        Status.PUBLISHED: {Status.ARCHIVED},
        Status.ARCHIVED: {Status.IN_REVIEW},
        Status.REJECTED: {Status.DRAFT},
    }

    REASONS_REQUIRED = {Status.PUBLISHED, Status.ARCHIVED}

    reference = models.CharField(
        "referência",
        max_length=20,
        unique=True,
        help_text="Código interno legível, por exemplo ECH-LU-0142.",
    )
    title = models.CharField("título", max_length=140)
    description = models.TextField("descrição", blank=True)
    type = models.CharField("tipologia", max_length=12, choices=Type.choices)
    purpose = models.CharField("finalidade", max_length=6, choices=Purpose.choices)

    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    price = models.DecimalField(
        "preço",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField("moeda", max_length=3, default="AOA")
    lease_term_months = models.PositiveSmallIntegerField(
        "prazo do contrato (meses)",
        null=True,
        blank=True,
    )
    accepts_annual_payment = models.BooleanField("aceita pagamento anual", default=False)

    province_ref = models.CharField(
        "província",
        max_length=20,
        choices=ANGOLA_PROVINCES,
        db_index=True,
    )
    municipality = models.CharField("município", max_length=80, blank=True, db_index=True)
    locality = models.CharField("localidade", max_length=120, blank=True)
    address_hint = models.CharField(
        "indício de morada",
        max_length=240,
        blank=True,
        help_text="Nunca publicado. Divulgado apenas após agendamento de visita.",
    )

    latitude = models.DecimalField(
        "latitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MaxValueValidator(Decimal("18.0")), MinValueValidator(Decimal("-18.0"))],
    )
    longitude = models.DecimalField(
        "longitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MaxValueValidator(Decimal("36.0")), MinValueValidator(Decimal("-36.0"))],
    )
    location_accuracy_m = models.PositiveIntegerField(
        "precisão da localização (m)",
        null=True,
        blank=True,
        help_text="Raio de confiança em metros. Vazio significa localização não verificada.",
    )
    location_verified_at = models.DateTimeField("localização verificada em", null=True, blank=True)
    map_reference = models.CharField("referência do pin", max_length=40, blank=True)

    area_m2 = models.PositiveIntegerField("área construída (m²)", null=True, blank=True)
    land_area_m2 = models.PositiveIntegerField("área do terreno (m²)", null=True, blank=True)
    bedrooms = models.PositiveSmallIntegerField("quartos", null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField("casas de banho", null=True, blank=True)

    has_water_tank = models.BooleanField("tem tanque de água", default=False)
    has_generator = models.BooleanField("tem gerador", default=False)
    is_furnished = models.BooleanField("mobilado", default=False)
    has_garden = models.BooleanField("tem quintal", default=False)
    has_pool = models.BooleanField("tem piscina", default=False)
    has_parking = models.BooleanField("tem parque de estacionamento", default=False)

    owner = models.ForeignKey(
        OwnerProfile,
        verbose_name="proprietário",
        on_delete=models.PROTECT,
        related_name="properties",
    )
    curated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="curador responsável",
        on_delete=models.PROTECT,
        related_name="properties_curated",
    )

    published_at = models.DateTimeField("publicado em", null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado em", auto_now=True)

    objects = PropertyQuerySet.as_manager()

    class Meta:
        verbose_name = "imóvel"
        verbose_name_plural = "imóveis"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "purpose"], name="prop_status_purpose_idx"),
            models.Index(fields=["status", "province_ref"], name="prop_status_province_idx"),
            models.Index(fields=["type", "status"], name="prop_type_status_idx"),
            # A pesquisa por área reduz candidatos pela caixa envolvente (§2.3).
            models.Index(fields=["latitude", "longitude"], name="prop_lat_lon_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.title}"

    def get_absolute_url(self) -> str:
        """Caminho público do imóvel, só faz sentido depois de publicado."""
        return reverse("properties:property_detail", kwargs={"reference": self.reference})

    def clean(self) -> None:
        """Impede incoerências entre tipologia, coordenadas e lease (§2.3, §2.6)."""
        if self.purpose == self.Purpose.RENT and not self.lease_term_months:
            raise ValidationError(
                {"lease_term_months": "Indique o prazo do contrato em meses para arrendamento."}
            )
        if self.type == self.Type.LAND and not self.land_area_m2:
            raise ValidationError({"land_area_m2": "Indique a área do terreno em metros quadrados."})
        if bool(self.latitude) != bool(self.longitude):
            raise ValidationError("As coordenadas de latitude e longitude têm de ser indicadas em conjunto.")
        if self.status == self.Status.PUBLISHED and not self.location_verified_at:
            raise ValidationError("Confirme a localização por satélite antes de publicar.")

    def missing_verified_documents(self) -> list[str]:
        """Lista a documentação legally exigida que ainda não foi verificada (§2.7)."""
        verified = set(
            self.documents.filter(status=PropertyDocument.Status.VERIFIED).values_list(
                "document_type", flat=True
            )
        )
        return [
            label
            for kind, label in PropertyDocument.REQUIRED_FOR_PUBLISHING
            if kind not in verified
        ]

    def is_rentable(self) -> bool:
        """Diz se o imóvel está activo para arrendamento."""
        return self.purpose == self.Purpose.RENT and self.status == self.Status.PUBLISHED

    def is_for_sale(self) -> bool:
        """Diz se o imóvel está activo para venda."""
        return self.purpose == self.Purpose.SALE and self.status == self.Status.PUBLISHED

    def transition_to(
        self,
        target: str,
        *,
        actor: "settings.AUTH_USER_MODEL",
        reason: str = "",
    ) -> None:
        """Muda o estado validando a transição e registando o autor no histórico."""
        if target not in self.Status.values:
            raise ValidationError(f"Estado desconhecido: {target}")
        if target not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValidationError(f"Não é possível passar de {self.status} para {target}.")
        if target in self.REASONS_REQUIRED and not reason.strip():
            raise ValidationError("Este estado exige uma justificação.")
        if target == self.Status.PUBLISHED and not self.location_verified_at:
            raise ValidationError("Confirme a localização por satélite antes de publicar.")

        previous = self.status
        self.status = target
        if target == self.Status.PUBLISHED:
            if self.missing_verified_documents():
                raise ValidationError("Falta verificar a documentação legal exigida.")
            self.published_at = timezone.now()
        self.save(update_fields=["status", "published_at", "updated_at"])
        PropertyStatusEvent.objects.create(
            property=self,
            from_status=previous,
            to_status=target,
            actor=actor,
            reason=reason.strip(),
        )

    def transition_allowed(self, target: str) -> bool:
        """Diz se a transição é permitida a partir do estado actual."""
        return target in self.ALLOWED_TRANSITIONS.get(self.status, set())

    @property
    def tone(self) -> str:
        """Chave de cor da interface, derivada da tipologia e da finalidade."""
        if self.type == self.Type.LAND:
            return "land"
        if self.purpose == self.Purpose.SALE:
            return "sale"
        return "rent"

    @property
    def municipality_choices(self) -> tuple[str, ...]:
        """Municípios conhecidos da província, usados para preencher o formulário interno."""
        return municipalities_for(self.province_ref)


class PropertyImage(models.Model):
    """Fotografia de imóvel. A primeira é a capa por omissão (§2.1)."""

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField("fotografia", upload_to="properties/%Y/%m/")
    caption = models.CharField("legenda", max_length=140, blank=True)
    sort_order = models.PositiveSmallIntegerField("ordem", default=0)
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "fotografia"
        verbose_name_plural = "fotografias"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.property.reference} — fotografia {self.sort_order}"


class PropertyDocument(models.Model):
    """Documento legal do imóvel com estado de verificação da equipa (§2.7)."""

    class Status(models.TextChoices):
        """Estado de verificação de cada documento."""

        PENDING = "PENDING", "Por verificar"
        VERIFIED = "VERIFIED", "Verificado"
        REJECTED = "REJECTED", "Recusado"

    class DocumentType(models.TextChoices):
        """Documentos aceites pela equipa de curadoria."""

        OWNERSHIP_TITLE = "OWNERSHIP_TITLE", "Escritura"
        LAND_REGISTRY = "LAND_REGISTRY", "Certidão de registo predial"
        IUR = "IUR", "Imposto único de renda"
        LEASE_CONTRACT = "LEASE_CONTRACT", "Contrato de arrendamento"
        ID_DOCUMENT = "ID_DOCUMENT", "Documento de identificação do proprietário"
        URBAN_CERTIFICATE = "URBAN_CERTIFICATE", "Certidão de conformidade urbanística"

    REQUIRED_FOR_PUBLISHING: tuple[tuple[str, str], ...] = (
        (DocumentType.OWNERSHIP_TITLE, "Escritura"),
        (DocumentType.ID_DOCUMENT, "Documento de identificação do proprietário"),
    )

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(
        "tipo de documento",
        max_length=20,
        choices=DocumentType.choices,
    )
    file = models.FileField("ficheiro", upload_to="documents/%Y/%m/", blank=True)
    status = models.CharField(
        "verificação",
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="verificado por",
        on_delete=models.PROTECT,
        related_name="documents_verified",
        null=True,
        blank=True,
    )
    verified_at = models.DateTimeField("verificado em", null=True, blank=True)
    rejection_reason = models.CharField("motivo da recusa", max_length=240, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        constraints = [
            models.UniqueConstraint(
                fields=["property", "document_type"],
                name="unique_document_per_property",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_document_type_display()} — {self.property.reference}"


class PropertyStatusEvent(models.Model):
    """Histórico imutável das transições de estado, exigido por §2.8."""

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="status_events",
    )
    from_status = models.CharField("estado anterior", max_length=20)
    to_status = models.CharField("novo estado", max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        on_delete=models.PROTECT,
        related_name="property_status_events",
    )
    reason = models.CharField("justificação", max_length=240, blank=True)
    created_at = models.DateTimeField("registado em", auto_now_add=True)

    class Meta:
        verbose_name = "evento de estado"
        verbose_name_plural = "eventos de estado"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.property.reference}: {self.from_status} → {self.to_status}"


class PropertySubmission(models.Model):
    """Dossiê de captação e triagem que precede o cadastro do imóvel (§2.1)."""

    class Source(models.TextChoices):
        """Canal de entrada do proprietário na etapa de captação."""

        WHATSAPP = "WHATSAPP", "WhatsApp"
        WEB_FORM = "WEB_FORM", "Formulário de adesão"
        PHONE_CALL = "PHONE_CALL", "Chamada"
        IN_PERSON = "IN_PERSON", "Presencial"

    property = models.OneToOneField(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="submission",
    )
    source = models.CharField("canal de entrada", max_length=12, choices=Source.choices)
    owner_name_declared = models.CharField("nome declarado", max_length=150)
    owner_phone_declared = models.CharField(
        "telefone declarado",
        max_length=20,
        validators=[validate_angolan_phone],
    )
    photos_confirmed = models.BooleanField("fotos confirmadas", default=False)
    location_confirmed = models.BooleanField("localização confirmada por satélite", default=False)
    price_confirmed = models.BooleanField("preço confirmado", default=False)
    legal_documents_confirmed = models.BooleanField("documentação legal recolhida", default=False)
    owner_id_confirmed = models.BooleanField("identidade do proprietário confirmada", default=False)
    notes = models.TextField("notas da triagem", blank=True)
    triaged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="triado por",
        on_delete=models.PROTECT,
        related_name="submissions_triaged",
        null=True,
        blank=True,
    )
    triaged_at = models.DateTimeField("triado em", null=True, blank=True)

    class Meta:
        verbose_name = "captação de imóvel"
        verbose_name_plural = "captações de imóveis"

    def __str__(self) -> str:
        return f"Captação {self.property.reference} ({self.get_source_display()})"

    def is_ready_for_review(self) -> bool:
        """Diz se a etapa de triagem está completa o suficiente para avançar (§2.1)."""
        prop = self.property
        checklist = all(
            (
                self.photos_confirmed,
                self.location_confirmed,
                self.price_confirmed,
                self.legal_documents_confirmed,
                self.owner_id_confirmed,
            )
        )
        media_ok = prop.images.count() >= 5
        coordinates_ok = prop.latitude is not None and prop.longitude is not None
        price_ok = prop.price is not None and prop.price > 0
        owner_ok = bool(prop.owner_id and prop.owner.phone)
        return checklist and media_ok and coordinates_ok and price_ok and owner_ok

    def pending_items(self) -> list[str]:
        """Descreve, em linguagem de equipa, o que ainda falta na triagem."""
        missing: list[str] = []
        prop = self.property
        if prop.images.count() < 5:
            missing.append("Fotografias (mínimo de 5)")
        if prop.latitude is None or prop.longitude is None:
            missing.append("Coordenadas no mapa")
        elif not prop.location_verified_at:
            missing.append("Confirmação da localização por satélite")
        if not self.photos_confirmed:
            missing.append("Conferência das fotografias")
        if not prop.price or prop.price <= 0:
            missing.append("Preço em Kwanza")
        if not prop.purpose:
            missing.append("Tipo de contrato (venda ou arrendamento)")
        if not prop.owner_id or not prop.owner.phone:
            missing.append("Identificação e contacto do proprietário")
        if not self.legal_documents_confirmed:
            missing.append("Documentação legal recolhida")
        return missing
