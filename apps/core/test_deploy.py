"""Testes da configuração de produção e do que a Vercel impõe.

Estas são decisões de plataforma, não de produto, e por isso são fáceis de
reverter sem dar erro: trocar `CONN_MAX_AGE` por 60 continua a funcionar, e o
catálogo continua a aparecer. O que falha é o que só se vê semanas depois, com
o produto no ar e o login a aceitar senhas erradas sem limite.

Por isso os testes aqui não verificam que a aplicação arranca. Verificam que
arranca nas condições que a Vercel cria, e que continua a não arrancar quando
lhe falta um segredo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

RAIZ = Path(__file__).resolve().parents[2]

# O que a Vercel põe no ambiente de uma função. `VERCEL=1` é posto pela própria
# plataforma e não se configura à mão.
AMBIENTE_VERCEL = {
    "VERCEL": "1",
    "DJANGO_SECRET_KEY": "chave-de-teste-com-tamanho-suficiente-1234567890",
    "DJANGO_ALLOWED_HOSTS": "echilo.ao",
    "CLOUDINARY_URL": "cloudinary://123456789012345:abcdefghijklmnopqrstuvwxyz123456@echiloteste",
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
}

# O que a função tem de reportar. Vai por código e não porvalor hard-coded
# para que o teste não possa passar por estar a comparar consigo próprio.
PERGUNTA = (
    "import django, json; django.setup();"
    "from django.conf import settings as s;"
    "print(json.dumps({"
    "'conn_max_age': s.DATABASES['default']['CONN_MAX_AGE'],"
    "'cache': s.CACHES['default']['BACKEND'],"
    "'cache_lugar': s.CACHES['default'].get('LOCATION'),"
    "'cache_timeout': s.CACHES['default'].get('OPTIONS', {}).get('TIMEOUT'),"
    "'cache_timeout_fora': s.CACHES['default'].get('TIMEOUT'),"
    "'armazenamento': s.STORAGES['default']['BACKEND'],"
    "'documentacao': s.MEDIA_DOCUMENTACAO_BACKEND,"
    "'hosts': s.ALLOWED_HOSTS,"
    "'timeout_ia': s.ECHILO_AI_TIMEOUT,"
    "'tentativas_ia': s.ECHILO_AI_MAX_RETRIES,"
    "'static': s.STATIC_URL,"
    "}))"
)


def _python(codigo: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Corre um trecho de Python com o ambiente de produção da Vercel."""
    ambiente = {**os.environ, **AMBIENTE_VERCEL, **(extra or {})}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", codigo],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        env=ambiente,
        timeout=180,
        check=False,
    )


def _perguntar(extra: dict[str, str] | None = None) -> dict[str, object]:
    """Arranca o Django em condições de produção e devolve as settings."""
    resultado = _python(PERGUNTA, extra)
    if resultado.returncode != 0:
        raise AssertionError(f"O Django não arrancou:\n{resultado.stderr}")
    return json.loads(resultado.stdout.strip().splitlines()[-1])


