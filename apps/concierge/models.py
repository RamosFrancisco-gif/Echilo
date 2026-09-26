"""Modelos do atendimento humano: leads, visitas, ofertas e conversas."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.validators import validate_angolan_phone
from apps.properties.models import Property


class Lead(models.Model):
    """Contacto de captação, tanto de proprietários como de clientes."""

    class Type(models.TextChoices):
        """Origem do contacto e o que a equipa deve fazer a seguir."""

        OWNER_INTAKE = "OWNER_INTAKE", "Captação de proprietário"
        CLIENT_ENQUIRY = "CLIENT_ENQUIRY", "Consulta de cliente"

    class Status(models.TextChoices):
        """Estados do lead numa máquina de estados ascendente."""

        NEW = "NEW", "Novo"
        CONTACTED = "CONTACTED", "Contactado"
        QUALIFIED = "QUALIFIED", "Qualificado"
        CONVERTED = "CONVERTED", "Convertido"
        DISQUALIFIED = "DISQUALIFIED", "Descartado"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        Status.NEW: {Status.CONTACTED, Status.DISQUALIFIED},
        Status.CONTACTED: {Status.QUALIFIED, Status.DISQUALIFIED},
        Status.QUALIFIED: {Status.CONVERTED, Status.DISQUALIFIED},
        Status.CONVERTED: set(),
        Status.DISQUALIFIED: {Status.NEW},
    }

    full_name = models.CharField("nome", max_length=150)
    phone = models.CharField(
        "telefone",
        max_length=20,
        validators=[validate_angolan_phone],
    )
    email = models.EmailField("e-mail", blank=True)
    lead_type = models.CharField("tipo", max_length=16, choices=Type.choices, db_index=True)
    status = models.CharField(
        "estado",
        max_length=14,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    purpose_interest = models.CharField(
        "interesse",
        max_length=20,
        choices=[("", "Não indicado")]
        + list(Property.Purpose.choices)
        + [(Property.Type.LAND, "Terreno")],
        blank=True,
    )
    message = models.TextField("mensagem", blank=True)
    property_interest = models.ForeignKey(
        Property,
        verbose_name="imóvel de interesse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="atribuído a",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="leads_assigned",
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado em", auto_now=True)

    class Meta:
        verbose_name = "contacto"
        verbose_name_plural = "contactos"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["lead_type", "status"], name="lead_type_status_idx")]

    def __str__(self) -> str:
        return f"{self.full_name} — {self.get_lead_type_display()}"

    def transition_to(self, target: str) -> None:
        """Muda o estado validando a transição permitida."""
        if target not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValidationError(f"Não é possível passar de {self.status} para {target}.")
        self.status = target
        self.save(update_fields=["status", "updated_at"])


class VisitRequest(models.Model):
    """Pedido de visita ao imóvel, sempre confirmado por um humano (§2.10)."""

    class Status(models.TextChoices):
        """Estados do agendamento."""

        PENDING = "PENDING", "Por confirmar"
        CONFIRMED = "CONFIRMED", "Confirmada"
        DECLINED = "DECLINED", "Recusada"
        COMPLETED = "COMPLETED", "Realizada"

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="visit_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="pedido por",
        on_delete=models.PROTECT,
        related_name="visits_requested",
    )
    lead = models.ForeignKey(
        Lead,
        verbose_name="contacto associado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits",
    )
    scheduled_for = models.DateTimeField("marcada para")
    duration_minutes = models.PositiveSmallIntegerField("duração (minutos)", default=45)
    status = models.CharField(
        "estado",
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    notes = models.CharField("notas", max_length=240, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="tratada por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="visits_handled",
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "pedido de visita"
        verbose_name_plural = "pedidos de visita"
        ordering = ["-scheduled_for"]
        indexes = [models.Index(fields=["property", "status"], name="visit_property_status_idx")]

    def __str__(self) -> str:
        return f"{self.property.reference} — {self.scheduled_for:%d/%m/%Y %H:%M}"

    def clean(self) -> None:
        """Recusa sobreposições com outras visitas confirmadas do mesmo imóvel."""
        super().clean()
        if self.status != self.Status.CONFIRMED or not self.scheduled_for:
            return
        start = self.scheduled_for
        end = start + timezone.timedelta(minutes=self.duration_minutes)
        overlapping = VisitRequest.objects.filter(
            property=self.property,
            status=VisitRequest.Status.CONFIRMED,
            scheduled_for__lt=end,
        ).exclude(pk=self.pk)
        overlapping = overlapping.filter(
            scheduled_for__gt=start - timezone.timedelta(minutes=45)
        )
        if overlapping.exists():
            raise ValidationError("Já existe uma visita confirmada neste período.")


class Offer(models.Model):
    """Proposta formal de preço, sempre tratada por um humano (§2.5)."""

    class Status(models.TextChoices):
        """Estados da proposta."""

        SUBMITTED = "SUBMITTED", "Submetida"
        UNDER_REVIEW = "UNDER_REVIEW", "Em análise"
        ACCEPTED = "ACCEPTED", "Aceite"
        REJECTED = "REJECTED", "Recusada"
        WITHDRAWN = "WITHDRAWN", "Retirada"

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="offers",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="submetida por",
        on_delete=models.PROTECT,
        related_name="offers_submitted",
    )
    amount = models.DecimalField(
        "valor proposto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField("moeda", max_length=3, default="AOA")
    message = models.TextField("justificação da proposta", blank=True)
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="respondida por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offers_answered",
    )
    response_notes = models.TextField("resposta da equipa", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    responded_at = models.DateTimeField("respondida em", null=True, blank=True)

    class Meta:
        verbose_name = "proposta"
        verbose_name_plural = "propostas"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.property.reference} — {self.amount} {self.currency}"

    def is_active(self) -> bool:
        """Diz se a proposta ainda aguarda decisão da equipa."""
        return self.status in {self.Status.SUBMITTED, self.Status.UNDER_REVIEW}


class Conversation(models.Model):
    """Filo de atendimento entre cliente e equipa, partilhado com o assistente."""

    class HandledBy(models.TextChoices):
        """Quem está a responder agora."""

        AI = "AI", "Assistente"
        HUMAN = "HUMAN", "Equipa"

    class Status(models.TextChoices):
        """Estado do atendimento no canal."""

        OPEN = "OPEN", "Aberto"
        ESCALATED = "ESCALATED", "Escalado para a equipa"
        RESOLVED = "RESOLVED", "Resolvido"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="cliente",
        on_delete=models.CASCADE,
        related_name="conversations",
        null=True,
        blank=True,
    )
    lead = models.ForeignKey(
        Lead,
        verbose_name="contacto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    property_interest = models.ForeignKey(
        Property,
        verbose_name="imóvel em discussão",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    handled_by = models.CharField(
        "tratado por",
        max_length=6,
        choices=HandledBy.choices,
        default=HandledBy.AI,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="agente responsável",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="conversations_assigned",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada em", auto_now=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"Conversa #{self.pk} ({self.get_handled_by_display()})"

    def escalate(self, *, agent: object) -> None:
        """Passa o atendimento para a equipa (§2.4 nível 2)."""
        self.status = Conversation.Status.ESCALATED
        self.handled_by = Conversation.HandledBy.HUMAN
        self.assigned_to = agent
        self.save(update_fields=["status", "handled_by", "assigned_to", "updated_at"])

    def resolve(self) -> None:
        """Encerra o atendimento."""
        self.status = Conversation.Status.RESOLVED
        self.handled_by = Conversation.HandledBy.HUMAN
        self.save(update_fields=["status", "handled_by", "updated_at"])


class Message(models.Model):
    """Mensagem individual dentro de uma conversa, da IA, do cliente ou da equipa."""

    class Author(models.TextChoices):
        """Origem da mensagem."""

        CLIENT = "CLIENT", "Cliente"
        ASSISTANT = "ASSISTANT", "Assistente"
        STAFF = "STAFF", "Equipa"

    conversation = models.ForeignKey(
        Conversation,
        verbose_name="conversa",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.CharField("autor", max_length=10, choices=Author.choices)
    body = models.TextField("conteúdo")
    is_escalation_notice = models.BooleanField("aviso de escalação", default=False)
    created_at = models.DateTimeField("enviada em", auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"[{self.get_author_display()}] {self.body[:40]}"
