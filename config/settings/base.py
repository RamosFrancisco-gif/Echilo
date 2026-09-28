"""Configuração comum a todos os ambientes do Echilo."""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# O tecto de upload é um facto da plataforma, e o `LIMITE_PEDIDO_MB` é a única
# explicação de porque é 4,5 MB e não 5. As settings vão buscá-lo ao app em vez de
# o repetir, porque um número repetido diverge sem dar erro — e o que divergia
# aqui não era o valor, era o facto de a segunda definição silenciar a primeira e
# o `env_int` nunca chegar a correr.
#
# Importar um módulo de app durante o carregamento das settings é seguro porque o
# `apps.core.validators` não toca no registo de apps: só `re`, `datetime`,
# `Decimal` e dois utilitários do Django que não dependem dele.
from apps.core.validators import LIMITE_PEDIDO_MB

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")

def env(key: str, default: str = "") -> str:
    """Lê uma variável de ambiente devolvendo `default` quando ausente."""
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    """Interpreta uma variável de ambiente como booleano.

    Uma variável em branco cai no omissão, como no `env_number`. O contrário
    dava `False` a uma variável que ninguém tinha posto a `False`: um
    `EMAIL_USE_TLS=` no painel desligava o TLS do SMTP em silêncio, e um
    `CLOUDINARY_PUBLICAO=` fazia a produção recusar arrancar sem dizer que
    ninguém tinha ligado o interruptor.
    """
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
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

def db_options() -> dict[str, object]:
    """Monta as `OPTIONS` do MySQL, e decide se a ligação é cifrada.

    O `OPTIONS` do Django não é um dicionário que o Django conheça: em MySQL ele
    é copiado tal e qual para o `connect()` do driver. Uma chave que o driver não
    aceite não é ignorada, é um `TypeError` na primeira ligação — e a primeira
    ligação é a primeira conta a tentar entrar.

    Daí a parte que não é óbvia. Um URI do Aiven vem com `ssl-mode=REQUIRED`, e
    essa palavra não pode ser passada: o PyMySQL não tem `ssl_mode`, e o
    `ssl-mode` do URI é para clientes que falam o protocolo dele. O PyMySQL
    entende `ssl`, com `ca`, `check_hostname` e `verify_mode` — que é o que
    transformam "cifrado" em "cifrado e verificado".

    A distinção importa. Sem nada disto o PyMySQL entra em modo `PREFERRED`:
    tenta TLS e, se o servidor não oferecer, continua em texto claro. Com a CA
    configurada, a verificação passa a ser real, e a senha de produção deixa de
    poder ser lida por quem se faça passar pelo servidor.
    """
    opcoes: dict[str, object] = {
        # `utf8mb4` só entra em vigor porque o `OPTIONS` é copiado depois dos
        # parâmetros que o Django já tinha montado. O backend começa por pôr
        # `utf8` e só no fim faz `kwargs.update(options)`, o que faz este
        # dicionário ganhar. Tirá-lo daqui repõe o `utf8` em silêncio.
        "charset": "utf8mb4",
        "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        "connect_timeout": env_int("DJANGO_DB_TIMEOUT", 10),
    }
    ca = env("DJANGO_DB_SSL_CA")
    if ca:
        caminho = Path(ca)
        if not caminho.is_file():
            # Falhar aqui vale mais do que falhar na ligação: o erro do driver é
            # um `FileNotFoundError` dentro do PyMySQL, numa stack de três
            # níveis que não diz que a variável está mal posta.
            raise ImproperlyConfigured(
                f"DJANGO_DB_SSL_CA aponta para {ca}, que não existe. O MySQL do "
                "Aiven usa uma CA própria do serviço, e o certificado baixa-se "
                "na consola, em Connection settings. Ver config/certs/README.md."
            )
        opcoes["ssl"] = {
            "ca": str(caminho),
            "check_hostname": env_bool("DJANGO_DB_SSL_VERIFICAR_HOST", default=True),
            "verify_mode": "required",
        }
    return opcoes


