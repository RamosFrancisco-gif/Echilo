"""Configuração do assistente de IA."""

from django.apps import AppConfig


class AssistantConfig(AppConfig):
    """Regista a app do atendimento de nível 1."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assistant"
    label = "assistant"
    verbose_name = "Assistente"
