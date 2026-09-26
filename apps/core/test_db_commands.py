"""Os travões do comando destrutivo são a única barreira contra perda de dados."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.core.management.commands.recreate_database import (
    PROTECTED_NAMES,
    validate_target,
)


class RecreateDatabaseGuardTests(SimpleTestCase):
    """Nenhuma base é eliminada sem DEBUG activo, confirmação e nome seguro."""

    def test_valid_target_passes(self) -> None:
        validate_target("echilo", debug=True, confirmed=True)

    def test_confirmation_is_mandatory(self) -> None:
        with self.assertRaisesMessage(CommandError, "--yes"):
            validate_target("echilo", debug=True, confirmed=False)

    def test_production_is_never_touched(self) -> None:
        with self.assertRaisesMessage(CommandError, "DEBUG"):
            validate_target("echilo", debug=False, confirmed=True)

    def test_confirmation_is_checked_before_debug(self) -> None:
        """Sem `--yes` a resposta é sobre a confirmação, mesmo em produção."""
        with self.assertRaisesMessage(CommandError, "--yes"):
            validate_target("echilo", debug=False, confirmed=False)

    def test_system_databases_are_protected(self) -> None:
        for name in sorted(PROTECTED_NAMES):
            with self.subTest(base=name), self.assertRaisesMessage(CommandError, "não seguro"):
                validate_target(name, debug=True, confirmed=True)

    def test_system_databases_are_protected_case_insensitively(self) -> None:
        with self.assertRaisesMessage(CommandError, "não seguro"):
            validate_target("MySQL", debug=True, confirmed=True)

    def test_unsafe_names_are_rejected(self) -> None:
        for name in ("", "outra base", "db; DROP DATABASE mysql", "echilo`", "echilo--"):
            with self.subTest(base=name), self.assertRaisesMessage(CommandError, "não seguro"):
                validate_target(name, debug=True, confirmed=True)

    def test_command_refuses_without_confirmation(self) -> None:
        """O comando exposto aplica a mesma validação."""
        with override_settings(DEBUG=True), self.assertRaisesMessage(CommandError, "--yes"):
            call_command("recreate_database", stdout=None, stderr=None)

    def test_command_refuses_in_production(self) -> None:
        with override_settings(DEBUG=False), self.assertRaisesMessage(CommandError, "DEBUG"):
            call_command("recreate_database", yes=True, stdout=None, stderr=None)
