"""Views de imóveis: página inicial, catálogo público e curadoria interna."""

from __future__ import annotations

from ipaddress import ip_address

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, QuerySet
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, ListView, View

from apps.core.maps import area_map_config
from apps.core.permissions import require_document_access, require_team_member
from apps.core.ratelimit import client_ip
from apps.core.storage import CloudinaryDocumentStorage, MIME_IMAGEM_ACEITE

from .forms import (
    PropertyCuratorForm,
    PropertyDeleteForm,
    PropertyQuickEditForm,
    PropertyImageUploadForm,
    PropertyTransitionForm,
    PublicSearchForm,
)
from .models import (
    DocumentAccessLog,
    Property,
    PropertyDocument,
    PropertyImage,
    PropertySubmission,
)
from .map_config import curation_map_config
from .reference import ANGOLA_PROVINCES, province_label
from .selectors import PropertyFilters, PropertyQueryService
from .services import (
    add_images,
    apply_quick_edit,
    build_map_payload,
    create_property,
    delete_property,
    motivos_para_recusar_apagar,
    remove_image,
    resolve_owner,
)
from .validators import MAX_FOTOS, MIN_FOTOS, remaining_photo_slots

PAGE_SIZE = 12


def ip_de_auditoria(request: HttpRequest) -> str | None:
    """Endereço de quem pediu o documento, ou `None` quando não é um endereço.

    O `client_ip` serve-se de `REMOTE_ADDR` como recurso e devolve
    "desconhecido" quando não o encontra, porque uma chave de cache tem sempre de
    ser uma string. Uma coluna `GenericIPAddressField` não aceita essa palavra e
    o registo de auditoria deixava de ser gravado — que é a pior forma de falhar
    uma auditoria: calar-se em silêncio em vez de dizer que não conseguiu.
    """
    candidato = client_ip(request)
    try:
        return str(ip_address(candidato))
    except ValueError:
        return None


class HomeView(ListView):
    """Página inicial: proposta de valor, imóveis em destaque e atalho para o assistente."""

    template_name = "index.html"
    context_object_name = "featured"
    paginate_by = 6

    def get_queryset(self) -> list[Property]:
        """Devolve os imóveis publicados em destaque."""
        return list(PropertyQueryService.featured(limit=6))

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Acrescenta contadores reais do catálogo para a página inicial."""
        context = super().get_context_data(**kwargs)
        published = Property.objects.published()
        context.update(
            {
                "purpose_choices": Property.Purpose.choices,
                "province_choices": self.get_province_choices(),
                "total_published": published.count(),
                "rent_count": published.filter(purpose=Property.Purpose.RENT).count(),
                "sale_count": published.filter(purpose=Property.Purpose.SALE).count(),
                "land_count": published.filter(type=Property.Type.LAND).count(),
                "province_count": published.values("province_ref").distinct().count(),
            }
        )
        return context

    def get_province_choices(self) -> list[tuple[str, str]]:
        """Oferece as 21 províncias, e não só as que já têm imóveis publicados.

        A home e o catálogo são a mesma pesquisa em dois sítios: um selector que
        aqui esconde Zaire e no catálogo mostra é uma resposta diferente à mesma
        pergunta, e quem só passa pela home nunca chega a ver o país inteiro.
        Uma província sem imóveis é um resultado vazio, que é honesto; uma
        província que o produto não menciona é uma que não existe para quem lê.
        """
        return [("", "Toda Angola")] + [
            (code, province_label(label)) for code, label in ANGOLA_PROVINCES
        ]


class PropertyListView(ListView):
    """Catálogo público com filtros combináveis e actualização por HTMX."""

    template_name = "properties/property_list.html"
    context_object_name = "properties"
    paginate_by = PAGE_SIZE

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Interpreta a query string uma única vez, para não repetir a consulta."""
        self.search_form = PublicSearchForm(request.GET or None)
        self.filters = PropertyFilters.from_query(request.GET)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> object:
        """Devolve o recorte paginado a partir dos filtros já interpretados."""
        return PropertyQueryService.search(self.filters)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Fornece o formulário, os filtros activos, as opções de área e o mapa."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "search_form": self.search_form,
                "filters": self.filters,
                "active_filters": self.filters.as_query_params(),
                "chip_all": self.filters.chip_query(purpose="", type=""),
                "chip_rent": self.filters.chip_query(purpose="RENT", type=""),
                "chip_sale": self.filters.chip_query(purpose="SALE", type=""),
                "chip_land": self.filters.chip_query(purpose="", type="LAND"),
                "clear_area_query": self.filters.chip_query(
                    center_lat="", center_lon="", radius_m=""
                ),
                "municipality_suggestions": self.municipality_suggestions(),
                "municipality_options_url": reverse("properties:municipalities"),
                "purpose_choices": Property.Purpose.choices,
                "type_choices": Property.Type.choices,
                "map_config": area_map_config(),
                "map_payload": build_map_payload(self.filters),
            }
        )
        return context

    def municipality_suggestions(self) -> list[str]:
        """Sugere os municípios da província escolhida, mais os que têm imóveis publicados."""
        return PropertyQueryService.municipality_suggestions(self.filters.province)

    def render_to_response(self, context: dict[str, object], **response_kwargs: object) -> HttpResponse:
        """Devolve só os resultados quando o pedido vem do HTMX."""
        if self.request.headers.get("HX-Request"):
            return render(
                self.request, "partials/property_results.html", context, **response_kwargs
            )
        return super().render_to_response(context, **response_kwargs)


