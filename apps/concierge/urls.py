"""Rotas de atendimento sob o prefixo `/atendimento/`."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "concierge"

urlpatterns = [
    path("aderir-imovel/", views.owner_intake, name="owner_intake"),
    path("imovel/<slug:reference>/visita/", views.visit_request, name="visit_request"),
    path("imovel/<slug:reference>/proposta/", views.offer_create, name="offer_create"),
    path("contactos/", views.lead_queue, name="lead_queue"),
]
