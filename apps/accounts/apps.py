"""Configuração do app de contas."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Regista a app de contas e o modelo de utilizador próprio."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Contas"