class PropertyDetailView(DetailView):
    """Ficha pública de um imóvel publicado, com pedido de visita e oferta."""

    model = Property
    template_name = "properties/property_detail.html"
    context_object_name = "property"
    slug_field = "reference"
    slug_url_kwarg = "reference"

    def get_queryset(self) -> object:
        """Serve apenas imóveis publicados."""
        return PropertyQueryService.base_queryset()

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Acrescenta capa, galeria e imóvel similar da mesma tipologia."""
        context = super().get_context_data(**kwargs)
        prop: Property = context["property"]
        images = list(prop.images.all())
        context.update(
            {
                "cover_image": images[0] if images else None,
                "gallery": images,
                "similar": PropertyQueryService.base_queryset()
                .filter(type=prop.type, purpose=prop.purpose)
                .exclude(pk=prop.pk)[:3],
            }
        )
        return context


class CuratorDashboardView(ListView):
    """Índice dos imóveis para a equipa, agrupado pelo estado em que estão.

    Existia um endereço para registar um imóvel e nenhum para os ver. Quem
    entrava na curadoria recebia um formulário em branco e a única forma de
    chegar a uma ficha era saber a referência de cor — o que obriga a equipa a
    manter a lista num papel.
    """

    template_name = "properties/curator_dashboard.html"
    context_object_name = "properties"
    paginate_by = 24

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Bloqueia o acesso de clientes e de visitantes sem sessão iniciada."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        return super().dispatch(request, *args, **kwargs)

    def estado_activo(self) -> str:
        """Devolve o estado pedido, e vazio quando o pedido não é um estado.

        Fica num método só porque o estado é lido em dois sítios e a resposta tem
        de ser a mesma nos dois. Se o queryset aceitasse um valor que a página
        não marca como escolhido, um estado escrito à mão deixava o filtro sem
        nenhum "está aqui" marcado e a lista toda em baixo.
        """
        pedido = (self.request.GET.get("estado") or "").strip().upper()
        return pedido if pedido in Property.Status.values else ""

    def get_queryset(self) -> QuerySet[Property]:
        """Filtra pelo estado pedido, e mostra o raso quando não há filtro."""
        base = Property.objects.select_related("curated_by").order_by("-created_at")
        estado = self.estado_activo()
        if estado:
            base = base.filter(status=estado)
        return base

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Acrescenta a contagem por estado, para o filtro não ser às cegas.

        A contagem vem como lista de dicionários e não como dicionário porque o
        template não consegue fazer `contagens[valor]`. Escrever o número no
        HTML dava um filtro que anuncia 0 em todos os estados.
        """
        context = super().get_context_data(**kwargs)
        contagens = {
            row["status"]: row["total"]
            for row in Property.objects.values("status").annotate(total=Count("id"))
        }
        context["estados"] = [
            {"valor": valor, "nome": nome, "total": contagens.get(valor, 0)}
            for valor, nome in Property.Status.choices
        ]
        context["estado_activo"] = self.estado_activo()
        context["total_equipa"] = Property.objects.count()
        return context


