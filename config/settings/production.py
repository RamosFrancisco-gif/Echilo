"""Ambiente de produção: falha cedo quando falta configuração sensível."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    ALLOWED_HOSTS,
    CLOUDINARY_PUBLICAO,
    CLOUDINARY_URL,
    DATABASES,
    MANAGER_EMAILS,
    SECRET_KEY,
    env,
    env_bool,
    env_int,
    env_list,
    storages,
)

DEBUG = False

if not SECRET_KEY:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured("DJANGO_SECRET_KEY é obrigatória em produção.")
if not ALLOWED_HOSTS:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS é obrigatória em produção.")
if not CLOUDINARY_URL:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured(
        "CLOUDINARY_URL é obrigatória em produção: o MEDIA_ROOT de uma função "
        "serverless é efémero e apaga o ficheiro no fim da invocação seguinte."
    )

# A Vercel dá a cada preview um domínio novo e aleatório. Sem este sufixo, cada
# preview vive com um 400 no ecrã inteiro, e o erro não é de template: é do host.
if env_bool("VERCEL", default=False) and not any(
    host.endswith(".vercel.app") for host in ALLOWED_HOSTS
):  # pragma: no cover - depende do ambiente
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, ".vercel.app"]

# O CSRF compara o `Origin` do browser com uma lista. Na Vercel o domínio de
# produção é o do projecto e cada preview tem o seu, por isso a lista vem do
# ambiente e não é adivinhada aqui.
CSRF_TRUSTED_ORIGINS = [
    origin for origin in env_list("DJANGO_CSRF_TRUSTED_ORIGINS") if origin
]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# A função da Vercel vive, morre e não volta. Uma ligação mantida por 60 s para
# um request que dura 300 ms é uma ligação que ocupa uma das poucas do plano
# Hobby durante quase mais um minuto do que precisava. Zero é a resposta certa
# quando não há processo persistente para a reaproveitar.
#
# Isto vive dentro de `DATABASES["default"]` e não como uma setting de topo:
# `CONN_MAX_AGE = 0` ao nível do módulo criava um nome novo que nada lia, e o
# pooling continuava a 60 s sem dizer nada.
DATABASES["default"]["CONN_MAX_AGE"] = env_int("DJANGO_CONN_MAX_AGE", 0)

# A base de produção do Aiven não está na mesma máquina, e a ligação atravessa
# a rede. Sem `DJANGO_DB_SSL_CA` o PyMySQL entra em modo `PREFERRED`: tenta TLS,
# e se o servidor não oferecer continua em texto claro — sem erro, sem aviso.
# Com a CA configurada a verificação é real, e a senha deixa de poder ser lida
# por quem se faça passar pelo servidor.
#
# A excepção é a base local, onde não há nada para interceptar. Reconhece-se pelo
# nome, e não por um interruptor: um interruptor que alguém desligue para
# Destinationário pode desligar-se para qualquer.
_HOSTS_SEM_REDE = {"localhost", "127.0.0.1", "::1", ""}
if DATABASES["default"]["HOST"] not in _HOSTS_SEM_REDE:  # pragma: no cover - arranque
    if not env("DJANGO_DB_SSL_CA"):
        raise ImproperlyConfigured(
            "DJANGO_DB_SSL_CA é obrigatória em produção: sem a CA, o PyMySQL "
            "tenta TLS mas não verifica o certificado do servidor, e a senha da "
            "base passa pela rede sem que ninguém prove quem está do outro lado."
        )

# `base` já montou um `STORAGES` com o `DEBUG` que encontrou no ambiente, que em
# produção é o do ficheiro `.env` local e não o daqui. A decisão é reescrita com a
# resposta de produção, e a função que a monta é a mesma — o que impede os dois
# ambientes de divergirem em silêncio.
STORAGES = storages(manifest=True, cloudinary=CLOUDINARY_PUBLICAO)

# O limitador de tentativas é a única defesa contra força bruta no login, no
# registo e na recuperação de senha (§6). `LocMemCache` guarda o contador na
# memória do processo, e em serverless cada invocação é um processo novo: o
# limite eram cinco tentativas por invocação, e infinitas por pessoa. Por isso a
# produção usa um cache que sobrevive ao processo.
if env_bool("DJANGO_CACHE_BD", default=CLOUDINARY_PUBLICAO):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": env("DJANGO_CACHE_LOCATION", "echilo_cache"),
            # O prazo vai dentro de `OPTIONS`. Escrito ao nível de cima, é aceite
            # sem erro e ninguém o lê: o backend procura o prazo em `OPTIONS`, e o
            # que ficou de fora era a conta de tentativas acaducar ao fim dos 300 s
            # por omissão do Django, ou seja, o prazo que alguém nunca configurou.
            "OPTIONS": {"TIMEOUT": env_int("DJANGO_CACHE_TIMEOUT", 300)},
        }
    }
else:  # pragma: no cover - cache em memória só para desenvolvimento
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "echilo",
        }
    }

if not CLOUDINARY_PUBLICAO:  # pragma: no cover - guardião de arranque
    raise ImproperlyConfigured(
        "CLOUDINARY_PUBLICAO não pode ser falso em produção: o MEDIA_ROOT de "
        "uma função serverless é efémero."
    )

X_FRAME_OPTIONS = "DENY"
