"""Apaga e recria a base de desenvolvimento, com travões contra uso destrutivo."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

# Bases de sistema do MySQL: o comando nunca as pode tocar.
PROTECTED_NAMES = frozenset({"mysql", "sys", "information_schema", "performance_schema"})
SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def validate_target(name: str, *, debug: bool, confirmed: bool) -> None:
    """Recusa a eliminação da base sempre que houver qualquer dúvida.

    Fica separada do comando para que cada travão possa ser testado sem abrir
    uma ligação ao MySQL.
    """
    if not confirmed:
        raise CommandError(
            "Esta operação apaga todos os dados da base. "
            "Volte a executar com --yes para confirmar."
        )
    if not debug:
        raise CommandError(
            "Recriar a base só é permitido com DEBUG activo. "
            "Em produção este comando recusa sempre executar."
        )
    if not name or name.lower() in PROTECTED_NAMES or not SAFE_NAME.match(name):
        raise CommandError(f"Nome de base não seguro: {name!r}")


class Command(BaseCommand):
    help = (
        "Elimina todos os dados da base configurada e cria-a vazia, para depois "
        "aplicar as migrations. Exige DEBUG activo e --yes."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirma a eliminação definitiva de todos os dados.",
        )

    def handle(self, *args, **options) -> None:
        """Recria a base apenas em desenvolvimento e com confirmação explícita."""
        config = connections["default"].settings_dict
        name = config["NAME"]
        validate_target(name, debug=settings.DEBUG, confirmed=bool(options["yes"]))

        self.stdout.write(f"A eliminar todos os dados de `{name}` em {config['HOST']}...")

        connection = connections["default"]
        connection.close()
        # Liga-se à base de sistema para poder eliminar a base do projecto.
        connection.settings_dict["NAME"] = "mysql"
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
                cursor.execute(
                    f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci"
                )
        finally:
            connection.settings_dict["NAME"] = name
            connection.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"Base `{name}` recriada e vazia. Execute `manage.py migrate` a seguir."
            )
        )
