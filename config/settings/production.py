"""Ambiente de produção: falha cedo quando falta configuração sensível."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, MANAGER_EMAILS, SECRET_KEY, env_bool

DEBUG = False

if not SECRET_KEY:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured("DJANGO_SECRET_KEY é obrigatória em produção.")
if not ALLOWED_HOSTS:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS é obrigatória em produção.")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

X_FRAME_OPTIONS = "DENY"
