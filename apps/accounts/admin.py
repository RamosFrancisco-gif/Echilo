"""Painel interno de gestão de contas."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Expose as contas com o perfil de acesso visível e filtrável."""

    ordering = ("full_name",)
    list_display = ("full_name", "email", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("full_name", "email", "phone")
    readonly_fields = ("is_team_member", "created_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Identificação"), {"fields": ("full_name", "phone", "role", "is_team_member")}),
        (
            _("Permissões"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Datas"), {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "phone", "password1", "password2"),
            },
        ),
    )