class GuardioesProducaoTests(SimpleTestCase):
    """Produção tem de recusar arrancar sem configuração, e dizer o que falta."""

    def test_sem_chave_secreta_a_producao_nao_arranca(self) -> None:
        """Um valor por omissão inseguro em produção é pior do que não arrancar."""
        resultado = _python("import django; django.setup()", {"DJANGO_SECRET_KEY": ""})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", resultado.stderr)

    def test_sem_hosts_a_producao_nao_arranca(self) -> None:
        """`ALLOWED_HOSTS` vazio com `DEBUG=False` recusa todos os pedidos.

        É um sítio inteiro a devolver 400, e o sintoma parece um problema de DNS.
        """
        resultado = _python("import django; django.setup()", {"DJANGO_ALLOWED_HOSTS": ""})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", resultado.stderr)

    def test_sem_cloudinary_a_producao_nao_arranca(self) -> None:
        """O `MEDIA_ROOT` de uma função serverless é efémero, e apaga tudo.

        Deixar arrancar e descobrir isso com o catálogo sem fotografias é perder
        as imagens que já lá estavam. A falha tem de ser no arranque, e não na
        primeira visita de um cliente.
        """
        resultado = _python("import django; django.setup()", {"CLOUDINARY_URL": ""})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("CLOUDINARY_URL", resultado.stderr)

    def test_a_mensagem_da_cloudinary_explica_a_causa_e_nao_so_o_sintoma(self) -> None:
        """`CLOUDINARY_URL é obrigatória` manda quem o lê procurar a variável.

        Dizer que o disco da função é efémero diz porque é que a variável é
        obrigatória, e é a diferença entre corrigir em dez segundos e procurar
        durante uma hora.
        """
        resultado = _python("import django; django.setup()", {"CLOUDINARY_URL": ""})
        self.assertIn("efémero", resultado.stderr)


class LimitesDaPlataformaTests(SimpleTestCase):
    """As promessas que o `vercel.json` faz e o código tem de cumprir."""

    def _limite_da_funcao(self) -> float:
        vercel = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))
        return float(vercel["functions"]["api/index.py"]["maxDuration"])  # type: ignore[index]

    def test_o_timeout_do_assistente_cabe_dentro_do_limite_da_funcao(self) -> None:
        """Uma chamada à IA mais lenta que a função é uma resposta que não chega.

        O `maxDuration` corta a invocação, e o corte da plataforma não traz
        mensagem nenhuma: o utilizador escrevia a pergunta e via um 504. A
        diferença entre os dois números é a margem que sobra para o arranque, a
        base de dados e o render do template.
        """
        limite = self._limite_da_funcao()
        resposta = _perguntar()
        self.assertLess(
            resposta["timeout_ia"],  # type: ignore[operator]
            limite,
            f"O timeout da IA ({resposta['timeout_ia']}s) não cabe em {limite}s.",
        )

    def test_as_retentativas_da_ia_tambem_cabem_no_limite(self) -> None:
        """O SDK conta o timeout por tentativa, e a Vercel conta o total.

        Uma retentativa com 6 s de timeout são 12 s, que é mais do que o limite.
        Aí o corte passava a ser da plataforma, e o corte da plataforma não sabe
        dizer o que estava a acontecer.
        """
        limite = self._limite_da_funcao()
        resposta = _perguntar()
        total = resposta["timeout_ia"] * (resposta["tentativas_ia"] + 1)  # type: ignore[operator]
        self.assertLess(total, limite, f"As retentativas estouram os {limite}s.")

    def test_a_conexao_nao_sobrevive_a_uma_invocacao(self) -> None:
        """`CONN_MAX_AGE=60` numa função sem processo é uma ligação a mais.

        A função morre no fim do pedido e a ligação fica a ocupar uma das poucas
        do plano até expirar. Com um catálogo respondido em 300 ms, sessenta
        segundos de ligação reservada são sessenta segundos de plano pagos por
        três décimos de segundo de trabalho.
        """
        self.assertEqual(_perguntar()["conn_max_age"], 0)

    def test_o_limite_da_funcao_e_o_que_diz_o_plano_e_nao_o_que_diz_o_juizo(self) -> None:
        """O número está escrito no `vercel.json` para ser lido e discutido.

        Escrevê-lo nas settings e repetir o valor em dois sítios dava dois
        números que divergem em silêncio. Aqui há um só, e os testes acima leem
        esse mesmo.
        """
        self.assertIn("maxDuration", json.loads((RAIZ / "vercel.json").read_text("utf-8"))["functions"]["api/index.py"])  # type: ignore[index]

    def test_a_build_corre_o_collectstatic(self) -> None:
        """Sem `collectstatic`, a função não tem CSS nem JS nenhum.

        O `whitenoise` serve de `STATIC_ROOT`, e `STATIC_ROOT` nasce vazio. O
        sítio responde 200 e aparece sem estilo, que é o defeito que passa
        despercebido num teste que só verifica o código de estado.
        """
        comando = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))["buildCommand"]
        self.assertIn("collectstatic", comando)  # type: ignore[operator]
        self.assertIn("--noinput", comando)  # type: ignore[operator]

    def test_a_build_nao_corre_migrations(self) -> None:
        """Cada preview partilharia a base de dados de produção.

        `migrate` no build aplica a cada preview o esquema que o ramo principal
        ainda não tem, e desfazer isso não tem comando. A migração corre à mão,
        ou num passo de CI, e nunca como efeito de um build.
        """
        comando = str(json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))["buildCommand"])
        self.assertNotIn("migrate", comando)


