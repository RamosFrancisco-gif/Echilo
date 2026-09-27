"""Regras sobre imóveis: escritas da equipa e preparação dos dados do mapa."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.money import kwanza_compact

from .models import (
    OwnerProfile,
    Property,
    PropertyDeletion,
    PropertyDocument,
    PropertyImage,
    PropertySubmission,
)
from .reference import ANGOLA_PROVINCES
from .selectors import MAP_MARKER_LIMIT, PropertyFilters, PropertyQueryService
from .validators import MAX_FOTOS, remaining_photo_slots

User = get_user_model()

REFERENCE_PREFIX = "ECH"

PROVINCE_NAMES = dict(ANGOLA_PROVINCES)


def build_map_payload(
    filters: PropertyFilters, *, limit: int = MAP_MARKER_LIMIT
) -> dict[str, object]:
    """Prepara os pinos que o mapa desenha e a caixa onde a vista abre.

    Sai tudo já formatado. O browser não sabe escrever Kwanza nem montar a URL
    de um imóvel: se um dia o pino disser `85000000` e o cartão disser `85 M Kz`,
    foi porque alguém formatou o preço só de um lado.
    """
    marked, total = PropertyQueryService.map_markers(filters, limit=limit)
    items = [
        {
            "id": prop.id,
            "titulo": prop.title,
            "preco": kwanza_compact(prop.price),
            "por_mes": prop.purpose == Property.Purpose.RENT,
            "local": _local_label(prop),
            "url": reverse("properties:property_detail", kwargs={"reference": prop.reference}),
            "lat": float(prop.latitude),
            "lon": float(prop.longitude),
        }
        for prop in marked
    ]
    return {
        "items": items,
        "total": total,
        "mostrados": len(items),
        # Um mapa com pinos a menos do que o número anunciado faz o utilizador
        # procurar um imóvel que "não está lá". Melhor dizer que ficou de fora.
        "incompletos": total > len(items),
        "caixa": _caixa(items),
    }


def _local_label(prop: Property) -> str:
    """Nome curto do sítio onde está o imóvel, para o rótulo do pino.

    Ojelista de mais para menos: quem procura em Angola diz o bairro antes da
    província. O pino precisa de duas palavras, não de três.
    """
    return (
        prop.locality.strip()
        or prop.municipality.strip()
        or PROVINCE_NAMES.get(prop.province_ref, "")
    )


def _caixa(items: list[dict[str, object]]) -> list[list[float]] | None:
    """Caixa que contém todos os pinos, para a vista abrir enquadrada neles.

    Um mapa de Angola inteiro não é um mapa: a 11 de zoom o país inteiro cabe
    no ecrã e nenhum imóvel se distingue do resto. Abrir sobre o catálogo é a
    diferença entre uma imagem de fundo e uma ferramenta.
    """
    if not items:
        return None
    lats = [item["lat"] for item in items]
    lons = [item["lon"] for item in items]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def next_reference(*, province_code: str) -> str:
    """Gera uma referência legível e única com o formato ECH-XX-0000."""
    tail = Property.objects.count() + 1
    while True:
        candidate = f"{REFERENCE_PREFIX}-{province_code[:2].upper()}-{tail:04d}"
        if not Property.objects.filter(reference=candidate).exists():
            return candidate
        tail += 1


@transaction.atomic
def resolve_owner(
    *,
    curator: User,
    full_name: str,
    phone: str,
    id_document_number: str,
) -> OwnerProfile:
    """Reutiliza o proprietário já validado ou cria a ficha a partir da captação."""
    existing = (
        OwnerProfile.objects.filter(full_name=full_name, phone=phone)
        .order_by("created_at")
        .first()
    )
    if existing is not None:
        return existing
    owner = OwnerProfile(
        full_name=full_name,
        phone=phone,
        id_document_type="BI",
        id_document_number=id_document_number or "PENDENTE",
        created_by=curator,
    )
    owner.save()
    return owner


@transaction.atomic
def create_property(
    *,
    curator: User,
    owner: OwnerProfile,
    submission_source: str,
    **fields: object,
) -> Property:
    """Regista um imóvel já curado e abre o dossiê de triagem (§2.1 etapa 3)."""
    province_ref = str(fields.get("province_ref") or "LUANDA")
    prop = Property(
        reference=next_reference(province_code=province_ref),
        owner=owner,
        curated_by=curator,
        **fields,  # type: ignore[arg-type]
    )
    prop.full_clean(exclude=["lease_term_months"] if prop.purpose != Property.Purpose.RENT else None)
    prop.save()
    PropertySubmission.objects.create(
        property=prop,
        source=submission_source,
        owner_name_declared=owner.full_name,
        owner_phone_declared=owner.phone,
        triaged_by=curator,
        triaged_at=timezone.now(),
    )
    return prop


@transaction.atomic
def apply_quick_edit(prop: Property, fields: dict[str, object]) -> Property:
    """Aplica a edição rápida da ficha interna e data o que mudou de lugar.

    É o serviço, e não a view, que decide quando a posição deixa de estar
    verificada. `location_verified_at` é quando a equipa confirmou o ponto
    (§2.3), e confirmar é o que o formulário faz: se as coordenadas mudam, a
    confirmação antiga é de outro ponto e não pode continuar a datar o novo.

    O pin é a única coisa que a equipa confirma por inspecção. Os restantes campos
    mudam sem que isso diga nada sobre a localização, que é o motivo de a regra
    olhar só para o par.
    """
    moved = any(
        getattr(prop, campo) != fields.get(campo) for campo in ("latitude", "longitude")
    )
    for campo, valor in fields.items():
        if hasattr(prop, campo):
            setattr(prop, campo, valor)
    if moved:
        prop.location_verified_at = timezone.now()
    prop.full_clean(exclude=["reference", "status", *fields])
    prop.save()
    return prop


@transaction.atomic
def confirm_submission(
    submission: PropertySubmission,
    *,
    curator: User,
    **confirmations: bool,
) -> PropertySubmission:
    """Actualiza o checklist de triagem e data a confirmação."""
    for field, value in confirmations.items():
        setattr(submission, field, bool(value))
    submission.triaged_by = curator
    submission.triaged_at = timezone.now()
    submission.save()
    return submission


def add_images(*, prop: Property, images: list[object]) -> tuple[list[PropertyImage], int]:
    """Grava um lote de fotografias e diz quantas ficaram de fora.

    O tecto é conferido aqui e não só na vista. A vista lê a contagem, monta o
    formulário e grava; entre a leitura e a gravação cabe outro pedido, e dois
    carregamentos a sério em simultâneo acabam com dezasseis. Trancar a linha do
    imóvel serializa as duas gravações: a segunda vê o tecto já cheio e diz que
    não coube, em vez de escrever por cima.

    Devolve também o que ficou de fora. Uma função que devolvesse só as
    guardadas obrigaria a view a subtrair, e a subtrair com as duas contagens
    a discordar é exactamente o "guardadas" que não é verdade.

    A ordem de escolha é a ordem que a pessoa leu no ecrã quando fotografou, e
    reorganizar isso depois é trabalho a mais para uma coisa que se resolve aqui.
    """
    if not images:
        return [], 0

    guardadas: list[PropertyImage] = []
    with transaction.atomic():
        # O trinco vai no imóvel e não nas fotografias: é a linha do imóvel que
        # decide o tecto. Trancar as fotografias não faria nada, porque dois
        # lotes em paralelo ainda não têm linhas para trancar.
        Property.objects.select_for_update().only("pk").get(pk=prop.pk)

        livres = remaining_photo_slots(prop.images.count())
        if not livres:
            return [], len(images)
        aceites = images[:livres]
        # A numeração é calculada uma vez, a partir do que já lá está, e não a
        # cada fotografia. Fazer a conta fotografia a fotografia e reescrever o
        # que o lote já decidiu só funcionava porque a base de dados é
        # sequencial, e deixou de ser verdade quando o MySQL entrou no meio.
        primeira_livre = MAX_FOTOS - livres

        for ordem, ficheiro in enumerate(aceites):
            fotografia = PropertyImage(
                image=ficheiro,
                sort_order=primeira_livre + ordem,
            )
            fotografia.property = prop
            fotografia.save()
            guardadas.append(fotografia)
    return guardadas, len(images) - len(aceites)


def remove_image(*, prop: Property, image: PropertyImage) -> None:
    """Apaga uma fotografia e fecha a numeração, para a capa não ficar um buraco.

    A ordem é reescrita porque `sort_order` é o que decide a capa, e `add_images()`
    numera a partir de `count()`. Apagar a capa é o caso visível: sem renumerar,
    o catálogo promove a segunda fotografia e ninguém é avisado. Apagar do meio é
    o caso silencioso e o pior: o buraco não se vê, mas a próxima fotografia
    carregada recebe um `sort_order` já ocupado e a capa acaba por ser a imagem
    errada. Por isso a renumeração não é "se for a capa".

    O ficheiro vai para o lixo com a linha. `Model.delete()` apaga a linha e
    deixa o ficheiro no storage — o Django não toca em armazenamento — e uma
    fotografia órfã na Cloudinary continua lá a ser paga todos os meses por
    alguém que já a apagou. O ficheiro vai primeiro, para que uma falha aqui
    deixe a linha a apontar para uma fotografia que ainda existe, e não o
    contrário.
    """
    with transaction.atomic():
        image.image.delete(save=False)
        image.delete()
        for posicao, restante in enumerate(prop.images.order_by("sort_order", "id")):
            if restante.sort_order != posicao:
                restante.sort_order = posicao
                restante.save(update_fields=["sort_order"])


def verify_document(
    document: PropertyDocument,
    *,
    agent: User,
    reason: str = "",
) -> PropertyDocument:
    """Marca o documento como verificado ou recusado, com registo de auditoria."""
    if document.status == PropertyDocument.Status.REJECTED and not reason.strip():
        raise ValidationError("Indique o motivo da recusa do documento.")
    document.status = (
        PropertyDocument.Status.REJECTED
        if reason.strip()
        else PropertyDocument.Status.VERIFIED
    )
    document.rejection_reason = reason.strip()
    document.verified_by = agent
    document.verified_at = timezone.now()
    document.save()
    return document


def publish_property(*, prop: Property, agent: User, reason: str) -> Property:
    """Publica o imóvel depois de confirmar triagem completa e documentação legal."""
    submission = getattr(prop, "submission", None)
    if submission is None or not submission.is_ready_for_review():
        raise ValidationError("A triagem ainda não está completa para publicação.")
    prop.full_clean()
    prop.transition_to(Property.Status.PUBLISHED, actor=agent, reason=reason)
    return prop


def normalise_price(raw: object) -> Decimal:
    """Converte texto ou número em Decimal, recusando valores não positivos."""
    try:
        value = Decimal(str(raw).replace(" ", "").replace(".", "").replace(",", "."))
    except (ArithmeticError, ValueError) as exc:
        raise ValidationError("Indique um preço válido em Kwanza.") from exc
    if value <= 0:
        raise ValidationError("O preço tem de ser maior do que zero.")
    return value.quantize(Decimal("0.01"))


def motivos_para_recusar_apagar(*, prop: Property, actor: User) -> list[str]:
    """Diz porque é que este imóvel não pode ser apagado por esta pessoa.

    A regra é do produto, não do ficheiro, e é uma regra de estado e não de
    papel: apaga-se o imóvel que ainda não foi transacionado. Um imóvel com
    proposta aceite já tem comprador em papel, e apagá-lo é apagar a prova de que
    a venda aconteceu — o mesmo tipo de erro que é apagar a ficha depois de a
    escritura ter sido lida.

    O arquivado é o caso especial, e o único que o administrador desempata.
    Arquivar é a forma correcta de tirar um imóvel do catálogo (§2.8), e por isso
    o estado existe: quem chega ao apagamento a partir de um imóvel arquivado
    está a contornar o caminho que o produto desenhou para ele, e essa é uma
    decisão de chefia. O agente apaga o que está a ser mal curado; o
    administrador apaga também o que já estava arquivado.

    A lista vem por extenso e não por código, porque a página escreve cada um dos
    motivos e um `False` não diz à equipa porque é que o botão desapareceu.
    """
    # `concierge` importa `properties` no topo do módulo, e o inverso seria um
    # ciclo de importações. A dependência é de uma linha e resolvida à chamada.
    from apps.concierge.models import Offer, VisitRequest

    motivos: list[str] = []
    if prop.status == Property.Status.ARCHIVED and actor.role != User.Role.ADMIN:
        motivos.append("Um imóvel arquivado só o administrador o pode apagar.")
    if prop.offers.filter(status=Offer.Status.ACCEPTED).exists():
        motivos.append("Há proposta aceite: o imóvel está vendido ou arrendado.")
    if prop.visit_requests.filter(
        status__in=[VisitRequest.Status.CONFIRMED, VisitRequest.Status.COMPLETED]
    ).exists():
        motivos.append("Há visita confirmada ou realizada.")
    return motivos


def delete_property(*, prop: Property, actor: User, reason: str) -> PropertyDeletion:
    """Apaga o imóvel e os seus ficheiros, deixando escrito o que se apagou.

    O ficheiro vai antes da linha, como em `remove_image()`: `Model.delete()`
    deixa o ficheiro no storage, e uma fotografia órfã continua a ser paga todos
    os meses por alguém que já não existe. A ordem ao contrário daria o contrário
    do que é seguro — uma linha a apontar para um ficheiro que já não está.

    O `PropertyDeletion` é escrito dentro da transacção, e não antes dela: um
    registo que sobrevive a um apagamento que falhou é pior do que nenhum, porque
    afirma que o imóvel foi apagado e ele está lá. E sobrevive ao próprio
    apagamento porque não tem chave estrangeira para o imóvel — é a única linha
    escrita sobre ele que fica de pé.

    Os acessos a documentos sobrevivem com o retrato copiado pelo `save()` do
    modelo (§6): o ficheiro da escritura vai, a prova de quem o leu fica.
    """
    motivos = motivos_para_recusar_apagar(prop=prop, actor=actor)
    if motivos:
        raise ValidationError(" ".join(motivos))
    if not reason.strip():
        raise ValidationError("Indique o motivo do apagamento.")

    with transaction.atomic():
        registo = PropertyDeletion.objects.create(
            reference=prop.reference,
            title=prop.title,
            status_at_deletion=prop.status,
            actor=actor,
            reason=reason.strip(),
        )
        for imagem in prop.images.all():
            imagem.image.delete(save=False)
        for documento in prop.documents.all():
            documento.file.delete(save=False)
        prop.delete()
    return registo
