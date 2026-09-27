"""Armazenamento de ficheiros fora do disco do servidor.

A Vercel dá a cada função um disco efémero e só de leitura fora de `/tmp`. Uma
fotografia de imóvel ou uma escritura guardadas em `MEDIA_ROOT` desapareciam no
fim da invocação seguinte, e o `MEDIA_ROOT` no pacote da função é `/var/task`,
que nem sequer aceita escrita. Não há configuração que resolva isto: os
ficheiros têm de viver noutro sítio, e esse sítio é a Cloudinary.

O que a Cloudinary resolve e o disco não resolvia:

- As imagens chegam transformadas e re-codificadas, porque a transformação é
  aplicada no próprio envio (§6: nunca servir o ficheiro como veio).
- Os documentos legais são entregues com autenticação, e não com um URL público.

São dois backends e não um com uma flag porque as duas regras não são as
mesmas, e um backend com opção é um backend em que ninguém sabe se está a
proteger o que deve.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import unquote, urlparse

# O `uploader` é importado à mão e não por efeito colateral. `import cloudinary`
# traz `config` e deixa `uploader` e `utils` de fora, e o `save()` chama
# `cloudinary.uploader.upload`: sem esta linha o erro só nasce no primeiro envio
# de produção, como `AttributeError: module 'cloudinary' has no attribute
# 'uploader'`, e um import que parece redundante é precisamente o que um
# clean-up apaga. `apps.core.test_storage` fixa o contrato.
import cloudinary
import cloudinary.uploader
import cloudinary.utils
from cloudinary.exceptions import Error as CloudinaryError
from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

# A capa de um imóvel aparece num cartão de catálogo, na ficha e na galeria. A
# 2000 px de lado maior, nenhuma perde píxeis a sério e todas deixam de pesar
# centenas de kilobytes. `q_auto` e `f_jpg` cumprem o §6: o ficheiro servido nunca
# é o que o utilizador enviou.
TRANSFORMACAO_CAPA = "c_limit,w_2000,h_2000,q_auto:good,f_jpg"

FORMATOS_IMAGEM = ("jpg", "jpeg", "png", "webp")

# O `accept` do input de ficheiros quer MIME types, e não extensões: o browser
# filtra o diálogo de escolha por aquilo que o campo diz, e `accept="jpg,png"`
# não filda nada. Vive ao lado de `FORMATOS_IMAGEM` para que os dois não
# divirjam — o campo aceitaria um formato que o servidor recusa, e a recusa
# chegaria depois de a pessoa já ter escolhido o ficheiro.
MIME_IMAGEM_ACEITE = "image/jpeg,image/png,image/webp"

# O §6 só aceita ficheiros de imagem. Um `.svg` é um programa que o browser
# executa, e um `.html` servido de um domínio nosso é pior.
EXTENSOES_IMAGEM = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Documentos legais: o que a equipa precisa de abrir e verificar. O §2.7 lista
# escritura, certidão, IUR, contrato, identificação e conformidade, que são
# imagens ou PDF. O resto não entra.
EXTENSOES_DOCUMENTO = frozenset({".jpg", ".jpeg", ".png", ".webp", ".pdf"})


class CloudinaryNaoConfigurada(Exception):
    """A storage foi escolhida mas não há credencial para a usar."""


def cloudinary_configurado() -> bool:
    """Diz se há `CLOUDINARY_URL` com os três valores de que a SDK precisa."""
    return _partes_da_url() is not None


def _partes_da_url() -> dict[str, str] | None:
    """Extrai cloud name, API key e API secret do `CLOUDINARY_URL`.

    O formato é `cloudinary://<api_key>:<api_secret>@<cloud_name>`, e as três
    peças são o segredo inteiro. Uma URL a que falte uma delas não é uma
    configuração a meio: a SDK levanta um erro de dentro do pacote, e o rasto
    não diz que falta o `CLOUDINARY_URL`.
    """
    crua = getattr(settings, "CLOUDINARY_URL", "")
    if not crua:
        return None
    partes = urlparse(crua)
    if not partes.hostname or not partes.username or not partes.password:
        return None
    return {
        "cloud_name": partes.hostname,
        "api_key": unquote(partes.username),
        "api_secret": unquote(partes.password),
    }


def _configurar() -> None:
    """Configura a SDK a partir das settings, sem confiar no ambiente.

    A SDK lê o `CLOUDINARY_URL` por si própria, mas só quando é importada. Ler a
    variável aqui e passar os valores é o que garante que o mesmo código
    funciona com a variável no `.env` local e na da Vercel.
    """
    partes = _partes_da_url()
    if partes is None:
        raise CloudinaryNaoConfigurada(
            "CLOUDINARY_URL tem de ser cloudinary://<api_key>:<api_secret>@<cloud_name>."
        )
    cloudinary.config(
        cloud_name=partes["cloud_name"],
        api_key=partes["api_key"],
        api_secret=partes["api_secret"],
        secure=True,
    )


def _url_de_entrega(identificador: str, **opcoes: Any) -> str:
    """URL de entrega da Cloudinary, já sem a segunda metade do retorno.

    A `cloudinary_url` da SDK devolve `(url, opções)`, não uma string. Devolvê-la
    tal como vem escreve no HTML `('https://...', {})` — que o browser lê como
    um URL partido e o `src` cai sem dar erro nenhum, que é a pior forma de
    falhar: a imagem some e a consola fica limpa.
    """
    if not identificador:
        return ""
    devolvido = cloudinary.utils.cloudinary_url(identificador, **opcoes)
    if isinstance(devolvido, tuple):
        devolvido = devolvido[0]
    return devolvido or ""


@deconstructible
class _BaseCloudinary(Storage):
    """O que as duas variantes fazem igual: enviar, nomear e apagar."""

    resource_type = "image"
    delivery_type = "upload"
    permitted_extensions: frozenset[str] = frozenset()
    pasta = ""

    def __init__(self, **kwargs: Any) -> None:
        _configurar()
        self._pasta = kwargs.pop("pasta", self.pasta)

    def _nome_opaco(self) -> str:
        """Identificador opaco do ficheiro na Cloudinary.

        Derivar o nome do que o utilizador enviou seria publicar o nome, e o
        nome de um documento legal é o nome de uma pessoa.
        """
        return uuid.uuid4().hex

    def _verificar_extensao(self, nome: str) -> None:
        """Recusa o que não é do tipo, mesmo que o formulário o tenha deixado passar.

        O formulário é a primeira linha e a storage é a segunda. A segunda existe
        porque a primeira só corre no caminho do browser, e o `save()` também é
        chamado pelo admin, por uma importação e por um `seed`.
        """
        if not self.permitted_extensions:
            return
        terminacao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
        if terminacao not in self.permitted_extensions:
            raise SuspiciousFileOperation(
                f"Ficheiro '{nome}' não é do tipo aceite "
                f"({', '.join(sorted(self.permitted_extensions))})."
            )

    def save(self, name: str, content: File, max_length: int | None = None) -> str:
        """Envia o ficheiro e devolve o identificador com que a Cloudinary o guarda."""
        self._verificar_extensao(name)
        opcoes: dict[str, Any] = {
            "resource_type": self.resource_type,
            "type": self.delivery_type,
            "overwrite": False,
        }
        if self._pasta:
            opcoes["folder"] = self._pasta
        if self.resource_type == "image":
            opcoes["allowed_formats"] = list(FORMATOS_IMAGEM)
            opcoes["transformation"] = TRANSFORMACAO_CAPA
        else:
            opcoes["allowed_formats"] = sorted(
                ext.lstrip(".") for ext in self.permitted_extensions
            )

        bruto = content.read()
        try:
            resposta = cloudinary.uploader.upload(
                ContentFile(bruto), public_id=self._nome_opaco(), **opcoes
            )
        except CloudinaryError as erro:  # pragma: no cover - depende da rede
            raise CloudinaryNaoConfigurada(f"A Cloudinary recusou o envio: {erro}") from erro
        return resposta["public_id"]

    def delete(self, name: str) -> None:
        """Apaga o ficheiro: numa conta paga, lixo custa dinheiro todos os meses."""
        try:
            cloudinary.uploader.destroy(
                name, resource_type=self.resource_type, type=self.delivery_type
            )
        except CloudinaryError:  # pragma: no cover - apagar nunca é caminho crítico
            pass

    def exists(self, name: str) -> bool:
        """Não pergunta à Cloudinary.

        O `public_id` é um uuid, por isso colisão não há e a resposta é sempre
        que o sitio está livre. Cada gravação pedir uma ida à rede custaria uma
        ida à rede em cada upload.
        """
        return False

    def size(self, name: str) -> int:  # pragma: no cover - a Cloudinary não devolve
        return 0

    def path(self, name: str) -> str:
        raise NotImplementedError(
            "Os ficheiros da Cloudinary não têm caminho local: não há disco por trás."
        )

    def listdir(self, path: str):  # pragma: no cover - o projecto não lista
        raise NotImplementedError("A listagem remota não é necessária ao Echilo.")


class CloudinaryImageStorage(_BaseCloudinary):
    """Fotografias dos imóveis: públicas, transformadas na entrega."""

    resource_type = "image"
    delivery_type = "upload"
    pasta = "echilo/imoveis"
    permitted_extensions = EXTENSOES_IMAGEM

    def url(self, name: str) -> str:
        """URL de entrega directa: uma fotografia de capa é o produto a mostrár-se."""
        return _url_de_entrega(
            name, resource_type=self.resource_type, type=self.delivery_type
        )

    def url_derivada(self, name: str, *, largura: int) -> str:
        """Uma versão mais estreita da mesma imagem, sem novo ficheiro.

        O cartão do catálogo é estreito e a ficha é larga. Servir a versão larga
        nos dois é servir 2000 px a quem precisa de 400, num site que roda numa
        rede móvel.
        """
        return _url_de_entrega(
            name,
            resource_type=self.resource_type,
            type=self.delivery_type,
            transformation=[{"width": largura, "crop": "limit", "quality": "auto"}],
        )


class CloudinaryDocumentStorage(_BaseCloudinary):
    """Documentação legal: autenticada, e nunca servida por URL público.

    O §6 proíbe a documentação legal em URL público, e o `url()` do Django é
    exactamente o que um URL público é. Por isso este backend não devolve a URL
    da Cloudinary: devolve uma rota do próprio sítio, que exige sessão e perfil
    `AGENT` ou `ADMIN`, e que por sua vez entrega um link temporário e assinado.

    A `delivery_type` é `authenticated`, e é isso que faz a Cloudinary recusar o
    ficheiro a quem não trouxer o link assinado. Sem isto, a palavra `raw` e um
    identificador adivinhado chegavam à escritura de alguém.
    """

    resource_type = "raw"
    delivery_type = "authenticated"
    pasta = "echilo/documentos"
    permitted_extensions = EXTENSOES_DOCUMENTO

    def url(self, name: str) -> str:
        """Aponta para a rota do projecto, nunca para a Cloudinary."""
        if not name:
            return ""
        from django.urls import reverse

        return reverse("properties:documento", kwargs={"identificador": name})

    def link_assinado(self, name: str) -> str:
        """Link de entrega com assinatura, que só funciona se não for adivinhado.

        É a diferença entre as duas funções do SDK, e é a diferença entre servir
        o ficheiro e não servir: `private_download_url` monta um endereço de
        `api.cloudinary.com`, que pede chave de API em cabeçalho e devolve 401 a
        um browser; `cloudinary_url` com `sign_url=True` monta o endereço de
        entrega, `res.cloudinary.com`, com a assinatura no caminho. O segundo é o
        que se abre num separador.

        A assinatura é calculada a partir do `public_id` e não inclui a versão, o
        que é o que torna a URL mais curta. Não é um descuido: os `public_id` são
        um `uuid4` e os envios nunca sobrescrevem, por isso "sem versão" quer
        dizer "a única versão que existe".
        """
        return _url_de_entrega(
            name,
            resource_type=self.resource_type,
            type=self.delivery_type,
            sign_url=True,
        )


def storage_documentacao() -> Storage:
    """Backend dos documentos legais, escolhido pelas settings.

    Django só tem um `default` e o §6 precisa de dois: as fotografias são
    públicas e as escrituras não são. Por isso o campo de documento declara o
    storage que este devolve, e não `STORAGES["default"]`.

    É uma função e não uma instância porque um campo serializado guarda a
    instância, e guardar a credencial dentro da migration seria guardar o
    segredo de todos os ambientes que a correrem.
    """
    caminho = getattr(settings, "MEDIA_DOCUMENTACAO_BACKEND", "")
    if caminho.endswith("CloudinaryDocumentStorage"):
        return CloudinaryDocumentStorage()
    return FileSystemStorage()
