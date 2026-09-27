"""Testes do armazenamento de ficheiros fora do disco.

O que se decide aqui não é se a Cloudinary está configurada — isso é do
ambiente — mas se o projecto respeita o §6 quando ela está: o que é público sai
público, o que é legal não sai, e nenhum dos dois se serve como se fosse o
outro. Um teste que sóConfirmasse o Backend configurado passaria com os dois
papéis trocados.
"""

from __future__ import annotations

import importlib
from unittest import mock

from cloudinary.exceptions import Error as CloudinaryError
from django.contrib import admin
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.properties.admin import DocumentAccessLogAdmin
from apps.core.storage import (
    TRANSFORMACAO_CAPA,
    CloudinaryDocumentStorage,
    CloudinaryImageStorage,
    CloudinaryNaoConfigurada,
    cloudinary_configurado,
    storage_documentacao,
)
from apps.core.testing import make_owner, make_property, make_user
from apps.properties.models import DocumentAccessLog, Property, PropertyDocument

CREDENCIAL = "cloudinary://123456789012345:abcdefghijklmnopqrstuvwxyz123456@echiloteste"

APENAS_NUVEM = override_settings(
    CLOUDINARY_URL=CREDENCIAL,
    CLOUDINARY_PUBLICAO=True,
    MEDIA_DOCUMENTACAO_BACKEND="apps.core.storage.CloudinaryDocumentStorage",
)


class ConfiguracaoStorageTests(SimpleTestCase):
    """A escolha do backend é feita pela credencial, e não pelo ambiente."""

    def test_sem_credencial_a_cloudinary_nao_esta_configurada(self) -> None:
        """Um `CLOUDINARY_URL` vazio é ausência de configuração, não configuração."""
        with override_settings(CLOUDINARY_URL=""):
            self.assertFalse(cloudinary_configurado())

    def test_uma_url_incompleta_nao_conta_como_configurada(self) -> None:
        """Faltando a chave ou o segredo, o erro tem de dizer o que falta.

        A SDK levanta uma excepção de dentro do pacote, e o rasto não menciona a
        variável: quem lê o erro procura uma biblioteca e não uma configuração.
        """
        for incompleta in (
            "cloudinary://so-a-chave@echiloteste",
            "cloudinary://123456789012345@echiloteste",
            "cloudinary://@echiloteste",
        ):
            with self.subTest(url=incompleta):
                with override_settings(CLOUDINARY_URL=incompleta):
                    self.assertFalse(cloudinary_configurado())

    def test_o_documento_legal_nunca_usa_o_backend_publico(self) -> None:
        """`STORAGES["default"]` é público; o campo do documento declara o seu.

        Se os dois forem o mesmo, uma escritura aparece no catálogo com a mesma
        permissão que uma fotografia de fachada, e nada no código denuncia isso.
        """
        with APENAS_NUVEM:
            self.assertIsInstance(storage_documentacao(), CloudinaryDocumentStorage)
            self.assertNotIsInstance(
                storage_documentacao(), type(CloudinaryImageStorage())
            )

    def test_sem_credencial_a_documentacao_cai_para_o_disco(self) -> None:
        """O desenvolvimento tem de funcionar sem Cloudinary configurada."""
        with override_settings(
            CLOUDINARY_URL="",
            CLOUDINARY_PUBLICAO=False,
            MEDIA_DOCUMENTACAO_BACKEND="django.core.files.storage.FileSystemStorage",
        ):
            self.assertIsInstance(storage_documentacao(), FileSystemStorage)


