"""Views do atendimento: adesão pública, pedidos e gestão interna."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.pagination import pagina_de
from apps.core.permissions import refuse_team_member, require_team_member
from apps.properties.models import Property
from apps.properties.selectors import PropertyQueryService

from .forms import LeadAdminFilterForm, OfferForm, OwnerIntakeForm, VisitRequestForm
from .models import Lead
from .services import IntakeThrottled, create_owner_intake, request_visit, submit_offer


def owner_intake(request: HttpRequest) -> HttpResponse:
    """Etapa 1 da captação: formulário público de adesão de proprietários."""
    form = OwnerIntakeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_owner_intake(
                full_name=str(form.cleaned_data["full_name"]),
                phone=str(form.cleaned_data["phone"]),
                email=str(form.cleaned_data.get("email", "")),
                purpose=str(form.cleaned_data.get("purpose_interest", "")),
                message=str(form.cleaned_data.get("message", "")),
                request=request,
            )
        except IntakeThrottled as exc:
            form.add_error(None, str(exc.messages[0]))
        else:
            return render(request, "concierge/owner_intake_done.html", {"form": OwnerIntakeForm()})
    return render(
        request,
        "concierge/owner_intake.html",
        {
            "form": form,
            "view_title": "Anunciar o seu imóvel",
            "view_subtitle": (
                "A equipa do Echilo valida cada imóvel antes de o publicar. "
                "Preencha os dados e entraremos em contacto."
            ),
        },
    )


@login_required
def visit_request(request: HttpRequest, reference: str) -> HttpResponse:
    """Regista o pedido de visita de um imóvel publicado."""
    refuse_team_member(request)
    prop = get_object_or_404(PropertyQueryService.base_queryset(), reference=reference)
    form = VisitRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request_visit(
                prop=prop,
                user=request.user,
                scheduled_for=form.cleaned_data["scheduled_for"],
                notes=str(form.cleaned_data.get("notes", "")),
            )
        except Exception as exc:  # noqa: BLE001 — o erro de domínio é apresentado ao cliente.
            form.add_error(None, getattr(exc, "messages", [str(exc)])[0])
        else:
            messages.success(
                request,
                _(
                    "Pedido de visita registado. A equipa vai confirmar o dia e a hora "
                    "por telefone ou WhatsApp."
                ),
            )
            return HttpResponseRedirect(reverse("properties:property_detail", args=[prop.reference]))
    return render(
        request,
        "concierge/visit_request.html",
        {
            "form": form,
            "property": prop,
            "view_title": "Pedir visita",
            "view_subtitle": "A morada exacta é revelada depois da confirmação da equipa.",
        },
    )


@login_required
def offer_create(request: HttpRequest, reference: str) -> HttpResponse:
    """Recebe uma proposta formal sobre um imóvel à venda."""
    refuse_team_member(request)
    prop = get_object_or_404(PropertyQueryService.base_queryset(), reference=reference)
    if prop.purpose != Property.Purpose.SALE:
        messages.error(request, _("Este imóvel não está disponível para proposta."))
        return HttpResponseRedirect(reverse("properties:property_detail", args=[prop.reference]))
    form = OfferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit_offer(
                prop=prop,
                user=request.user,
                amount=form.cleaned_data["amount"],
                message=str(form.cleaned_data.get("message", "")),
            )
        except Exception as exc:  # noqa: BLE001 — o erro de domínio é apresentado ao cliente.
            form.add_error(None, getattr(exc, "messages", [str(exc)])[0])
        else:
            messages.success(
                request,
                _("Proposta submetida. Um agente da equipa vai analisá-la e responde em breve."),
            )
            return HttpResponseRedirect(reverse("properties:property_detail", args=[prop.reference]))
    return render(
        request,
        "concierge/offer_form.html",
        {
            "form": form,
            "property": prop,
            "view_title": "Enviar proposta",
            "view_subtitle": "Toda a negociação é conduzida por um agente do Echilo.",
        },
    )


@login_required
def lead_queue(request: HttpRequest) -> HttpResponse:
    """Fila interna de contactos, ordenada por urgência."""
    require_team_member(request)
    form = LeadAdminFilterForm(request.GET or None)
    # O `-id` é o desempate. O `ordering` do modelo é só por `-created_at`, e dois
    # contactos criados no mesmo instante ficavam por ordenar: a página 1 levava
    # um deles e a página 2 podia levar o mesmo. Com a fila a paginar, isso é um
    # contacto que desaparece sem ninguém o apagar.
    leads = Lead.objects.select_related("assigned_to", "property_interest").order_by(
        "-created_at", "-id"
    )
    active_type = ""
    active_status = ""
    if form.is_valid():
        active_type = str(form.cleaned_data.get("lead_type") or "")
        active_status = str(form.cleaned_data.get("status") or "")
        if active_type:
            leads = leads.filter(lead_type=active_type)
        if active_status:
            leads = leads.filter(status=active_status)
    return render(
        request,
        "concierge/lead_queue.html",
        {
            "form": form,
            # A fila era um `[:100]` mudo. Cortava sem dizer que cortou, e a
            # equipa via uma lista acabada a meio sem forma de saber que o
            # formulário de adesão continua a entrar por baixo dela.
            "leads": pagina_de(leads, request),
            "active_type": active_type,
            "active_status": active_status,
            # Mudar de página a perder o filtro é trocar a fila dos proprietários
            # pela fila toda sem que ninguém tenha pedido essa troca.
            "consulta_lista": {
                chave: valor
                for chave, valor in (("lead_type", active_type), ("status", active_status))
                if valor
            },
        },
    )
