"""Modelos do atendimento humano: leads, visitas, ofertas e conversas."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.validators import validate_angolan_phone
from apps.properties.models import Property


class Lead(models.Model):
    """Contacto de captação, tanto de proprietários como de clientes."""

    class Type(models.TextChoices):
        """Origem do contacto e o que a equipa deve fazer a seguir."""

        OWNER_INTAKE = "OWNER_INTAKE", "Captação de proprietário"
        CLIENT_ENQUIRY = "CLIENT_ENQUIRY", "Consulta de cliente"

    class Status(models.TextChoices):
        """Estados do lead numa máquina de estados ascendente."""

        NEW = "NEW", "Novo"
        CONTACTED = "CONTACTED", "Contactado"
        QUALIFIED = "QUALIFIED", "Qualificado"
        CONVERTED = "CONVERTED", "Convertido"
        DISQUALIFIED = "DISQUALIFIED", "Descartado"

    ALLOWED_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        Status.NEW: {Status.CONTACTED, Status.DISQUALIFIED},
        Status.CONTACTED: {Status.QUALIFIED, Status.DISQUALIFIED},
        Status.QUALIFIED: {Status.CONVERTED, Status.DISQUALIFIED},
        Status.CONVERTED: set(),
        Status.DISQUALIFIED: {Status.NEW},
    }

    full_name = models.CharField("nome", max_length=150)
    phone = models.CharField(
        "telefone",
        max_length=20,
        validators=[validate_angolan_phone],
    )
    email = models.EmailField("e-mail", blank=True)
    lead_type = models.CharField("tipo", max_length=16, choices=Type.choices, db_index=True)
    status = models.CharField(
        "estado",
        max_length=14,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    purpose_interest = models.CharField(
        "interesse",
        max_length=20,
        choices=[("", "Não indicado")]
        + list(Property.Purpose.choices)
        + [(Property.Type.LAND, "Terreno")],
        blank=True,
    )
    message = models.TextField("mensagem", blank=True)
    property_interest = models.ForeignKey(
        Property,
        verbose_name="imóvel de interesse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="atribuído a",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="leads_assigned",
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado em", auto_now=True)

    class Meta:
        verbose_name = "contacto"
        verbose_name_plural = "contactos"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [models.Index(fields=["lead_type", "status"], name="lead_type_status_idx")]

    def __str__(self) -> str:
        return f"{self.full_name} — {self.get_lead_type_display()}"

    def transition_to(self, target: str) -> None:
        """Muda o estado validando a transição permitida."""
        if target not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValidationError(f"Não é possível passar de {self.status} para {target}.")
        self.status = target
        self.save(update_fields=["status", "updated_at"])


class VisitRequest(models.Model):
    """Pedido de visita ao imóvel, sempre confirmado por um humano (§2.10)."""

    class Status(models.TextChoices):
        """Estados do agendamento."""

        PENDING = "PENDING", "Por confirmar"
        CONFIRMED = "CONFIRMED", "Confirmada"
        DECLINED = "DECLINED", "Recusada"
        COMPLETED = "COMPLETED", "Realizada"
        NO_SHOW = "NO_SHOW", "Não compareceu"

    # `DECLINED` volta a `PENDING` e `CONFIRMED` pode ser recusada: são os dois
    # casos em que o combinado cai depois de escrito, e obrigar a equipa a criar
    # outro pedido para o mesmo cliente deixava a agenda com duas visitas à mesma
    # hora. `COMPLETED` e `NO_SHOW` são finais: o desfecho da visita regista-se uma
    # vez, e quem o registou mal corrigiu-o pelo `/admin`, que é o sítio onde se
    # desfaz o que não tem estado intermédio. Duas maneiras de chegar ao mesmo
    # desfecho seriam duas perguntas à equipa sobre a mesma visita.
    # A visita mais longa que a agenda aceita. Serve de janela ao filtro de
    # sobreposicao: e um tecto do modelo, e a consulta que o usa esta a
    # trinta linhas abaixo deste comentario.
    DURACAO_MAXIMA_MINUTOS = 480

    ALLOWED_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        Status.PENDING: {Status.CONFIRMED, Status.DECLINED},
        Status.CONFIRMED: {Status.COMPLETED, Status.NO_SHOW, Status.DECLINED},
        Status.DECLINED: {Status.PENDING},
        Status.COMPLETED: set(),
        Status.NO_SHOW: set(),
    }

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="visit_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="pedido por",
        on_delete=models.PROTECT,
        related_name="visits_requested",
    )
    lead = models.ForeignKey(
        Lead,
        verbose_name="contacto associado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits",
    )
    scheduled_for = models.DateTimeField("marcada para")
    duration_minutes = models.PositiveSmallIntegerField("duração (minutos)", default=45)
    status = models.CharField(
        "estado",
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    notes = models.CharField("notas", max_length=240, blank=True)
    # A recusa vai com motivo porque o cliente lê a resposta. Um «não» sem
    # explicação obriga a equipa a explicar ao telefone o que a página já podia
    # ter dito, e o motivo escrito é o registo de porquê.
    decline_reason = models.CharField("motivo da recusa", max_length=240, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="tratada por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="visits_handled",
    )
    # `handled_at` é o que separa "ninguém viu" de "a equipa respondeu e a data
    # já passou". A fila de reconfirmação precisa da diferença: um pedido de
    # três dias que já foi tratado não é o mesmo que um pedido de três dias.
    handled_at = models.DateTimeField("tratada em", null=True, blank=True)
    # Os dois campos seguintes são o registo do que o trabalho de fundo já fez.
    # Sem eles, o comando que acorda sozinho corre a cada hora e avisa outra vez
    # sobre a mesma visita, todas as horas, até a visita deixar de estar marcada.
    reminded_at = models.DateTimeField("lembrete enviado em", null=True, blank=True)
    reconfirmation_flagged_at = models.DateTimeField(
        "pedido de reconfirmação em", null=True, blank=True
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "pedido de visita"
        verbose_name_plural = "pedidos de visita"
        ordering: ClassVar[list[str]] = ["-scheduled_for", "-id"]
        indexes: ClassVar[list[models.Index]] = [models.Index(fields=["property", "status"], name="visit_property_status_idx")]

    def __str__(self) -> str:
        return f"{self.property.reference} — {self.scheduled_for:%d/%m/%Y %H:%M}"

    def transition_to(
        self, target: str, *, actor: settings.AUTH_USER_MODEL, reason: str = ""
    ) -> None:
        """Move a visita para outro estado, validando o caminho e o motivo.

        O `reason` é obrigatório a recusar e é o que o cliente vai ler. O `actor`
        é obrigatório e não é opcional: quem decide uma visita é sempre alguém da
        equipa (§2.10), e um `actor=None` gravava um estado sem autor e sem data —
        que é a mesma coisa que `handled_at` a `NULL`, e é por isso que o trabalho
        de fundo trataria a visita como se ninguém a tivesse visto.
        """
        if target not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValidationError(f"Não é possível passar de {self.status} para {target}.")
        if target == self.Status.DECLINED and not reason.strip():
            raise ValidationError("A recusa vai com o motivo.")
        self.status = target
        if target == self.Status.DECLINED:
            self.decline_reason = reason.strip()
        else:
            self.decline_reason = ""
        self.handled_by = actor
        self.handled_at = timezone.now()
        self.save(
            update_fields=["status", "decline_reason", "handled_by", "handled_at"]
        )

    def clean(self) -> None:
        """Recusa sobreposições com outras visitas confirmadas do mesmo imóvel."""
        super().clean()
        if self.status != self.Status.CONFIRMED or not self.scheduled_for:
            return
        inicio = self.scheduled_for
        fim = inicio + timedelta(minutes=self.duration_minutes)
        # A base de dados filtra pela janela mais larga que qualquer visita pode
        # ter, e a comparação dos intervalos é feita aqui a seguir. A janela tem de
        # ser a mais longa possível e não os 45 minutos da visita: 30 + 60 com 40
        # minutos de sobreposição é um conflito real que um olhar para trás de 45
        # minutos não via, porque a outra visita começava antes de a janela abrir.
        candidatas = (
            VisitRequest.objects.filter(
                property=self.property,
                status=VisitRequest.Status.CONFIRMED,
                scheduled_for__lt=fim,
                scheduled_for__gt=inicio - timedelta(minutes=VisitRequest.DURACAO_MAXIMA_MINUTOS),
            )
            .exclude(pk=self.pk)
            .only("id", "scheduled_for", "duration_minutes")
        )
        for outra in candidatas:
            if (
                inicio < outra.scheduled_for + timedelta(minutes=outra.duration_minutes)
                and outra.scheduled_for < fim
            ):
                raise ValidationError("Já existe uma visita confirmada neste período.")


class Offer(models.Model):
    """Proposta formal de preço, sempre tratada por um humano (§2.5).

    Uma proposta sem `parent` é a primeira, do cliente. Com `parent` é uma
    contraproposta da equipa, e é esse campo que distingue as duas coisas — não o
    `submitted_by`, que em ambos os casos é quem pôs a proposta no sistema.
    """

    class Status(models.TextChoices):
        """Estados da proposta."""

        SUBMITTED = "SUBMITTED", "Submetida"
        UNDER_REVIEW = "UNDER_REVIEW", "Em análise"
        COUNTERED = "COUNTERED", "Contraproposta"
        ACCEPTED = "ACCEPTED", "Aceite"
        REJECTED = "REJECTED", "Recusada"
        WITHDRAWN = "WITHDRAWN", "Retirada"

    # A seta do plano — `SUBMITTED → UNDER_REVIEW → COUNTERED → ACCEPTED |
    # REJECTED | WITHDRAWN` — é a lista de estados, não uma cadeia: uma proposta
    # aceite ao preço pedido não passa por `COUNTERED`, porque não há
    # contraproposta nenhuma.
    #
    # `WITHDRAWN` é o cliente a desistir, e a equipa é quem o regista: o cliente
    # avisa por telefone ou WhatsApp, que é o canal da captação (§2.1), e o
    # produto não tem — nem por esta fase — uma página onde o cliente recuse a
    # própria proposta. Tirá-lo das transições deixava um estado que nada
    # escreve, e um estado que nada escreve é um que o `is_active()` esquece e a
    # equipa não consegue alcançar.
    #
    # Os três finais não têm saída porque uma proposta fechada é o fim da
    # negociação, e reabrir uma recusa sem o cliente pedir seria a equipa a
    # continuar a negociar sozinha.
    ALLOWED_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        Status.SUBMITTED: {
            Status.UNDER_REVIEW,
            Status.COUNTERED,
            Status.ACCEPTED,
            Status.REJECTED,
            Status.WITHDRAWN,
        },
        Status.UNDER_REVIEW: {
            Status.COUNTERED,
            Status.ACCEPTED,
            Status.REJECTED,
            Status.WITHDRAWN,
        },
        Status.COUNTERED: {Status.ACCEPTED, Status.REJECTED, Status.WITHDRAWN},
        Status.ACCEPTED: set(),
        Status.REJECTED: set(),
        Status.WITHDRAWN: set(),
    }

    property = models.ForeignKey(
        Property,
        verbose_name="imóvel",
        on_delete=models.CASCADE,
        related_name="offers",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="criada por",
        on_delete=models.PROTECT,
        related_name="offers_submitted",
    )
    # `PROTECT`, não `CASCADE`: apagar a proposta que originou a negociação leva
    # a contraproposta com ela, e a contraproposta é o registo do preço que a
    # equipa propôs. `CASCADE` apagava o histórico da negociação a partir de um
    # clique no `/admin`, e sem registo a disputa de preço não tem prova.
    parent = models.ForeignKey(
        "self",
        verbose_name="contraproposta a",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counters",
    )
    amount = models.DecimalField(
        "valor proposto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField("moeda", max_length=3, default="AOA")
    message = models.TextField("justificação da proposta", blank=True)
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="respondida por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offers_answered",
    )
    response_notes = models.TextField("resposta da equipa", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    responded_at = models.DateTimeField("respondida em", null=True, blank=True)

    class Meta:
        verbose_name = "proposta"
        verbose_name_plural = "propostas"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [models.Index(fields=["property", "status"], name="offer_property_status_idx")]

    def __str__(self) -> str:
        return f"{self.property.reference} — {self.amount} {self.currency}"

    def is_active(self) -> bool:
        """Diz se a negociação continua aberta.

        Lê a máquina em vez de repetir a lista de estados abertos. A lista escrita
        à mão tinha `COUNTERED` de fora, e uma contraproposta que ficasse de fora
        desaparecia do filtro «ainda em aberto» no dia em que a equipa a faz — que
        é o dia em que ela precisa de aparecer. Um estado com transições de saída
        é um estado aberto, e é a máquina que sabe quais são.
        """
        return bool(self.ALLOWED_TRANSITIONS.get(self.status))

    def is_counter(self) -> bool:
        """Diz se esta proposta é uma contraproposta da equipa."""
        return self.parent_id is not None

    def negotiation_with(self) -> settings.AUTH_USER_MODEL:
        """Devolve o cliente com quem a proposta está a ser negociada.

        Numa proposta do cliente é o próprio. Numa contraproposta é quem a
        originou, e não o agente que a escreveu: o `submitted_by` de uma
        contraproposta é o membro da equipa que a pôs no sistema, e avisar esse
        agente de que a contraproposta foi aceite é a equipa a responder a si
        mesma. A distinção está em `parent`, e não em `submitted_by`: nas duas é
        o mesmo campo, e é por isso que ele não se pode ler como «o cliente
        desta proposta».
        """
        if self.parent_id is None:
            return self.submitted_by
        return self.parent.submitted_by

    def transition_to(
        self, target: str, *, actor: settings.AUTH_USER_MODEL, notes: str = ""
    ) -> None:
        """Move a proposta para outro estado, validando o caminho.

        `COUNTERED` não chega por aqui: uma contraproposta é uma proposta nova, e
        quem a cria é `counter_offer()`. A transição directa saltaria o registo do
        valor que a equipa propôs, e o valor é a única coisa que a proposta diz.
        """
        if target not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValidationError(f"Não é possível passar de {self.status} para {target}.")
        self.status = target
        self.responded_by = actor
        if notes.strip():
            self.response_notes = notes.strip()
        self.responded_at = timezone.now()
        self.save(
            update_fields=["status", "responded_by", "response_notes", "responded_at"]
        )


class Conversation(models.Model):
    """Filo de atendimento entre cliente e equipa, partilhado com o assistente."""

    class HandledBy(models.TextChoices):
        """Quem está a responder agora."""

        AI = "AI", "Assistente"
        HUMAN = "HUMAN", "Equipa"

    class Status(models.TextChoices):
        """Estado do atendimento no canal."""

        OPEN = "OPEN", "Aberto"
        ESCALATED = "ESCALATED", "Escalado para a equipa"
        RESOLVED = "RESOLVED", "Resolvido"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="cliente",
        on_delete=models.CASCADE,
        related_name="conversations",
        null=True,
        blank=True,
    )
    lead = models.ForeignKey(
        Lead,
        verbose_name="contacto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    property_interest = models.ForeignKey(
        Property,
        verbose_name="imóvel em discussão",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    handled_by = models.CharField(
        "tratado por",
        max_length=6,
        choices=HandledBy.choices,
        default=HandledBy.AI,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="agente responsável",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="conversations_assigned",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada em", auto_now=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        ordering: ClassVar[list[str]] = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"Conversa #{self.pk} ({self.get_handled_by_display()})"

    def escalate(self, *, agent: object) -> None:
        """Passa o atendimento para a equipa (§2.4 nível 2)."""
        self.status = Conversation.Status.ESCALATED
        self.handled_by = Conversation.HandledBy.HUMAN
        self.assigned_to = agent
        self.save(update_fields=["status", "handled_by", "assigned_to", "updated_at"])

    def resolve(self) -> None:
        """Encerra o atendimento."""
        self.status = Conversation.Status.RESOLVED
        self.handled_by = Conversation.HandledBy.HUMAN
        self.save(update_fields=["status", "handled_by", "updated_at"])


class Message(models.Model):
    """Mensagem individual dentro de uma conversa, da IA, do cliente ou da equipa."""

    class Author(models.TextChoices):
        """Origem da mensagem."""

        CLIENT = "CLIENT", "Cliente"
        ASSISTANT = "ASSISTANT", "Assistente"
        STAFF = "STAFF", "Equipa"

    conversation = models.ForeignKey(
        Conversation,
        verbose_name="conversa",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.CharField("autor", max_length=10, choices=Author.choices)
    body = models.TextField("conteúdo")
    is_escalation_notice = models.BooleanField("aviso de escalação", default=False)
    # O motivo técnico da escalação é da equipa, não do cliente. Escrevê-lo como
    # uma bolha normal punha o diagnóstico dentro da conversa: o cliente lia
    # "Erro técnico no assistente." e a equipa, que devia agir, não o via em
    # lado nenhum. Fica na base, para o admin, e fora do ecrã do cliente.
    is_internal = models.BooleanField("uso interno da equipa", default=False)
    created_at = models.DateTimeField("enviada em", auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering: ClassVar[list[str]] = ["created_at", "id"]

    def __str__(self) -> str:
        return f"[{self.get_author_display()}] {self.body[:40]}"
