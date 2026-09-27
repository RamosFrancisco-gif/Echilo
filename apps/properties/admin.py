"""Painel interno de curadoria: equipa gere, publica e audita os imóveis."""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.html import format_html

from .models import (
    DocumentAccessLog,
    OwnerProfile,
    Property,
    PropertyDocument,
    PropertyImage,
    PropertyStatusEvent,
    PropertySubmission,
)
from .validators import validate_photo_count


class PropertyImageInlineFormSet(BaseInlineFormSet):
    """Impede o tecto de quinze de ser contornado por dentro do painel.

    `add_images()` tranca o imóvel e recusa o que não cabe, mas o painel não
    passa por lá: o inline grava directamente. Sem esta conferência, a equipa
    chega ao tecto pela ficha e a primeira fotografia a mais entra sem aviso —
    e sem o ficheiro a ser re-codificado, que é a parte que o serviço faz.
    """

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        # `total_form_count()` menos as marcadas para apagar é o número de
        # linhas que vão existir depois do `save()`. Contar `self.forms` seria
        # contar o que foi enviado, e um payload que não traga as fotografias
        # que já lá estão passaria a dar um tecto que não é o tecto.
        validate_photo_count(self.total_form_count() - len(self.deleted_forms))


class PropertyImageInline(admin.TabularInline):
    """Galeria de fotografias, editável dentro da ficha do imóvel."""

    model = PropertyImage
    extra = 1
    fields = ("image", "caption", "sort_order")
    ordering = ("sort_order",)
    formset = PropertyImageInlineFormSet


