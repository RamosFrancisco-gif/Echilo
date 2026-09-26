"""Configuração do app de imóveis."""

from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    """Regista a app de imóveis e o formulário público de adesão."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.properties"
    label = "properties"
    verbose_name = "Imóveis"