@APENAS_NUVEM
class URLsCloudinaryTests(SimpleTestCase):
    """As três URLs que a Cloudinary produz, e o que cada uma pode ser."""

    def setUp(self) -> None:
        self.imagem = CloudinaryImageStorage()
        self.documento = CloudinaryDocumentStorage()

    def test_a_url_da_imagem_e_uma_string_e_esta_publica(self) -> None:
        """A SDK devolve `(url, opções)`, e a tupla no HTML parte o `src`.

        O browser lê `('https://…', {})` como um endereço e a imagem não aparece
        — sem erro na consola, que é o que torna este bug difícil de apanhar.
        """
        url = self.imagem.url("abc123")
        self.assertIsInstance(url, str)
        self.assertTrue(url.startswith("https://res.cloudinary.com/"))
        self.assertIn("/image/upload/", url)

    def test_a_url_derivada_e_uma_transformacao_da_mesma_imagem(self) -> None:
        """A versão estreita muda a transformação, não o ficheiro."""
        estreita = self.imagem.url_derivada("abc123", largura=400)
        self.assertIsInstance(estreita, str)
        self.assertIn("w_400", estreita)
        self.assertIn("abc123", estreita)

    def test_a_url_do_documento_aponta_para_o_sitio_e_nao_para_a_nuvem(self) -> None:
        """Um link público de uma escritura é a coisa que o §6 proíbe.

        O `url()` do Django é o que o admin escreve no `href`. Devolver aqui o
        endereço da Cloudinary punha a escritura no endereço, e o endereço é
        público por definição.
        """
        url = self.documento.url("abc123")
        self.assertEqual(url, reverse("properties:documento", kwargs={"identificador": "abc123"}))
        self.assertNotIn("cloudinary", url)
        self.assertTrue(url.startswith("/"))

    def test_o_link_assinado_e_de_entrega_e_nao_da_api(self) -> None:
        """`private_download_url` monta um endereço de API, que o browser não abre.

        A diferença entre as duas funções do SDK é 401 em silêncio: a segunda
        pede chave de API em cabeçalho, e o browser não a tem.
        """
        link = self.documento.link_assinado("abc123")
        self.assertIsInstance(link, str)
        self.assertTrue(link.startswith("https://res.cloudinary.com/"), link)
        self.assertNotIn("api.cloudinary.com", link)
        self.assertIn("/raw/authenticated/", link)
        self.assertIn("s--", link)

    def test_o_identificador_do_documento_nao_parece_em_nome_de_ficheiro(self) -> None:
        """O `public_id` é um uuid, e não o nome que o proprietário enviou.

        O nome de um documento legal é o nome de uma pessoa, e um `public_id` com
        o nome dentro é um índice de nomes de proprietários para quem o adivinhe.
        """
        with mock.patch("apps.core.storage.uuid.uuid4") as gerador:
            gerador.return_value.hex = "0123456789abcdef0123456789abcdef"
            nome = self.documento._nome_opaco()
        self.assertEqual(nome, "0123456789abcdef0123456789abcdef")
        self.assertEqual(len(nome), 32)


@APENAS_NUVEM
class ValidacaoStorageTests(SimpleTestCase):
    """O §6 limita o que entra, e a storage é a última linha."""

    def test_um_executavel_renomeado_a_jpg_e_recusado(self) -> None:
        """O formulário valida o cabeçalho; a storage valida a extensão.

        O `save()` também é chamado pelo admin e por uma importação, e nenhum dos
        dois passa pelo formulário.
        """
        imagem = CloudinaryImageStorage()
        with self.assertRaises(SuspiciousFileOperation):
            imagem._verificar_extensao("escritura.exe")
        with self.assertRaises(SuspiciousFileOperation):
            imagem._verificar_extensao("anuncio.html")

    def test_um_svg_e_recusado_porque_e_codigo_que_o_browser_executa(self) -> None:
        """`.svg` passa como imagem em quase toda a parte, e é um programa."""
        imagem = CloudinaryImageStorage()
        with self.assertRaises(SuspiciousFileOperation):
            imagem._verificar_extensao("planta.svg")

    def test_um_pdf_e_documento_legal_valido(self) -> None:
        """A certidão de registo predial chega em PDF, e é dos documentos do §2.7."""
        documento = CloudinaryDocumentStorage()
        documento._verificar_extensao("certidao.pdf")
        documento._verificar_extensao("escritura.JPG")

    def test_um_ficheiro_sem_extensao_e_recusado(self) -> None:
        """Sem extensão não há como saber o que é, e adivinhar é o que o §6 proíbe."""
        with self.assertRaises(SuspiciousFileOperation):
            CloudinaryDocumentStorage()._verificar_extensao("documento")