class EntregaDeFicheirosTests(SimpleTestCase):
    """O que a Cloudinary guarda tem de ser o que a produção declara guardar."""

    def test_a_producao_guarda_na_nuvem_e_nao_no_disco(self) -> None:
        """As duas respostas têm de ser as duas, e não só a primeira."""
        resposta = _perguntar()
        self.assertEqual(resposta["armazenamento"], "apps.core.storage.CloudinaryImageStorage")
        self.assertEqual(
            resposta["documentacao"], "apps.core.storage.CloudinaryDocumentStorage"
        )

    def test_a_documentacao_legal_nao_partilha_o_backend_publico(self) -> None:
        """As fotografias e as escrituras não podem ter o mesmo destino.

        Se os dois backend fossem o mesmo, a escritura sairia com a mesma
        permissão que uma fotografia de fachada, e nada no código denunciava isso:
        os dois `url()` funcionariam e a única diferença seria o nome.
        """
        resposta = _perguntar()
        self.assertNotEqual(resposta["armazenamento"], resposta["documentacao"])


class CachePartilhadoTests(SimpleTestCase):
    """O limitador de tentativas é a defesa do §6 e não pode morrer com o processo."""

    def test_a_producao_conta_tentativas_numa_estrutura_que_sobrevive(self) -> None:
        """`LocMemCache` em serverless é cinco tentativas por invocação.

        Cada invocação é um processo novo, por isso o contador recomeça a zero em
        cada pedido. O limite continuava a existir no código e a não existir na
        prática, que é a forma mais cara de uma defesa parecer activa.
        """
        resposta = _perguntar()
        self.assertEqual(resposta["cache"], "django.core.cache.backends.db.DatabaseCache")
        self.assertTrue(resposta["cache_lugar"])

    def test_o_desenvolvimento_usa_memoria_para_nao_precisar_de_tabela(self) -> None:
        """Em desenvolvimento o cache em memória poupa um `createcachetable`."""
        self.assertEqual(
            settings.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )


class ProxyEHostsTests(SimpleTestCase):
    """A Vercel é um proxy, e o Django precisa de saber disso."""

    def test_o_proxy_https_e_declarado(self) -> None:
        """Sem isto, `request.is_secure()` é falso e o redireccionamento para
        HTTPS faz um laço: o Django diz "vá para https" e o proxy repete o pedido
        em http, para sempre."""
        self.assertIn("SECURE_PROXY_SSL_HEADER", (RAIZ / "config" / "settings" / "production.py").read_text("utf-8"))

    def test_a_previsualizacao_da_vercel_tem_host_permitido(self) -> None:
        """Cada preview da Vercel nasce num domínio aleatório.

        Sem `.vercel.app` nos hosts, cada preview abre com 400 em todas as páginas
        — e o erro não é de template nem de rota, por isso custa a entender.
        """
        hosts = _perguntar()["hosts"]
        self.assertTrue(
            any(str(host).endswith(".vercel.app") for host in hosts),  # type: ignore[union-attr]
            f"Nenhum host de preview autorizado: {hosts}",
        )