class CuratorPropertyCreateView(FormView):
    """Cadastro gerido: só a equipa da Echilo cria imóveis."""

    form_class = PropertyCuratorForm
    template_name = "properties/curator_form.html"

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Bloqueia o acesso de clientes e de visitantes sem sessão iniciada."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Descreve a etapa de cadastro no cabeçalho do formulário."""
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Registar imóvel curado"
        context["municipality_options_url"] = reverse("properties:municipalities")
        context["map_config"] = curation_map_config()
        context["form_intro"] = (
            "Só a equipa do Echilo regista imóveis. Confirme os dados já verificados "
            "durante a triagem antes de guardar."
        )
        return context

    def form_valid(self, form: PropertyCuratorForm) -> HttpResponseRedirect:
        """Persiste o imóvel já ligado ao proprietário e ao curador."""
        owner = resolve_owner(
            curator=self.request.user,
            **form.owner_fields(),
        )
        prop = create_property(
            curator=self.request.user,
            owner=owner,
            submission_source=str(
                form.cleaned_data.get("submission_source") or PropertySubmission.Source.WEB_FORM
            ),
            **form.property_fields(),
        )
        # As fotografias entram depois do imóvel existir, e pelo mesmo serviço que
        # a ficha usa. Não é uma segunda via de carregar: o limite de 15, a ordem
        # e a renumeração estão num sítio só, e um caminho novo para gravar
        # fotografias é um caminho que ninguém lembra de trancar.
        guardadas, fora_do_tecto = add_images(
            prop=prop, images=list(form.cleaned_data.get("images") or [])
        )
        self._diz_o_que_aconteceu_as_fotografias(form, guardadas, fora_do_tecto)

        messages.success(
            self.request,
            _("Imóvel %(ref)s registado. Preencha agora a triagem.") % {"ref": prop.reference},
        )
        destino = reverse("properties:curator_detail", args=[prop.reference])
        return HttpResponseRedirect(f"{destino}#fotografias" if guardadas else destino)

    def _diz_o_que_aconteceu_as_fotografias(
        self,
        form: PropertyCuratorForm,
        guardadas: list[PropertyImage],
        fora_do_tecto: int,
    ) -> None:
        """Conta as fotografias por fotografia, como a ficha faz (§2.1).

        Dizer só "guardadas" obriga a pessoa a procurar a fotografia que não
        gravou, e a conclusão que ela tira é que o ficheiro se perdeu.
        """
        if guardadas:
            messages.success(
                self.request,
                _("%(quantidade)d fotografia(s) adicionada(s).")
                % {"quantidade": len(guardadas)},
            )
        for recusa in getattr(form, "recusas", []):
            messages.warning(self.request, recusa)
        # O formulário conta o que não coube à luz da contagem que viu, e o serviço
        # conta à luz da contagem com o imóvel trancado. Somam-se, como na ficha.
        descartadas = getattr(form, "descartadas", 0) + fora_do_tecto
        if descartadas:
            messages.warning(
                self.request,
                _("%(quantidade)d fotografia(s) não couberam no limite de %(maximo)d.")
                % {"quantidade": descartadas, "maximo": MAX_FOTOS},
            )