class SubmoduloDaCloudinaryTests(SimpleTestCase):
    """O `uploader` tem de estar importado, e ninguém o descobre sem enviar.

    Nenhum teste da suite chegava ao `save()`: em desenvolvimento o backend é o
    do disco, e o caminho da nuvem só corre com `CLOUDINARY_PUBLICAO` ligado. O
    primeiro envio de produção foi o primeiro envio de sempre, e o que apareceu
    foi `AttributeError: module 'cloudinary' has no attribute 'uploader'` — o
    pacote não importa o submódulo por si. Um teste que só confirme o backend
    configurado passava com o `uploader` por importar.
    """

    def test_o_submodulo_do_envio_esta_importado(self) -> None:
        modulo = importlib.import_module("apps.core.storage")
        uploader = getattr(modulo.cloudinary, "uploader", None)
        self.assertIsNotNone(
            uploader,
            "import cloudinary não traz cloudinary.uploader: o save() chama "
            "cloudinary.uploader.upload e o import tem de ser explícito.",
        )
        for nome in ("upload", "destroy"):
            with self.subTest(nome=nome):
                self.assertTrue(
                    hasattr(uploader, nome),
                    f"cloudinary.uploader não tem `{nome}` na versão instalada.",
                )


@APENAS_NUVEM
class EnvioParaACloudinaryTests(SimpleTestCase):
    """O `save()` é o caminho que falhou em produção, e escreve-se aqui."""

    def test_o_save_devolve_o_public_id_e_manda_a_pasta_e_a_transformacao(self) -> None:
        with mock.patch("apps.core.storage.cloudinary.uploader.upload") as envio:
            envio.return_value = {"public_id": "opaco123"}
            nome = CloudinaryImageStorage().save(
                "casa.jpg", ContentFile(b"bytes-de-uma-fotografia")
            )

        self.assertEqual(nome, "opaco123")
        opcoes = envio.call_args.kwargs
        self.assertEqual(opcoes["folder"], "echilo/imoveis")
        self.assertEqual(opcoes["transformation"], TRANSFORMACAO_CAPA)
        self.assertEqual(opcoes["overwrite"], False)
        self.assertEqual(sorted(opcoes["allowed_formats"]), ["jpeg", "jpg", "png", "webp"])

    def test_o_nome_enviado_nao_e_o_nome_que_a_pessoa_escolheu(self) -> None:
        """O §6 não publica o nome do ficheiro de ninguém."""
        with mock.patch("apps.core.storage.cloudinary.uploader.upload") as envio:
            envio.return_value = {"public_id": "opaco123"}
            CloudinaryImageStorage().save(
                "escritura-da-maria-dos-santos.jpg", ContentFile(b"bytes")
            )

        enviado = envio.call_args.kwargs["public_id"]
        self.assertNotIn("maria", enviado)
        self.assertEqual(len(enviado), 32)

    def test_a_recusa_da_nuvem_vira_o_erro_do_projecto(self) -> None:
        """A excepção de dentro do pacote não diz o que a equipa deve fazer."""
        with mock.patch("apps.core.storage.cloudinary.uploader.upload") as envio:
            envio.side_effect = CloudinaryError("credencial recusada")
            with self.assertRaises(CloudinaryNaoConfigurada) as contexto:
                CloudinaryImageStorage().save("casa.jpg", ContentFile(b"bytes"))

        self.assertIn("recusou o envio", str(contexto.exception))

    def test_o_documento_vai_autenticado_e_nunca_com_url_publica(self) -> None:
        with mock.patch("apps.core.storage.cloudinary.uploader.upload") as envio:
            envio.return_value = {"public_id": "escritura1"}
            CloudinaryDocumentStorage().save("escritura.pdf", ContentFile(b"bytes"))

        opcoes = envio.call_args.kwargs
        self.assertEqual(opcoes["type"], "authenticated")
        self.assertEqual(opcoes["resource_type"], "raw")
        self.assertEqual(opcoes["folder"], "echilo/documentos")

    def test_apagar_uma_fotografia_chama_o_destroy(self) -> None:
        """Numa conta paga, lixo custa dinheiro todos os meses."""
        with mock.patch("apps.core.storage.cloudinary.uploader.destroy") as destroy:
            CloudinaryImageStorage().delete("opaco123")

        destroy.assert_called_once_with(
            "opaco123", resource_type="image", type="upload"
        )


