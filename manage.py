#!/usr/bin/env python
"""Utilitário de linha de comandos do Django para o projecto Echilo."""

import os
import sys


def main() -> None:
    """Executa a tarefa administrativa pedido pela linha de comandos."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django não está instalado. Active o ambiente e corra `pip install -r requirements.txt`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
