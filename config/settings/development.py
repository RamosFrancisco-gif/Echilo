"""Ambiente de desenvolvimento: verboso, sem cache de assets, SMTP quando configurado."""

from .base import *  # noqa: F403
from .base import EMAIL_HOST_USER, INSTALLED_APPS, MIDDLEWARE  # noqa: F401

DEBUG = True

# Sem credenciais SMTP no `.env` o e-mail é impresso no terminal, nunca enviado.
if not EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
