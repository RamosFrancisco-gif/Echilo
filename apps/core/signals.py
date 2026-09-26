"""Sinais transversais do projecto."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

User = get_user_model()


@receiver(user_logged_in)
def promote_manager_emails(sender: type[User], user: User, **kwargs: object) -> None:
    """Concede acesso ao painel interno a quem está listado em MANAGER_EMAILS."""
    if user.email in settings.MANAGER_EMAILS and not user.is_staff:
        user.is_staff = True
        user.is_superuser = True
        user.role = User.Role.ADMIN
        user.save(update_fields=["is_staff", "is_superuser", "role"])
