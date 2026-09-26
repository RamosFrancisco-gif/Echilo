"""Utilitários transversais sem dependência de domínio."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configura o app de utilitários e regista o sinal de criação de perfil."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Núcleo"

    def ready(self) -> None:
        """Importa os sinais para que os hooks sejam registados."""
        from . import signals  # noqa: F401