@APENAS_NUVEM
class _DocumentoParaAuditar(TestCase):
    """Um imóvel e uma escritura, montados como a equipa os monta."""

    IDENTIFICADOR = "abc123def456"

    def setUp(self) -> None:
        self.agente = make_user(role=User.Role.AGENT, email="agente@echilo.ao")
        self.curador = make_user(role=User.Role.CURATOR, email="curador@echilo.ao")
        self.cliente = make_user(role=User.Role.CLIENT, email="cliente@echilo.ao")
        self.imóvel = make_property(
            curator=self.agente,
            owner=make_owner(created_by=self.agente),
            status=Property.Status.IN_REVIEW,
        )
        self.documento = PropertyDocument.objects.create(
            property=self.imóvel,
            document_type=PropertyDocument.DocumentType.OWNERSHIP_TITLE,
            status=PropertyDocument.Status.PENDING,
        )
        self.documento.file.name = self.IDENTIFICADOR
        self.documento.save(update_fields=["file"])

    def _abrir(self, utilizador: User | None) -> int:
        if utilizador is not None:
            self.client.force_login(utilizador)
        return self.client.get(
            reverse("properties:documento", kwargs={"identificador": self.IDENTIFICADOR})
        ).status_code


class DocumentoLegalTests(_DocumentoParaAuditar):
    """A rota de documentos, com a sessão e o perfil a fazerem o que diz o §6."""

    def test_o_agente_abre_o_documento(self) -> None:
        """O caminho normal da equipa."""
        self.assertEqual(self._abrir(self.agente), 302)

    def test_o_curador_nao_abre_a_escritura(self) -> None:
        """Curador cria e edita imóveis; validar documentos é de `AGENT` e `ADMIN` (§3)."""
        self.assertEqual(self._abrir(self.curador), 403)

    def test_o_cliente_nao_abre_a_escritura(self) -> None:
        """A escritura é do proprietário, e o cliente é alguém que a quer ler."""
        self.assertEqual(self._abrir(self.cliente), 403)

    def test_anonimo_nao_abre_a_escritura(self) -> None:
        """Sem sessão, a resposta é 403 e não 404: não se confirma a existência."""
        self.assertEqual(self._abrir(None), 403)

    def test_abrir_o_documento_registra_quem_e_quando(self) -> None:
        """§6 pede registo de auditoria, e não há forma de o adivinhar depois."""
        self._abrir(self.agente)
        registo = DocumentAccessLog.objects.get(document=self.documento)
        self.assertEqual(registo.actor, self.agente)
        self.assertIsNotNone(registo.created_at)

    def test_o_registo_de_auditoria_e_por_acesso_e_nao_por_sessao(self) -> None:
        """Duas aberturas são dois registos: quem leu quando conta, não quem entrou."""
        self._abrir(self.agente)
        self._abrir(self.agente)
        self.assertEqual(DocumentAccessLog.objects.filter(document=self.documento).count(), 2)

    def test_um_acesso_recusado_nao_deixa_vestigio_de_sucesso(self) -> None:
        """Um 403 que gravasse auditoria enganaria quem lesse o histórico depois."""
        self._abrir(self.curador)
        self.assertEqual(DocumentAccessLog.objects.count(), 0)

    def test_um_identificador_que_nao_existe_nao_e_um_documento(self) -> None:
        """Um uuid inventado dá 404, e não uma página de erro."""
        self.client.force_login(self.agente)
        resposta = self.client.get(
            reverse("properties:documento", kwargs={"identificador": "0" * 32})
        )
        self.assertEqual(resposta.status_code, 404)

    def test_o_post_nao_abre_o_documento(self) -> None:
        """Só há leitura. Um `POST` que devolvesse 302 serviria de base para CSRF."""
        self.client.force_login(self.agente)
        resposta = self.client.post(
            reverse("properties:documento", kwargs={"identificador": self.IDENTIFICADOR})
        )
        self.assertEqual(resposta.status_code, 405)

    def test_a_resposta_nao_e_guardada_por_ninguem(self) -> None:
        """O link aponta para a Cloudinary; um cache intermédio guardava a escritura."""
        self.client.force_login(self.agente)
        resposta = self.client.get(
            reverse("properties:documento", kwargs={"identificador": self.IDENTIFICADOR})
        )
        self.assertEqual(resposta.headers.get("Cache-Control"), "no-store")
        self.assertEqual(resposta.headers.get("Referrer-Policy"), "no-referrer")


