"""Configuração comum a todos os ambientes do Echilo."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    """Lê uma variável de ambiente devolvendo `default` quando ausente."""
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    """Interpreta uma variável de ambiente como booleano."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str) -> list[str]:
    """Devolve uma lista de valores separados por vírgula, sem espaços órfãos."""
    return [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]


def env_number(key: str, default: float) -> float:
    """Lê um número do ambiente, ignorando em branco ou texto malformado.

    Uma variável em branco no `.env` é o caso comum, não a excepção: sem isto
    o `float("")` rebentaria o import do projecto inteiro.
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(key: str, default: int) -> int:
    """Lê um inteiro do ambiente, com a mesma tolerância a valores inválidos."""
    return int(env_number(key, float(default)))


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.accounts",
    "apps.properties",
    "apps.concierge",
    "apps.assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context.site_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": env("DJANGO_DB_ENGINE", "django.db.backends.mysql"),
        "NAME": env("DJANGO_DB_NAME", "echilo"),
        "USER": env("DJANGO_DB_USER", "root"),
        "PASSWORD": env("DJANGO_DB_PASSWORD"),
        "HOST": env("DJANGO_DB_HOST", "127.0.0.1"),
        "PORT": env("DJANGO_DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        "CONN_MAX_AGE": 60,
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-ao"
TIME_ZONE = "Africa/Luanda"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "properties:property_list"
LOGOUT_REDIRECT_URL = "properties:home"
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2

SESSION_COOKIE_NAME = "echilo_sessionid"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))
# Sem remetente explícito, o SMTP só aceita a conta autenticada (limite do Gmail).
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or f"Echilo <{EMAIL_HOST_USER}>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

ECHILO_AI_API_KEY = env("ECHILO_AI_API_KEY")
ECHILO_AI_MODEL = env("ECHILO_AI_MODEL", "openai/gpt-oss-120b")
ECHILO_AI_ENABLED = env_bool("ECHILO_AI_ENABLED", default=True)
ECHILO_WHATSAPP_NUMBER = env("ECHILO_WHATSAPP_NUMBER")
MANAGER_EMAILS = env_list("MANAGER_EMAILS")
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000")

# Mapa da pesquisa por área (§2.3). A biblioteca é JavaScript vendorizado em
# `static/vendor/`; aqui só mora o fornecedor de tiles, que é a única peça que
# muda de fornecedor. `{key}` é substituído pela chave pública do tiles.
#
# O default é um basemap escuro: o catálogo é escuro, e assim o mapa não briga
# com a página nem precisa de ser invertido por CSS. Também evita o
# `tile.openstreetmap.org`, que recusa pedidos com 403 quando o tráfego não
# passa a Identifying User-Agent que a sua política exige. `{s}` e `{r}` são
# placeholders do Leaflet (subdomínio e densidade de píxeis).
# Em produção, aponte para um fornecedor comercial — ver `AGENTS.md` §2.12.
ECHILO_MAP_TILE_URL = env("ECHILO_MAP_TILE_URL") or (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery"
    "/MapServer/tile/{z}/{y}/{x}"
)
ECHILO_MAP_TILE_ATTRIBUTION = env("ECHILO_MAP_TILE_ATTRIBUTION") or (
    "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics"
)
ECHILO_MAP_TILE_KEY = env("ECHILO_MAP_TILE_KEY")
ECHILO_MAP_CENTER_LAT = env_number("ECHILO_MAP_CENTER_LAT", -8.8383)
ECHILO_MAP_CENTER_LON = env_number("ECHILO_MAP_CENTER_LON", 13.2344)
# 12 enquadra Luanda e arredores. A 11 cabia Angola inteira no ecrã, e aí um
# imóvel é um pixel perdido. Com imóveis publicados, o mapa abre enquadrado
# neles e este valor só pesa quando ainda não há nenhum.
ECHILO_MAP_DEFAULT_ZOOM = env_int("ECHILO_MAP_DEFAULT_ZOOM", 12)
# A imagem aérea vem em cor natural. Invertê-la falsearia a vegetação e a
# água, que são as pistas que servem para avaliar um terreno, por isso fica
# desligada. Só se liga para um basemap vectorial claro, como o do OSM.
#
# O `tile.openstreetmap.org` devolvia 403 a um browser em 127.0.0.1, e o
# CARTO passou a devolver HTTP 200 com um cartaz de "API KEY REQUIRED". Nenhum
# dos dois é fiável para um mapa que é parte do produto.
ECHILO_MAP_INVERT_TILES = env_bool("ECHILO_MAP_INVERT_TILES", False)

DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        }
    },
}