DATABASES = {
    "default": {
        "ENGINE": env("DJANGO_DB_ENGINE", "django.db.backends.mysql"),
        "NAME": env("DJANGO_DB_NAME", "echilo"),
        "USER": env("DJANGO_DB_USER", "root"),
        "PASSWORD": env("DJANGO_DB_PASSWORD"),
        "HOST": env("DJANGO_DB_HOST", "127.0.0.1"),
        "PORT": env("DJANGO_DB_PORT", "3306"),
        "OPTIONS": db_options(),
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

# Os ficheiros vão para a Cloudinary quando há credencial, e para o disco quando
# não há. A escolha é feita pela variável e não pelo ambiente: um `.env` de
# desenvolvimento sem `CLOUDINARY_URL` tem de continuar a funcionar sozinho.
CLOUDINARY_URL = env("CLOUDINARY_URL")

# Só o que é público pode ser uma foto do imóvel: as imagens aparecem no catálogo
# sem sessão. Os documentos legais não entram aqui e nunca podem entrar.
CLOUDINARY_PUBLICAO = env_bool("CLOUDINARY_PUBLICAO", default=bool(CLOUDINARY_URL))

# O `PropertyDocument.file` é o único campo do projecto que não pode ser público
# (§6). Django não tem dois backends "default", por isso o campo declara o seu.
MEDIA_DOCUMENTACAO_BACKEND = (
    "apps.core.storage.CloudinaryDocumentStorage"
    if CLOUDINARY_PUBLICAO
    else "django.core.files.storage.FileSystemStorage"
)

# O retrato é público, como a capa, mas não é uma capa: aos 512 px e na sua pasta.
# O `default` continua a ser o das imagens de imóvel, e é por isso que o campo
# `User.photo` declara o seu — como o do documento, e pelo mesmo motivo.
MEDIA_PERFIS_BACKEND = (
    "apps.core.storage.CloudinaryAvatarStorage"
    if CLOUDINARY_PUBLICAO
    else "django.core.files.storage.FileSystemStorage"
)

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


def storages(*, manifest: bool, cloudinary: bool) -> dict[str, dict[str, str]]:
    """Monta o `STORAGES` a partir de duas decisões explícitas.

    É uma função e não um bloco no topo do ficheiro porque `production.py` muda
    `DEBUG` para `False` **depois** de este módulo ser importado. Um `STORAGES`
    decidido aqui com o `DEBUG` do ambiente lia `True` da variável de
    desenvolvimento, e a produção ficava com `StaticFilesStorage`: sem manifesto,
    o `whitenoise` não sabe que ficheiro responder a cada nome com hash, e o
    sítio aparecia sem CSS — com 200 e sem um erro.

    `manifest` e `cloudinary` são parâmetros, e não letidas de `DEBUG`, para que
    cada ambiente diga o que é em vez de o herdado. `production.py` e
    `development.py` respondem, e a resposta fica escrita à vista.
    """
    return {
        "default": {
            "BACKEND": (
                "apps.core.storage.CloudinaryImageStorage"
                if cloudinary
                else "django.core.files.storage.FileSystemStorage"
            )
        },
        "staticfiles": {
            "BACKEND": (
                "whitenoise.storage.CompressedManifestStaticFilesStorage"
                if manifest
                else "django.contrib.staticfiles.storage.StaticFilesStorage"
            )
        },
    }


STORAGES = storages(manifest=not DEBUG, cloudinary=CLOUDINARY_PUBLICAO)

# O §6 limita o que entra. A Cloudinary volta a validar os formatos, mas validar
# duas vezes não é redundância: uma das validações está a oito saltos do
# ficheiro e a outra no próprio campo.
#
# O tecto sai de `LIMITE_PEDIDO_MB`, e não de um número escrito aqui, porque o
# limite é um só e há uma explicação de porquê em `apps.core.validators`. Com
# 5 MiB nas settings e 4,5 MB na plataforma, o pedido entre os dois morria na
# edge da Vercel — terso, sem página e sem a mensagem que a equipa de curadoria
# entendia — e a validação do Django nunca chegava a correr.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int(
    "DATA_UPLOAD_MAX_MEMORY_SIZE", int(LIMITE_PEDIDO_MB * 1024 * 1024)
)

# Este não é um tecto de recusa: é a partir de que tamanho o Django escreve o
# ficheiro num temporário em vez de o ter em memória. Fica no tamanho do pedido
# porque um ficheiro não pode ser maior do que o pedido que o traz.
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int(
    "FILE_UPLOAD_MAX_MEMORY_SIZE", int(LIMITE_PEDIDO_MB * 1024 * 1024)
)

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
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
# Sem remetente explícito, o SMTP só aceita a conta autenticada (limite do Gmail).
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or f"Echilo <{EMAIL_HOST_USER}>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

ECHILO_AI_API_KEY = env("ECHILO_AI_API_KEY")
ECHILO_AI_MODEL = env("ECHILO_AI_MODEL", "openai/gpt-oss-120b")
ECHILO_AI_ENABLED = env_bool("ECHILO_AI_ENABLED", default=True)

# O plano Hobby da Vercel corta a função aos 10 s, e `vercel.json` diz 10 para
# que o limite seja um número nosso e não um que muda com a Vercel.
#
# Uma chamada à Groq com 25 s de timeout e duas retentativas podia passar dos
# 60 s: o utilizador escrevia a pergunta e, um minuto depois, recebia um 504 sem
# explicação nenhuma. Pior ainda, uma retentativa com timeout de 8 s já estoura
# os 10 s, e quem corta é a plataforma — que não sabe dizer o que aconteceu.
#
# Por isso: uma tentativa só, com timeout abaixo do limite. Ou a resposta vem,
# ou falhamos nós, com a mensagem que o assistente já sabe escrever. Falhar
# depressa e dizer porquê é melhor do que falhar tarde e não dizer nada.
ECHILO_AI_TIMEOUT = env_number("ECHILO_AI_TIMEOUT", 6.0)
ECHILO_AI_MAX_RETRIES = env_int("ECHILO_AI_MAX_RETRIES", 0)
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