class EntregaAssinadaTests(_DocumentoParaAuditar):
    """O redireccionamento tem de levar a um link que a Cloudinary assinou."""

    def test_o_agente_e_redireccionado_para_o_link_assinado(self) -> None:
        """O ficheiro vem da Cloudinary, não passa por esta função.

        Servir os bytes aqui custaria o limite de 10 s e a memória de uma
        invocação para um ficheiro que já está onde deve estar.
        """
        self.client.force_login(self.agente)
        resposta = self.client.get(
            reverse("properties:documento", kwargs={"identificador": self.IDENTIFICADOR})
        )
        self.assertEqual(resposta.status_code, 302)
        destino = resposta.headers["Location"]
        self.assertTrue(destino.startswith("https://res.cloudinary.com/"), destino)
        self.assertIn("/raw/authenticated/", destino)

    def test_o_link_nao_leva_o_ficheiro_em_si(self) -> None:
        """Nada da resposta entrega bytes da escritura ao browser do agente."""
        self.client.force_login(self.agente)
        resposta = self.client.get(
            reverse("properties:documento", kwargs={"identificador": self.IDENTIFICADOR})
        )
        self.assertEqual(b"", resposta.content.strip())


class RegistoDeAuditoriaTests(_DocumentoParaAuditar):
    """A prova sobrevive ao ficheiro, e a imutabilidade dela no painel (§6)."""

    def test_apagar_o_documento_nao_apaga_a_prova_de_quem_o_leu(self) -> None:
        """O ficheiro pode sair; a leitura fica.

        Um imóvel arquivado continua a ter uma escritura que alguém abriu. Com
        `CASCADE`, apagar o ficheiro levava o registo junto e a pergunta "quem
        leu isto em Março" ficava sem resposta — que era o que o registo existia
        para responder. O `SET_NULL` desliga a linha do documento em vez de a
        apagar, e o retrato escrito na altura da leitura é o que sobra.
        """
        self._abrir(self.agente)
        self.assertEqual(DocumentAccessLog.objects.count(), 1)

        self.documento.delete()

        self.assertEqual(DocumentAccessLog.objects.count(), 1)

    def test_a_prova_continua_la_depois_da_apagagem(self) -> None:
        """Sem documento ligado, a linha ainda diz o que foi lido e por quem.

        Um `SET_NULL` que esvaziasse a referência deixaria a prova órfã: o
        registo sobrevivia e não contava nada. É para isso que a referência, o
        tipo e o nome do ficheiro são copiados para a própria linha no momento
        do acesso.
        """
        self._abrir(self.agente)
        self.documento.delete()

        registo = DocumentAccessLog.objects.get()
        self.assertIsNone(registo.document_id)
        self.assertEqual(registo.actor, self.agente)
        self.assertEqual(registo.property_reference, self.imóvel.reference)
        self.assertEqual(
            registo.document_type, PropertyDocument.DocumentType.OWNERSHIP_TITLE
        )
        self.assertEqual(registo.file_name, self.IDENTIFICADOR)

    def test_o_registo_nao_se_escreve_a_mao_no_painel(self) -> None:
        """Um acesso é o que a aplicação registou, não o que alguém inventa."""
        self.client.force_login(self.agente)
        painel = DocumentAccessLogAdmin(DocumentAccessLog, admin.site)
        pedido = RequestFactory().get("/admin/properties/documentaccesslog/add/")
        self.assertFalse(painel.has_add_permission(pedido))

    def test_o_registo_nao_se_altera_nem_se_apaga_no_painel(self) -> None:
        """O registo é imutável depois de escrito, senão não é prova de nada."""
        self.client.force_login(self.agente)
        painel = DocumentAccessLogAdmin(DocumentAccessLog, admin.site)
        pedido = RequestFactory().get("/admin/properties/documentaccesslog/1/change/")
        self.assertFalse(painel.has_change_permission(pedido))
        self.assertFalse(painel.has_delete_permission(pedido))

    def test_o_registo_esta_no_painel_e_mostra_quem_e_quando(self) -> None:
        """A trilha tem de ser legível, ou ninguém a consulta.

        Não se testa por HTTP porque o `/admin` não é do `AGENT`: é de quem está
        em `MANAGER_EMAILS` (§7). O que interessa travar é a inscrição e as
        colunas, que é onde a trilha se perde sem dar erro.
        """
        self.assertIn(DocumentAccessLog, admin.site._registry)
        painel = DocumentAccessLogAdmin(DocumentAccessLog, admin.site)
        self.assertEqual(
            painel.list_display, ("document", "actor", "ip_address", "created_at")
        )
