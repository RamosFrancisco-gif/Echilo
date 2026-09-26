"""Cria a tabela do cache na base de dados.

O limitador de tentativas (§6) é a única defesa contra força bruta no login, no
registo e na recuperação de senha. Com `LocMemCache` o contador vive na memória
do processo, e numa plataforma serverless cada invocação é um processo novo: o
limite eram cinco tentativas por invocação, e infinitas por pessoa. A tabela é o
que faz o contador sobreviver ao processo.

Django não sabe fazer isto numa migration: `createcachetable` regista o modelo
`CacheEntry` em tempo de execução, e um modelo que só existe depois do arranque
não pode ser o destino de um `CreateModel` escrito à mão. A alternativa seria um
`RunSQL` com o `CREATE TABLE` copiado da versão do Django instalada, que parte
na primeira actualização. Chamar o comando é mais feio e é o que não parte.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import migrations

BACKEND_CACHE_BD = "django.core.cache.backends.db.DatabaseCache"


def _usa_cache_de_bd() -> bool:
    """Diz se o ambiente configurou o cache na base de dados."""
    return settings.CACHES["default"]["BACKEND"] == BACKEND_CACHE_BD


def criar_tabela_cache(apps, schema_editor) -> None:
    """Cria a tabela do cache, e não faz nada se o cache não for o da base de dados."""
    if not _usa_cache_de_bd():
        return
    call_command("createcachetable", verbosity=0)


def apagar_tabela_cache(apps, schema_editor) -> None:
    """Apaga a tabela do cache na reversão.

    Os contadores de tentativas não valem nada depois de um rollback: o
    utilizador que errou cinco vezes volta a ter cinco tentativas, e o
    `DROP TABLE IF EXISTS` evita que a reversão falhe por a tabela não existir.
    """
    if not _usa_cache_de_bd():
        return
    tabela = settings.CACHES["default"].get("LOCATION", "echilo_cache")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(tabela)}")


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunPython(criar_tabela_cache, apagar_tabela_cache),
    ]
