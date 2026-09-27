"""Verifica o armazenamento configurado, enviando um ficheiro e apagando-o.

A credencial da Cloudinary pode estar correcta e o envio continuar a falhar: o
`AttributeError` do submódulo `uploader` e a transformação rejeitada
(`Unknown transformation c_limit`) só aparecem quando o serviço responde, e nenhum
teste os apanha porque o `save()` é mockado. São bugs que só nascem em produção e
que custam um imóvel sem fotografia, do lado de quem está a cadastrar.

A verificação é uma ida à rede a sério, com um ficheiro de poucos kilobytes que é
apagado no fim. É a mesma resposta que o projecto dá aos tiles do mapa: um aviso
não é um tile, e um envio que o serviço recusa também não é uma credencial
correcta.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from apps.core.images import solid_colour_jpeg
from apps.core.storage import (
    TRANSFORMACAO_CAPA,
    CloudinaryImageStorage,
    CloudinaryNaoConfigurada,
    cloudinary_configurado,
)

USER_AGENT = "Echilo/1.0 (verificacao de armazenamento)"

#: O ficheiro de prova é uma cor sólida de 640x420: poucos kilobytes, e menor que
#: o tecto de 2000 px, para que a transformação `c_limit` tenha de o deixar como
#: está. Um envio que devolvesse a imagem ampliada provaria que o `c_limit` não
#: foi aplicado, e é isso que se está a verificar.
PROVA_LARGURA = 640
PROVA_ALTURA = 420


@dataclass(frozen=True)
class RelatorioEnvio:
    """O que o serviço entregou, medido no ficheiro que o browser vai buscar."""

    identificador: str
    url: str
    largura: int
    altura: int
    formato: str
    tamanho: int

    def _linha(self) -> str:
        return (
            f"entregue {self.largura}x{self.altura} .{self.formato}, "
            f"{self.tamanho:,} bytes"
        )


class Command(BaseCommand):
    """Confirma que o armazenamento configurado aceita um envio e o entrega."""

    help = "Envia um ficheiro de prova, confirma que é servido e apaga-o."

    def add_arguments(self, parser: object) -> None:
        parser.add_argument(
            "--manter",
            action="store_true",
            help="Não apaga o ficheiro de prova, para o ver no painel da Cloudinary.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not cloudinary_configurado():
            raise CommandError(
                "CLOUDINARY_URL não tem cloud name, api key e api secret. "
                "O formato é cloudinary://<api_key>:<api_secret>@<cloud_name>."
            )

        armazenamento = CloudinaryImageStorage()
        try:
            identificador = armazenamento.save(
                "verificacao.jpg",
                SimpleUploadedFile(
                    "verificacao.jpg",
                    solid_colour_jpeg(colour=(40, 30, 20)),
                    content_type="image/jpeg",
                ),
            )
        except CloudinaryNaoConfigurada as erro:
            raise CommandError(
                f"{erro}\n"
                "A credencial pode estar certa e a transformação errada: o serviço "
                "responde `Unknown transformation` quando a transformação não é "
                "aceite, e isso não é culpa da senha."
            ) from erro

        relatorio = self._relatorio(armazenamento, identificador)
        self.stdout.write(f"identificador: {relatorio.identificador[:8]}...")
        self.stdout.write(f"envio        : {relatorio._linha()}")

        tecto = int(TRANSFORMACAO_CAPA["width"])
        if relatorio.largura > tecto or relatorio.altura > tecto:
            raise CommandError(
                f"A entrega devolveu {relatorio.largura}x{relatorio.altura} e o tecto "
                f"declarado é {tecto} px. A transformação não foi aplicada, e o "
                "cartão do catálogo vai pedir à Cloudinary uma imagem que ninguém "
                "guardou."
            )

        if options["manter"]:
            self.stdout.write(f"URL          : {relatorio.url}")
            self.stdout.write("ficheiro mantido; sem --manter o comando apaga-o")
            return

        armazenamento.delete(relatorio.identificador)
        self.stdout.write(
            self.style.SUCCESS("armazenamento verificado e ficheiro de prova apagado")
        )

    def _relatorio(
        self, armazenamento: CloudinaryImageStorage, identificador: str
    ) -> RelatorioEnvio:
        """Mede o ficheiro que o browser vai buscar, e não o que o SDK devolveu.

        A transformação só conta se chegar a quem pede. Medir a resposta do envio
        provaria que o serviço aceitou o pedido, não que a imagem servida tem a
        forma que o cartão assume.
        """
        url = armazenamento.url(identificador)
        corpo, content_type = self._entregar(url)
        if not content_type.startswith("image/"):
            raise CommandError(
                f"O envio foi aceite mas o URL de entrega respondeu "
                f"content-type {content_type or 'vazio'}. Enviou e não entrega é o "
                "pior dos dois: o cadastro aceita e o cartão fica vazio."
            )
        with Image.open(io.BytesIO(corpo)) as imagem:
            largura, altura = imagem.size
            formato = (imagem.format or "").lower()
        return RelatorioEnvio(
            identificador=identificador,
            url=url,
            largura=largura,
            altura=altura,
            formato=formato,
            tamanho=len(corpo),
        )

    def _entregar(self, url: str) -> tuple[bytes, str]:
        """Pede o URL de entrega e devolve o corpo e o content-type."""
        pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(pedido, timeout=20) as resposta:
                return resposta.read(), resposta.headers.get("Content-Type", "")
        except urllib.error.HTTPError as erro:
            raise CommandError(
                f"O envio foi aceite mas o URL de entrega respondeu HTTP {erro.code}. "
                "Um 403 aqui é quase sempre a transformação recusada e aceite no "
                "envio, ou o que o serviço devolveu não é o que o cartão pede."
            ) from erro
        except urllib.error.URLError as erro:
            raise CommandError(
                f"O envio foi aceite mas o URL de entrega não respondeu: {erro.reason}"
            ) from erro
