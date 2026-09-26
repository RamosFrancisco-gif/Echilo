"""Views de imóveis: página inicial, catálogo público e curadoria interna."""

from __future__ import annotations

from ipaddress import ip_address

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
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
from apps.core.storage import CloudinaryDocumentStorage

from .forms import (
    PropertyCuratorForm,
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
from .reference import ANGOLA_PROVINCES, province_label
from .selectors import PropertyFilters, PropertyQueryService
from .services import add_image, build_map_payload, create_property, resolve_owner

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
        messages.success(
            self.request,
            _("Imóvel %(ref)s registado. Preencha agora a triagem.") % {"ref": prop.reference},
        )
        return HttpResponseRedirect(reverse("properties:curator_detail", args=[prop.reference]))


class CuratorPropertyDetailView(FormView):
    """Ficha interna do imóvel com checklist de triagem e transições de estado."""

    form_class = PropertyCuratorForm
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
            Property.objects.select_related("owner", "curated_by"),
            reference=self.kwargs["reference"],
        )

    def get_form_kwargs(self) -> dict[str, object]:
        """ reencaminha apenas o que o formulário aceita, já com os dados actuais."""
        kwargs = super().get_form_kwargs()
        self.property = self.get_object()
        kwargs["initial"] = {
            "title": self.property.title,
            "type": self.property.type,
            "purpose": self.property.purpose,
            "price": self.property.price,
            "lease_term_months": self.property.lease_term_months,
            "province_ref": self.property.province_ref,
            "municipality": self.property.municipality,
            "latitude": self.property.latitude,
            "longitude": self.property.longitude,
            "bedrooms": self.property.bedrooms,
            "bathrooms": self.property.bathrooms,
        }
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
        context.update(
            {
                "property": prop,
                "submission": submission,
                "municipality_options_url": reverse("properties:municipalities"),
                "missing_items": submission.pending_items() if submission else [],
                "ready_for_review": bool(submission and submission.is_ready_for_review()),
                "missing_documents": prop.missing_verified_documents(),
                "documents": prop.documents.select_related("verified_by").order_by(
                    "document_type"
                ),
                "can_validate_documents": getattr(pedido.user, "can_validate", False),
                "allowed_transitions": [
                    (target, label)
                    for target, label in Property.Status.choices
                    if prop.transition_allowed(target)
                ],
                "status_events": prop.status_events.select_related("actor")[:20],
            }
        )
        return context

    def form_valid(self, form: PropertyCuratorForm) -> HttpResponseRedirect:
        """Actualiza os campos do imóvel sem sair do fluxo de estados auditado."""
        prop = self.get_object()
        updated = []
        for field, value in form.property_fields().items():
            if hasattr(prop, field):
                setattr(prop, field, value)
                updated.append(field)
        prop.full_clean(exclude=["reference", "status", *updated])
        prop.save()
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
            messages.error(request, _("A transição não foi aplicada."))
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


class CuratorPropertyImageView(View):
    """Carrega uma fotografia para o imóvel e devolve à ficha."""

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
        """Valida, dimensiona e guarda a fotografia enviada."""
        prop = get_object_or_404(Property, reference=reference)
        form = self.form_class(request.POST, request.FILES, property=prop)
        target_url = reverse("properties:curator_detail", args=[prop.reference])
        if not form.is_valid():
            messages.error(request, form.errors.as_text())
            return HttpResponseRedirect(f"{target_url}#fotografias")

        add_image(prop=prop, image=PropertyImage(image=form.cleaned_data["image"]))
        messages.success(request, _("Fotografia adicionada."))
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