class CuratorPropertyDetailView(FormView):
    """Ficha interna do imóvel com checklist de triagem e transições de estado."""

    form_class = PropertyQuickEditForm
    template_name = "properties/curator_detail.html"

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a ficha interna à equipa."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self) -> Property:
        """Carrega o imóvel a Curar a partir da referência na URL."""
        return get_object_or_404(
            # As fotografias entram à mesma: a página mostra-as e conta-as, e sem
            # o prefetch são duas perguntas à mesma relação numa visita só.
            Property.objects.select_related("owner", "curated_by").prefetch_related(
                "images"
            ),
            reference=self.kwargs["reference"],
        )

    def get_form_kwargs(self) -> dict[str, object]:
        """ reencaminha apenas o que o formulário aceita, já com os dados actuais."""
        kwargs = super().get_form_kwargs()
        self.property = self.get_object()
        # O `initial` vai por campo, e não pelo imóvel inteiro: o formulário é um
        # subconjunto, e passá-lo como instância faria o Django propor alterações
        # que esta página não edita. Ler a lista de `initial` do próprio
        # formulário é o que impede a lista e a página de divergirem outra vez.
        initial = {
            name: getattr(self.property, name)
            for name in self.form_class.base_fields
            if hasattr(self.property, name)
        }
        kwargs["initial"] = initial
        return kwargs

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Junta o estado da triagem, a documentação e o histórico de estados."""
        context = super().get_context_data(**kwargs)
        prop = self.get_object()
        # `self.request` e não `kwargs["request"]`: o `kwargs` só traz o pedido
        # quando o `get()` o repassa, e um `get()` que chame
        # `render_to_response(self.get_context_data())` não o repassa. Ficar a
        # ler de `kwargs` dava um `KeyError` só nessa página e em nenhum teste
        # que não a visitasse por esse caminho.
        pedido = self.request
        submission = getattr(prop, "submission", None)
        # Calculado uma vez e reutilizado: a regra consulta ofertas e visitas, e
        # perguntar duas vezes à mesma pergunta são dois pedidos à base de dados
        # para a mesma resposta.
        motivos_sem_apagar = motivos_para_recusar_apagar(prop=prop, actor=pedido.user)
        context.update(
            {
                "property": prop,
                "submission": submission,
        "municipality_options_url": reverse("properties:municipalities"),
        "map_config": curation_map_config(),
                "missing_items": submission.pending_items() if submission else [],
                "ready_for_review": bool(submission and submission.is_ready_for_review()),
                "missing_documents": prop.missing_verified_documents(),
                "documents": prop.documents.select_related("verified_by").order_by(
                    "document_type"
                ),
                "can_validate_documents": getattr(pedido.user, "can_validate", False),
                # O formulário das transições é a única fonte da lista de destinos
                # e da obrigatoriedade da justificação. A ficha tinha duas formas
                # de os oferecer — a lista no contexto e o `required` no HTML —, e
                # duas fontes para a mesma pergunta é uma resposta que diverge
                # conforme se pergunta ao formulário ou à vista.
                "transition_form": PropertyTransitionForm(property=prop),
                "status_events": prop.status_events.select_related("actor")[:20],
                "max_fotos": MAX_FOTOS,
                "min_fotos": MIN_FOTOS,
                "fotos_livres": remaining_photo_slots(prop.images.count()),
                # O `accept` do input e o `accept` do formulário têm de dizer a
                # mesma coisa. Escritos à mão em dois sítios, um deles serve de
                # mais formatos do que o servidor aceita, e a pessoa descobre-o
                # depois de escolher o ficheiro.
                "aceite_imagens": MIME_IMAGEM_ACEITE,
                # Apagar é a acção que não tem volta, e por isso a página diz o que
                # a impede. Uma lista vazia de motivos é o que autoriza o botão; a
                # regra é `motivos_para_recusar_apagar()` e a ficha não decide nada
                # sozinha, senão o botão e a recusa acabavam em respostas diferentes
                # à pergunta "isto pode ser apagado?".
                "pode_apagar": getattr(pedido.user, "can_validate", False)
                and not motivos_sem_apagar,
                "motivos_sem_apagar": motivos_sem_apagar,
                "delete_form": PropertyDeleteForm(property=prop),
            }
        )
        return context

    def form_valid(self, form: PropertyQuickEditForm) -> HttpResponseRedirect:
        """Actualiza os campos do imóvel sem sair do fluxo de estados auditado."""
        prop = apply_quick_edit(self.get_object(), form.property_fields())
        messages.success(self.request, _("Dados do imóvel actualizados."))
        return HttpResponseRedirect(
            reverse("properties:curator_detail", args=[prop.reference])
        )


class CuratorPropertyTransitionView(View):
    """Aplica uma transição de estado pela equipa, com registo de auditoria (§2.8)."""

    form_class = PropertyTransitionForm

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a transição à equipa e só aceita POST."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[kwargs["reference"]])
            )
        return super().dispatch(request, *args, **kwargs)

    def get_property(self) -> Property:
        """Carrega o imóvel a Curar a partir da referência na URL."""
        return get_object_or_404(Property, reference=self.kwargs["reference"])

    def post(self, request: HttpRequest, reference: str) -> HttpResponse:
        """Valida a transição e devolve o utilizador à ficha com o resultado."""
        prop = self.get_property()
        form = self.form_class(request.POST, property=prop)
        target_url = reverse("properties:curator_detail", args=[prop.reference])
        if not form.is_valid():
            messages.error(request, self._motivo(form))
            return HttpResponseRedirect(f"{target_url}#estado")

        try:
            prop.transition_to(
                str(form.cleaned_data["to_status"]),
                actor=request.user,
                reason=str(form.cleaned_data.get("reason", "")),
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return HttpResponseRedirect(f"{target_url}#estado")
        messages.success(request, _("Estado do imóvel actualizado."))
        return HttpResponseRedirect(target_url)

    @staticmethod
    def _motivo(form: PropertyTransitionForm) -> str:
        """Escreve o que falta, e não que não foi aplicada.

        A ficha tem dois formulários e este é o único que falha por uma razão que
        a pessoa não adivinha: a justificação que o estado escolhido exige. A
        recusa genérica deixava a equipa a ver um erro sem resposta, e o erro do
        formulário — que é a resposta — ia fora com o formulário.

        A mensagem é o texto do erro tal e qual, e não o erro com o rótulo do
        campo à frente: "Justificação: Este estado exige uma justificação." lê-se
        como três vezes a mesma palavra. E o texto do campo já é uma frase
        inteira que diz o que fazer, que é o que a pessoa precisa de ler.

        A transição recusada também não entra na mensagem: ela não foi aplicada, e
        dizer qual era era dar a ideia de que o histórico ficou com um registo que
        não existe.
        """
        motivos = [texto for textos in form.errors.values() for texto in textos]
        if motivos:
            return " ".join(motivos)
        return _("A transição não foi aplicada.")


class CuratorPropertyDeleteView(View):
    """Apaga o imóvel a pedido da equipa, e deixa escrito o que se apagou.

    Não é a mesma coisa que arquivar, e a página da ficha diz isso antes de
    oferecer o botão. Arquivar tira o imóvel do catálogo e mantém o histórico
    (§2.8); apagar remove a ficha, as fotografias e a documentação, e é o que
    serve para o imóvel que não devia ter entrado. Por isso o botão aparece
    depois de arquivar, e não ao lado do arquivo: a ordem das acções na página é
    a ordem em que se deve tentar.

    A guarda é a de `can_validate`, a mesma que abre a documentação legal (§6): é
    a pergunta "quem pode tirar uma ficha pública de um imóvel verificado", e
    escrevê-la aqui daria uma segunda resposta ao lado da de `permissions.py`.
    Quem decide o estado do imóvel é depois `motivos_para_recusar_apagar()`, que
    é regra de produto e não de papel.
    """

    form_class = PropertyDeleteForm

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a equipa, restringe o perfil e só aceita POST."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_document_access(request)
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[kwargs["reference"]])
            )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request: HttpRequest, reference: str) -> HttpResponse:
        """Valida a confirmação, apaga e devolve ao dashboard com o resultado."""
        prop = get_object_or_404(Property, reference=reference)
        form = self.form_class(request.POST, property=prop)
        destino = reverse("properties:curator_dashboard")
        if not form.is_valid():
            motivos = " ".join(
                texto for textos in form.errors.values() for texto in textos
            )
            messages.error(
                request,
                motivos or "O imóvel não foi apagado.",
            )
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[prop.reference])
            )

        try:
            registo = delete_property(
                prop=prop,
                actor=request.user,
                reason=str(form.cleaned_data["reason"]),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[prop.reference])
            )

        messages.success(
            request,
            f"Imóvel {registo.reference} apagado. "
            "O registo do apagamento ficou guardado com o motivo.",
        )
        return HttpResponseRedirect(destino)


class CuratorPropertyImageView(View):
    """Carrega fotografias para o imóvel e devolve à ficha com o resultado.

    O resultado é escrito por fotografia, e não como um "guardado" genérico. A
    equipa escolhe doze e uma é um PDF: dizer apenas "guardadas" faz a pessoa
    procurar twelfth fotografia no ecrã e não a encontra, e a conclusão que
    tira é que o ficheiro se perdeu.
    """

    form_class = PropertyImageUploadForm

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe o carregamento à equipa e só aceita POST."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[kwargs["reference"]])
            )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request: HttpRequest, reference: str) -> HttpResponse:
        """Valida, dimensiona e guarda o lote, dizendo o que entrou e o que não."""
        prop = get_object_or_404(Property, reference=reference)
        target_url = reverse("properties:curator_detail", args=[prop.reference])

        if prop.images.count() >= MAX_FOTOS:
            messages.error(
                request,
                _("Este imóvel já tem o máximo de %(maximo)d fotografias.") % {"maximo": MAX_FOTOS},
            )
            return HttpResponseRedirect(f"{target_url}#fotografias")

        form = self.form_class(
            request.POST,
            request.FILES,
            property=prop,
            livres=remaining_photo_slots(prop.images.count()),
        )
        if not form.is_valid():
            messages.error(request, form.errors.as_text())
            return HttpResponseRedirect(f"{target_url}#fotografias")

        guardadas, fora_do_tecto = add_images(
            prop=prop, images=list(form.cleaned_data["images"])
        )

        if guardadas:
            messages.success(
                request,
                _("%(quantidade)d fotografia(s) adicionada(s).")
                % {"quantidade": len(guardadas)},
            )
        for recusa in getattr(form, "recusas", []):
            messages.warning(request, recusa)
        # O formulário conta o que não coube à luz da contagem que viu, e o
        # serviço conta o que não coube à luz da contagem com o imóvel trancado.
        # São o mesmo número quase sempre, e somam-se quando dois carregamentos
        # chegam ao mesmo tempo. Mostrar só um dos dois é mentir sobre o outro.
        descartadas = getattr(form, "descartadas", 0) + fora_do_tecto
        if descartadas:
            messages.warning(
                request,
                _(
                    "%(quantidade)d fotografia(s) não couberam: o limite é %(maximo)d "
                    "por imóvel. Apague uma para dar lugar a outra."
                )
                % {"quantidade": descartadas, "maximo": MAX_FOTOS},
            )
        return HttpResponseRedirect(f"{target_url}#fotografias")


class CuratorPropertyImageDeleteView(View):
    """Apaga uma fotografia e devolve à ficha.

    Existe porque o tecto de quinze fotografias é um muro: sem isto, uma
    fotografia errada ocupa um lugar que ninguém pode recuperar, e a equipa fica
    a trocar as boas todas para conseguir meter as boas. É o que torna o limite
    defensável — um tecto que se pode recuperar é um aviso, e um que não se pode
    é um erro.
    """

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe o apagamento à equipa e só aceita POST."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_team_member(request)
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[kwargs["reference"]])
            )
        return super().dispatch(request, *args, **kwargs)

    def post(
        self, request: HttpRequest, reference: str, image_id: int
    ) -> HttpResponse:
        """Apaga a fotografia escolhida e devolve a ficha ao mesmo sítio."""
        prop = get_object_or_404(Property, reference=reference)
        target_url = reverse("properties:curator_detail", args=[prop.reference])

        # A fotografia tem de ser deste imóvel. Sem o filtro, um `image_id` de
        # outro imóvel apagava a fotografia de um imóvel que ninguém está a ver.
        image = get_object_or_404(
            PropertyImage, pk=image_id, property=prop
        )
        era_a_capa = image.sort_order == 0
        remove_image(prop=prop, image=image)
        messages.success(
            request,
            _("Fotografia apagada.")
            + (
                _(" A capa passou a ser a fotografia seguinte.")
                if era_a_capa
                else ""
            ),
        )
        return HttpResponseRedirect(f"{target_url}#fotografias")


class MunicipalityOptionsView(View):
    """As sugestões de município de uma província, para o campo do filtro.

    Existe para que a página do catálogo não leve os cento e setenta e um
    nomes no HTML à espera de alguém escrever numa caixa. O que o campo oferece
    tem de ser só o da província escolhida — escrever no código-fonte todas as
    províncias é o mesmo que oferecer todas, e a promessa que o filtro faz é
    outra.

    Devolve a mesma lista que a vista do catálogo escreve no `<datalist>`, porque
    as duas chamam o mesmo selector. Divergir aqui era ter duas verdades sobre
    o que o campo oferece, e a divergência aparecia na segunda mudança de
    província.
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        provincia = str(request.GET.get("province", ""))
        return JsonResponse(
            {"sugestoes": PropertyQueryService.municipality_suggestions(provincia)}
        )