class EntradaVercelTests(SimpleTestCase):
    """A Vercel encontra a WSGI sozinha, e isso depende do nome da variável."""

    def _texto(self) -> str:
        return (RAIZ / "api" / "index.py").read_text(encoding="utf-8")

    def test_o_entrypoint_exporta_a_variavel_que_a_vercel_procura(self) -> None:
        """A runtime Python de `api/index.py` procura uma WSGI chamada `app`."""
        self.assertIn("as app", self._texto())

    def test_o_entrypoint_nao_arranca_em_desenvolvimento(self) -> None:
        """`config.wsgi` usa `development` por omissão, e isso em produção é um
        sítio aberto.

        `DEBUG=True` mostra tracebacks com segredos e desliga os cookies
        `secure`. A omissão é uma linha e o efeito é o sítio inteiro.
        """
        self.assertIn("config.settings.production", self._texto())


class DependenciasTests(SimpleTestCase):
    """O que a Vercel consegue instalar é a diferença entre build e não-build."""

    def _requisitos(self) -> str:
        return (RAIZ / "requirements.txt").read_text(encoding="utf-8")

    def test_o_mysqlclient_esta_fora_porque_exige_headers_de_c(self) -> None:
        """A Vercel não tem headers do MySQL, e o build falhava antes do Django.

        `mysqlclient` compila C. O `PyMySQL` é o mesmo protocolo em Python puro,
        e o `config/__init__.py` apresenta-o ao Django com o nome que ele espera.
        """
        self.assertNotIn("mysqlclient", self._requisitos())
        self.assertIn("PyMySQL", self._requisitos())

    def test_a_cryptografia_vem_porque_o_mysql_8_autentica_com_caching_sha2(self) -> None:
        """Sem `cryptography`, o `PyMySQL` recusa a ligação a um MySQL gerido.

        O erro é `cryptography is required`, e aparece no primeiro pedido em vez
        de no arranque — o pior sítio para ele aparecer, porque o build passa e o
        sítio está no ar.
        """
        self.assertIn("cryptography", self._requisitos())

    def test_o_driver_e_apresentado_ao_django_antes_de_qualquer_acesso(self) -> None:
        """O import tem de estar no pacote de configuração, e não dentro de uma app.

        Uma app só é importada quando o Django a vai usar, e a ligação à base de
        dados acontece antes disso.
        """
        texto = (RAIZ / "config" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("install_as_MySQLdb", texto)

    def test_a_cloudinary_esta_declarada(self) -> None:
        """A storage de produção é uma importação no topo do módulo."""
        self.assertIn("cloudinary", self._requisitos())


class UrlsEstaticasTests(SimpleTestCase):
    """`STATIC_URL` e `MEDIA_URL` são endereços, e não caminhos de ficheiro."""

    def test_as_urls_comecam_por_barra(self) -> None:
        """Sem a barra inicial, o browser resolve `static/css/x.css` contra o
        caminho actual: em `/pesquisa/` pede `/pesquisa/static/css/x.css`, que é
        404, e o catálogo fica sem estilo. Não há erro na consola que diga
        «faltou a barra».

        Com a barra, `/static/...` é `/static/...` esteja o browser onde estiver.
        """
        self.assertTrue(settings.STATIC_URL.startswith("/"), settings.STATIC_URL)
        self.assertTrue(settings.MEDIA_URL.startswith("/"), settings.MEDIA_URL)


class BuildEstaticoTests(SimpleTestCase):
    """O `collectstatic` de produção, que é o que a Vercel corre no build.

    Estes testes precisam de correr de verdade. Um ficheiro de estático em falta
    não dá erro no browser, não dá erro em nenhum pedido e não aparece em
    nenhum teste de estado HTTP: aparece quando o `collectstatic` de produção
    falha, que é no build, que é onde não há ninguém a ver.
    """

    def test_o_collectstatic_de_producao_consegue_correr(self) -> None:
        """O build tem de terminar. Um `MissingFileError` aqui é build vermelho."""
        resultado = _python("import django; django.setup(); from django.core.management import call_command; call_command('collectstatic', '--noinput', verbosity=0)")
        self.assertEqual(resultado.returncode, 0, resultado.stderr[-2500:])

    def test_o_collectstatic_de_producao_gera_o_manifesto(self) -> None:
        """Sem manifesto o `whitenoise` não sabe que ficheiro responde a cada nome.

        O `base.html` aponta para `static/css/echilo.<hash>.css`. Sem o manifesto,
        esse nome não existe em lado nenhum e a página aparece sem estilo — com
        200 e sem uma linha na consola que diga o que falta.
        """
        self.test_o_collectstatic_de_producao_consegue_correr()
        manifesto = RAIZ / "staticfiles" / "staticfiles.json"
        self.assertTrue(manifesto.exists(), "collectstatic não gerou o manifesto.")
        self.assertIn("css/echilo", manifesto.read_text(encoding="utf-8"))

    def test_nenhum_estatico_referencia_um_ficheiro_que_nao_enviamos(self) -> None:
        """Um `sourceMappingURL` para um `.map` ausente quebra o build.

        O `leaflet.js` vendorizado vinha com essa referência e o `.map` não foi
        vendorizado: quase um megabyte, e só serve a depurar. O sintoma aparece
        no build e fala de um ficheiro que ninguém sabe porque está ali.
        """
        for caminho in (RAIZ / "static" / "vendor").rglob("*.js"):
            texto = caminho.read_text(encoding="utf-8", errors="replace")
            for linha in texto.splitlines():
                if "sourceMappingURL=" not in linha:
                    continue
                with self.subTest(ficheiro=caminho.name):
                    mapa = linha.split("sourceMappingURL=")[-1].strip()
                    alvo = (caminho.parent / mapa).resolve()
                    self.assertTrue(
                        alvo.exists(),
                        f"{caminho.name} aponta para {mapa}, que não está vendorizado. "
                        "Ou se vendoriza o mapa, ou se remove a linha.",
                    )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "echilo_tabela_cache",
            "OPTIONS": {"TIMEOUT": 1},
        }
    }
)
class CacheDaBaseDeDadosTests(TestCase):
    """O limitador de tentativas tem de sobreviver à função (§6).

    O cache de cada invocação é novo. Com o cache local, o `login` aceitava
    senhas erradas para sempre: a contagem começava a zero a cada pedido, e o
    limite de tentativas passava a não existir sem dar nenhum erro. Por isso a
    produção aponta para a base de dados, e estes testes escrevem e leem a sério.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Cria a tabela como a migration cria.

        A tabela é criada aqui e não pela migration porque a migration corre com
        as settings do ambiente de teste, onde o cache é local e por isso não
        cria tabela nenhuma. Este teste não está a verificar a migration — isso
        é o teste de baixo — está a verificar que o backend escreve e lê.
        """
        super().setUpClass()
        call_command("createcachetable", database="default", verbosity=0)

    @classmethod
    def tearDownClass(cls) -> None:
        """Deixa a base de teste como estava, para a ordem dos testes não importar."""
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS echilo_tabela_cache")
        super().tearDownClass()

    def test_o_cache_da_base_de_dados_guarda_e_devolve(self) -> None:
        """Um valor escrito numa invocação tem de existir na seguinte."""
        cache = caches["default"]
        cache.set("echilo:teste", 42, 60)
        self.assertEqual(cache.get("echilo:teste"), 42)
        cache.delete("echilo:teste")
        self.assertIsNone(cache.get("echilo:teste"))

    def test_o_timeout_configurado_chega_ao_backend(self) -> None:
        """`DJANGO_CACHE_TIMEOUT` tem de chegar ao `timeout_by_key` do backend.

        O `TIMEOUT` escrito no nível superior de `CACHES` é uma opção que o
        backend lê de `OPTIONS`, e não de lado: posto no sítio errado é aceite
        sem erro e ignorado, e a conta de tentativas fica com o prazo por omissão
        do Django em vez do prazo que alguém configurou.
        """
        configuracao = _perguntar({"DJANGO_CACHE_TIMEOUT": "900"})
        self.assertEqual(configuracao["cache_timeout"], 900)
        self.assertIsNone(
            configuracao["cache_timeout_fora"],
            "O TIMEOUT também está no nível superior, onde ninguém o lê.",
        )

    def test_o_timeout_por_omissao_e_o_do_django(self) -> None:
        """Sem `DJANGO_CACHE_TIMEOUT` a conta de tentativas esgota-se sozinha."""
        self.assertEqual(_perguntar()["cache_timeout"], 300)

    def test_o_limite_de_tentativas_cruza_invocacoes(self) -> None:
        """A conta de tentativas é o que a base de dados tem de guardar.

        Um cache local zera a cada invocação e o limite de tentativas nunca
        dispara. Aqui as escritas são operações distintas de base de dados, e é
        isso que a Vercel faz entre um pedido e o seguinte.
        """
        cache = caches["default"]
        for tentativa in range(3):
            cache.set(f"echilo:tentativa:{tentativa}", 1, 60)
            self.assertEqual(cache.incr(f"echilo:tentativa:{tentativa}"), 2)

    def test_a_producao_usa_o_cache_da_base_de_dados(self) -> None:
        """Um backend em memória entre invocações não é cache nenhum."""
        self.assertEqual(
            _perguntar()["cache"], "django.core.cache.backends.db.DatabaseCache"
        )

    def test_a_migration_da_tabela_de_cache_esta_escrita(self) -> None:
        """A tabela é criada por migration, não à mão em cada ambiente.

        Um `createcachetable` manual funciona até alguém criar uma base nova e se
        esquecer, e a falha só aparece quando um utilizador tenta entrar.
        """
        migration = RAIZ / "apps" / "core" / "migrations" / "0001_tabela_cache.py"
        self.assertTrue(migration.exists(), "A migration da tabela de cache desapareceu.")
        texto = migration.read_text(encoding="utf-8")
        self.assertIn("createcachetable", texto)
        self.assertIn("apagar_tabela_cache", texto, "Falta a inversão da migration.")


