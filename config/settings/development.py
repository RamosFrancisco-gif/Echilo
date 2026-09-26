"""Ambiente de desenvolvimento: verboso, sem cache de assets, SMTP quando configurado."""

from .base import *  # noqa: F403
from .base import (  # noqa: F401
    CLOUDINARY_PUBLICAO,
    EMAIL_HOST_USER,
    INSTALLED_APPS,
    MIDDLEWARE,
    storages,
)

DEBUG = True

# O `.env` de desenvolvimento costuma trazer `DJANGO_DEBUG=true`, e o `base` já
# teria escolhido o backend de ficheiros a partir disso. Mesmo assim a resposta é
# escrita aqui, para que cada ambiente diga a sua e não dependa do que o anterior
# deixou.
STORAGES = storages(manifest=False, cloudinary=CLOUDINARY_PUBLICAO)

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
