"""Configuração do app de atendimento humano."""

from django.apps import AppConfig


class ConciergeConfig(AppConfig):
    """Regista a app de leads, visitas, ofertas e conversas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.concierge"
    label = "concierge"
    verbose_name = "Atendimento"