class PropertyDocumentInline(admin.TabularInline):
    """Documentação legal com estado de verificação visível na ficha."""

    model = PropertyDocument
    extra = 1
    fields = ("document_type", "file", "status", "verified_by", "verified_at", "rejection_reason")
    readonly_fields = ("verified_at",)


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    """Cadastro de proprietários validados pela equipa."""

    list_display = ("full_name", "phone", "whatsapp", "email", "created_at")
    search_fields = ("full_name", "phone", "id_document_number", "email")
    list_filter = ("id_document_type",)
    readonly_fields = ("created_at",)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    """Ficha completa do imóvel com histórico de estados."""

    list_display = (
        "reference",
        "title",
        "type",
        "purpose",
        "province_ref",
        "price_display",
        "status_badge",
        "curated_by",
    )
    list_filter = ("status", "purpose", "type", "province_ref", "has_water_tank")
    search_fields = ("reference", "title", "municipality", "locality", "description")
    readonly_fields = ("reference", "published_at", "created_at", "updated_at", "map_reference_preview")
    inlines = (PropertyImageInline, PropertyDocumentInline)
    actions = ("publish_selected", "archive_selected")
    fieldsets = (
        ("Identificação", {"fields": ("reference", "title", "description", "type", "purpose")}),
        ("Estado", {"fields": ("status", "owner", "curated_by", "published_at")}),
        (
            "Preço",
            {
                "fields": ("price", "currency", "lease_term_months", "accepts_annual_payment"),
            },
        ),
        (
            "Localização",
            {
                "fields": (
                    "province_ref",
                    "municipality",
                    "locality",
                    "address_hint",
                    "latitude",
                    "longitude",
                    "location_accuracy_m",
                    "location_verified_at",
                    "map_reference_preview",
                )
            },
        ),
        (
            "Características",
            {
                "fields": (
                    "area_m2",
                    "land_area_m2",
                    "bedrooms",
                    "bathrooms",
                    "has_water_tank",
                    "has_generator",
                    "is_furnished",
                    "has_garden",
                    "has_pool",
                    "has_parking",
                )
            },
        ),
        ("Datas", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Preço")
    def price_display(self, obj: Property) -> str:
        """Mostra o preço já formatado em Kwanza."""
        if not obj.price:
            return "—"
        return f"{obj.price:,.2f} {obj.currency}".replace(",", "X").replace(".", ",").replace("X", ".")

    def get_readonly_fields(self, request: admin.ModelRequest, obj: object = None) -> tuple[str, ...]:
        """Tira `status` da edição: só muda por `transition_to()` (§2.8)."""
        return (*super().get_readonly_fields(request, obj), "status")

    def save_model(
        self,
        request: admin.ModelRequest,
        obj: Property,
        form: admin.ModelForm,
        change: bool,
    ) -> None:
        """Impede que um POST forçado no admin contorne a máquina de estados."""
        if change:
            stored = (
                Property.objects.filter(pk=obj.pk)
                .values_list("status", flat=True)
                .first()
            )
            if stored is not None:
                obj.status = stored
        super().save_model(request, obj, form, change)

    @admin.display(description="Estado")
    def status_badge(self, obj: Property) -> str:
        """Mostra o estado com cor para leitura rápida na lista."""
        palette = {
            Property.Status.PUBLISHED: "#2dd4a0",
            Property.Status.REJECTED: "#ef5b5b",
            Property.Status.CHANGES_REQUESTED: "#f5a623",
        }
        color = palette.get(obj.status, "#a49a8c")
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>', color, obj.get_status_display()
        )

    @admin.display(description="Pin no mapa")
    def map_reference_preview(self, obj: Property) -> str:
        """Mostra a referência do pin de satélite sem expor coordenadas exactas."""
        return obj.map_reference or "Por confirmar"

    @admin.action(description="Publicar imóveis seleccionados")
    def publish_selected(self, request: admin.ModelRequest, queryset: admin.QuerySet) -> None:
        """Publica apenas imóveis cuja triagem e documentação estão completas."""
        published = 0
        for prop in queryset:
            try:
                prop.transition_to(
                    Property.Status.PUBLISHED,
                    actor=request.user,
                    reason="Publicação a partir do painel interno",
                )
            except ValidationError as exc:
                self.message_user(request, f"{prop.reference}: {exc.messages[0]}", messages.WARNING)
                continue
            published += 1
        if published:
            self.message_user(request, f"{published} imóvel(is) publicado(s).", messages.SUCCESS)

    @admin.action(description="Arquivar imóveis seleccionados")
    def archive_selected(self, request: admin.ModelRequest, queryset: admin.QuerySet) -> None:
        """Arquiva imóveis que deixaram de estar disponíveis."""
        archived = 0
        for prop in queryset:
            try:
                prop.transition_to(
                    Property.Status.ARCHIVED,
                    actor=request.user,
                    reason="Retirado do mercado pela equipa",
                )
            except ValidationError as exc:
                self.message_user(request, f"{prop.reference}: {exc.messages[0]}", messages.WARNING)
                continue
            archived += 1
        if archived:
            self.message_user(request, f"{archived} imóvel(is) arquivado(s).", messages.SUCCESS)


@admin.register(PropertyStatusEvent)
class PropertyStatusEventAdmin(admin.ModelAdmin):
    """Histórico de transições, apenas de leitura."""

    list_display = ("property", "from_status", "to_status", "actor", "created_at")
    list_filter = ("to_status", "created_at")
    search_fields = ("property__reference", "reason")
    readonly_fields = [field.name for field in PropertyStatusEvent._meta.fields]

    def has_add_permission(self, request: admin.ModelRequest) -> bool:
        """Impede a criação manual de eventos de estado."""
        return False

    def has_change_permission(self, request: admin.ModelRequest, obj: object = None) -> bool:
        """Impede a edição do histórico já registado."""
        return False


@admin.register(DocumentAccessLog)
class DocumentAccessLogAdmin(admin.ModelAdmin):
    """Quem abriu a documentação legal, e quando (§6).

    Só de leitura. Um registo de auditoria que se possa apagar não é um registo
    de auditoria, e a razão de o registo existir é precisamente o dia em que
    alguém precisa de provar que leu a escritura e que não a leu.
    """

    list_display = ("document", "actor", "ip_address", "created_at")
    list_filter = ("created_at", "actor__role")
    search_fields = ("document__property__reference", "actor__email", "ip_address")
    readonly_fields = [field.name for field in DocumentAccessLog._meta.fields]

    def has_add_permission(self, request: admin.ModelRequest) -> bool:
        """Um acesso é o que a aplicação registou, não o que alguém escreve à mão."""
        return False

    def has_change_permission(self, request: admin.ModelRequest, obj: object = None) -> bool:
        """O registo é imutável depois de escrito."""
        return False

    def has_delete_permission(self, request: admin.ModelRequest, obj: object = None) -> bool:
        """Apagar o registo de uma leitura é o que o registo existe para impedir."""
        return False


@admin.register(PropertySubmission)
class PropertySubmissionAdmin(admin.ModelAdmin):
    """Dossiê de captação com o checklist de triagem da etapa 2."""

    list_display = ("property", "source", "ready", "triaged_by", "triaged_at")
    list_filter = ("source", "photos_confirmed", "location_confirmed", "price_confirmed")
    search_fields = ("property__reference", "owner_name_declared", "owner_phone_declared")
    readonly_fields = ("triaged_at", "missing_items")

    @admin.display(boolean=True, description="Pronta para revisão")
    def ready(self, obj: PropertySubmission) -> bool:
        """Indica se a etapa de triagem está completa."""
        return obj.is_ready_for_review()

    @admin.display(description="Em falta")
    def missing_items(self, obj: PropertySubmission) -> str:
        """Resume o que ainda bloqueia a entrada em revisão."""
        items = obj.pending_items()
        return "; ".join(items) if items else "Nada em falta"