class ComandosDeDesenvolvimentoTests(SimpleTestCase):
    """Comandos que escrevem em disco e mexe na base de produção."""

    def test_o_travao_recusa_fora_de_desenvolvimento(self) -> None:
        """O erro tem de dizer qual é o comando e porquê.

        Um `PermissionError` do sistema de ficheiros do build fala de `/var/task`
        e não diz que o comando não devia ter corrido ali.
        """
        from django.core.management.base import CommandError

        from apps.core.management.utils import exige_desenvolvimento

        with self.assertRaises(CommandError) as ctx:
            exige_desenvolvimento(debug=False, comando="seed_demo")
        self.assertIn("seed_demo", str(ctx.exception))
        self.assertIn("DEBUG", str(ctx.exception))

    def test_o_travao_deixa_passar_em_desenvolvimento(self) -> None:
        """Sem isto, o comando de demonstração deixava de servir para nada."""
        from apps.core.management.utils import exige_desenvolvimento

        exige_desenvolvimento(debug=True, comando="seed_demo")

    def test_a_verificacao_das_fracas_continua_livre(self) -> None:
        """`--check` só lê, e é assim que se confirma a deriva num build.

        O travão está no caminho da escrita e não no `handle`: posto antes do
        `--check`, a verificação deixava de poder correr em lado nenhum.
        """
        texto = (RAIZ / "apps" / "properties" / "management" / "commands" / "build_admin_boundaries.py").read_text(
            encoding="utf-8"
        )
        posicao_check = texto.index("if options[\"check\"]:")
        posicao_travao = texto.index("exige_desenvolvimento(debug=")
        self.assertLess(
            posicao_check,
            posicao_travao,
            "O travão está antes do `--check` e trava a verificação.",
        )