class PropertyDocumentView(View):
    """Entrega um documento legal a quem tem permissão, e a mais ninguém (§6).

    A Cloudinary guarda o ficheiro como `authenticated`, e por isso não o serve
    a quem não trouxer um link assinado. Esta vista é quem emite esse link: exige
    sessão activa, exige perfil `AGENT` ou `ADMIN`, regista quem abriu e em que
    momento, e só então redirecciona.

    O redireccionamento é de propósito. Servir os bytes aqui obrigaria a cada
    função da Vercel a puxar o ficheiro inteiro, a gastá-lo do limite de 10 s e
    da memória, e a manter o ficheiro a passar por um servidor que não precisa
    dele. A Cloudinary entrega-o directamente a quem provou que tem direito.

    O `identificador` é o `public_id` que a storage gerou: um `uuid4` em hexadecimal.
    Não é sequencial e não é adivinhável, que é o que torna o link curto de vida
    e ainda assim difícil de usar por outra pessoa.
    """

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a leitura à equipa autorizada e só aceita GET."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para aceder à curadoria.")
        require_document_access(request)
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest, identificador: str) -> HttpResponse:
        """Registra o acesso e devolve o link temporário da Cloudinary."""
        document = get_object_or_404(
            PropertyDocument.objects.select_related("property"),
            file=identificador,
        )
        if not document.file:
            raise Http404("Este documento ainda não tem ficheiro.")

        DocumentAccessLog.objects.create(
            document=document,
            actor=request.user,
            ip_address=ip_de_auditoria(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
        )

        link = CloudinaryDocumentStorage().link_assinado(document.file.name)
        if not link:
            messages.error(request, _("O ficheiro não está disponível de momento."))
            return HttpResponseRedirect(
                reverse("properties:curator_detail", args=[document.property.reference])
            )

        resposta = HttpResponseRedirect(link)
        resposta["Cache-Control"] = "no-store"
        resposta["Referrer-Policy"] = "no-referrer"
        return resposta


def property_not_published(request: HttpRequest, reference: str) -> HttpResponse:
    """Mensagem de imóvel fora do ar para referências que deixaram de estar publicadas."""
    return render(
        request,
        "properties/unavailable.html",
        {"reference": reference},
        status=404,
    )
