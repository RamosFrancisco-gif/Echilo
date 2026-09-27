"""Os travões do comando destrutivo são a única barreira contra perda de dados."""

from __future__ import annotations

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.management.commands.recreate_database import (
    PROTECTED_NAMES,
    validate_target,
)
from apps.core.management.utils import NOME_DEMO_PASSWORD, exige_permissao_explicita
from apps.properties.management.commands.seed_demo import DEMO_PASSWORD


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


class SeedDemoGuardTests(SimpleTestCase):
    """Semear em produção é uma decisão, e a decisão tem de ficar escrita.

    O travão antigo era o `DEBUG`, e o `DEBUG` desliga-se com uma variável de
    ambiente: quem protege a base de produção desligava-a com um `set` para poder
    correr o comando. A permissão é um argumento, e quem a vê é a consola.
    """

    def test_sem_permissao_a_producao_e_recusada(self) -> None:
        with self.assertRaisesMessage(CommandError, "--permitir-producao"):
            exige_permissao_explicita(
                debug=False, permitido=False, comando="seed_demo"
            )

    def test_a_permissao_explicita_deixa_passar(self) -> None:
        exige_permissao_explicita(debug=False, permitido=True, comando="seed_demo")

    def test_desenvolvimento_continua_a_passar(self) -> None:
        exige_permissao_explicita(debug=True, permitido=False, comando="seed_demo")

    def test_a_mensagem_diz_o_que_falta(self) -> None:
        """O erro que não diz a flag deixa a pessoa a procurar no código."""
        with self.assertRaises(CommandError) as ctx:
            exige_permissao_explicita(debug=False, permitido=False, comando="seed_demo")
        self.assertIn("seed_demo", str(ctx.exception))
        self.assertIn("--permitir-producao", str(ctx.exception))

    def test_a_flag_no_argumento_e_nao_no_ambiente(self) -> None:
        """Um bypass que uma variável resolve é o que este travão veio substituir."""
        with (
            mock.patch.dict(os.environ, {NOME_DEMO_PASSWORD: "x"}, clear=False),
            self.assertRaisesMessage(CommandError, "--permitir-producao"),
        ):
            exige_permissao_explicita(debug=False, permitido=False, comando="seed_demo")


@override_settings(DEBUG=False)
class SeedDemoEmProducaoTests(TestCase):
    """O que muda quando a semeadura vai para a base de produção."""

    def test_a_conta_de_administracao_nao_e_criada(self) -> None:
        """Uma password no repositório, com `ADMIN`, é o site todo.

        O `get_or_create` não protege: a conta ainda não existe em produção, por
        isso era criada nova, com a senha que está escrita neste ficheiro.
        """
        call_command(
            "seed_demo", "--limit", "1", "--permitir-producao", stdout=None, stderr=None
        )
        User = get_user_model()
        self.assertFalse(User.objects.filter(email="admin@echilo.ao").exists())
        self.assertTrue(User.objects.filter(email="curador@echilo.ao").exists())

    def test_a_password_conhecida_nao_serve_para_entrar(self) -> None:
        call_command(
            "seed_demo", "--limit", "1", "--permitir-producao", stdout=None, stderr=None
        )
        curador = get_user_model().objects.get(email="curador@echilo.ao")
        self.assertFalse(curador.check_password(DEMO_PASSWORD))

    def test_a_password_do_ambiente_e_a_usada(self) -> None:
        with mock.patch.dict(os.environ, {NOME_DEMO_PASSWORD: "Tchapo-novo-2026"}):
            call_command(
                "seed_demo", "--limit", "1", "--permitir-producao", stdout=None, stderr=None
            )
        curador = get_user_model().objects.get(email="curador@echilo.ao")
        self.assertTrue(curador.check_password("Tchapo-novo-2026"))

    def test_o_flush_e_recusado_em_producao(self) -> None:
        """`--flush` apaga `Property.objects.all()`: imóveis, visitas e ofertas.

        O `--help` dizia "remove imóveis de demonstração" e o código apaga todos.
        Em produção essa diferença apaga o trabalho de quem está a usar o site.
        """
        with self.assertRaisesMessage(CommandError, "DEBUG"):
            call_command(
                "seed_demo", "--limit", "1", "--permitir-producao", "--flush",
                stdout=None, stderr=None
            )

    def test_a_senha_gerada_nao_vai_para_o_codigo(self) -> None:
        """Sem `ECHILO_DEMO_PASSWORD` a senha é gerada e mostrada no `stderr`."""
        import io

        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(NOME_DEMO_PASSWORD, None)
            call_command(
                "seed_demo", "--limit", "1", "--permitir-producao",
                stdout=io.StringIO(), stderr=err
            )
        self.assertIn(NOME_DEMO_PASSWORD, err.getvalue())
        self.assertNotIn(DEMO_PASSWORD, err.getvalue())
