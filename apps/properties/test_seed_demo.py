"""Testes do `seed_demo`, o comando que publica o catálogo de demonstração.

Vêm num módulo próprio porque `tests.py` do app tem quatro mil linhas, e um
ficheiro desse tamanho é um sítio onde um teste novo passa despercebido. O
comando também tem o seu próprio assunto: escreve em produção, e o que se
quer garantir é que repetir a semeadura não duplica o que já foi semeado.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.core.testing import make_owner, make_property, make_user
from apps.properties.models import OwnerProfile, Property

User = get_user_model()


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    """A semeadura de demonstração escreve em produção, e por isso repete-se bem.

    O comando tem `--permitir-producao` e `--flush`, o que quer dizer que alguém o
    corre contra o sítio vivo. Um comando desses que duplica o que semeou não dá
    erro: dá o dobro do catálogo de demonstração, com proprietários novos e tudo.
    """

    def setUp(self) -> None:
        cache.clear()
        self.curador = make_user(role="CURATOR", email="equipa@echilo.ao")
        self.registado = make_property(
            curator=self.curador,
            owner=make_owner(created_by=self.curador),
        )

    def _corre(self, **extra: object) -> str:
        """Corre o comando e devolve o que escreveu."""
        saida = StringIO()
        with redirect_stdout(saida), redirect_stderr(StringIO()):
            call_command("seed_demo", stdout=saida, stderr=StringIO(), **extra)
        return saida.getvalue()

    def test_a_primeira_execucao_publica_o_catalogo(self) -> None:
        self._corre(limit=3)

        self.assertEqual(Property.objects.count(), 4)
        for prop in Property.objects.exclude(pk=self.registado.pk):
            with self.subTest(referencia=prop.reference):
                self.assertEqual(prop.status, Property.Status.PUBLISHED)

    def test_correr_o_comando_duas_vezes_nao_duplica_o_catalogo(self) -> None:
        """Repetir a semeadura é como se avalia a interface mais do que uma vez.

        O guarda que impedia isto comparava a referência, e a referência nunca
        está ocupada: `next_reference()` salta para a primeira livre de propósito.
        O `continue` nunca corria, e a segunda execução publicava o catálogo
        inteiro outra vez. Identificar a linha pelo título e pela província é o que
        fecha a porta.
        """
        self._corre(limit=3)
        primeira = sorted(Property.objects.exclude(pk=self.registado.pk).values_list(
            "reference", flat=True
        ))

        saida = self._corre(limit=3)

        self.assertEqual(Property.objects.count(), 4)
        self.assertEqual(
            sorted(
                Property.objects.exclude(pk=self.registado.pk).values_list(
                    "reference", flat=True
                )
            ),
            primeira,
        )
        self.assertIn("já existe", saida)

    def test_o_flush_limpa_o_catalogo_e_nao_o_ficheiro_de_proprietarios(self) -> None:
        """`--flush` apaga imóveis, não pessoas.

        A semeadura identifica o proprietário pelo número do documento, e esse
        número sai do índice da linha do catálogo. Se `--flush` levasse também o
        `OwnerProfile`, a próxima execução recriaria-os a partir do zero — o
        cadastro de clientes a recomeçar de cada vez que alguém limpa a
        demonstração, que é o contrário do que um ficheiro de clientes deve fazer.

        Este teste também é o único que chega a `_owner()` duas vezes: nas outras
        passagens o guarda de duplicação faz `continue` antes de o proprietário ser
        criado, e um teste que conta proprietários quando ninguém os cria não
        está a testar os proprietários.

        O que se compara são as chaves, não a quantidade: o mesmo número de
        proprietários pode ser o mesmo número de linhas recriadas com outro `pk`.
        """
        self._corre(limit=3)
        antes = sorted(OwnerProfile.objects.values_list("pk", flat=True))

        self._corre(limit=3, flush=True)

        self.assertEqual(
            sorted(OwnerProfile.objects.values_list("pk", flat=True)),
            antes,
            "a semeadura recriou proprietários que já existiam",
        )
        self.assertEqual(Property.objects.exclude(pk=self.registado.pk).count(), 3)

    def test_a_segunda_execucao_nao_duplica_as_contas_de_equipa(self) -> None:
        """As contas de demonstração são `get_or_create` e não se duplicam."""
        self._corre(limit=2)
        antes = User.objects.count()

        self._corre(limit=2)

        self.assertEqual(User.objects.count(), antes)

    def test_o_limite_muda_o_que_e_semeado_e_nao_o_que_ja_esta(self) -> None:
        """Semear três e depois seis acrescenta três: os três primeiros ficam.

        Um guarda que apagasse o que está lá para semear de novo seria tão mau
        como duplicar, e `--flush` já existe para quem quer recomeçar.
        """
        self._corre(limit=3)
        antes = set(
            Property.objects.exclude(pk=self.registado.pk).values_list(
                "reference", flat=True
            )
        )

        self._corre(limit=6)

        depois = set(
            Property.objects.exclude(pk=self.registado.pk).values_list(
                "reference", flat=True
            )
        )
        self.assertEqual(len(depois), 6)
        self.assertTrue(antes <= depois)

    def test_a_semente_nao_apaga_o_que_a_equipa_registou(self) -> None:
        """Sem `--flush` a semeadura só acrescenta, mesmo contra a base de produção."""
        self._corre(limit=2)

        self.assertTrue(Property.objects.filter(pk=self.registado.pk).exists())
        self.assertEqual(Property.objects.count(), 3)
