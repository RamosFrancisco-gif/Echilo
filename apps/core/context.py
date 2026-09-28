"""Contexto partilhado por todos os templates do projecto."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.templatetags.static import static


def site_context(request: HttpRequest) -> dict[str, Any]:
    """Disponibiliza dados da operação e permissões em todos os templates."""
    user = request.user
    return {
        "site_name": "Echilo",
        "site_url": settings.SITE_URL,
        "whatsapp_number": settings.ECHILO_WHATSAPP_NUMBER,
        "manager_emails": settings.MANAGER_EMAILS,
        "static_assets": static,
        "current_user": user,
        "is_team_member": user.is_authenticated and user.is_team_role,
    }
