"""Painel interno do atendimento: fila de contactos, visitas, propostas e conversas."""

from __future__ import annotations

from django.contrib import admin

from .models import Conversation, Lead, Message, Offer, VisitRequest


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    """Fila de contactos com estado e tipo filtráveis."""

    list_display = ("full_name", "phone", "lead_type", "status", "assigned_to", "created_at")
    list_filter = ("lead_type", "status", "purpose_interest")
    search_fields = ("full_name", "phone", "email", "message")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(VisitRequest)
class VisitRequestAdmin(admin.ModelAdmin):
    """Pedidos de visita com confirmação manual da equipa."""

    list_display = ("property", "requested_by", "scheduled_for", "status", "handled_by")
    list_filter = ("status",)
    search_fields = ("property__reference", "requested_by__full_name", "notes")
    readonly_fields = ("created_at",)


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    """Propostas formais, respondidas exclusivamente por um agente."""

    list_display = ("property", "submitted_by", "amount", "status", "responded_by", "created_at")
    list_filter = ("status",)
    search_fields = ("property__reference", "submitted_by__full_name", "message")
    readonly_fields = ("created_at", "responded_at")


class MessageInline(admin.TabularInline):
    """Histórico de mensagens dentro da conversa."""

    model = Message
    extra = 0
    fields = ("author", "body", "is_escalation_notice", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Conversas com indicador do nível de atendimento activo."""

    list_display = ("id", "user", "property_interest", "status", "handled_by", "assigned_to")
    list_filter = ("status", "handled_by")
    search_fields = ("user__full_name", "user__email", "property_interest__reference")
    readonly_fields = ("created_at", "updated_at")
    inlines = (MessageInline,)
