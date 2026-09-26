"""Rotas de imóveis: catálogo público e área de curadoria."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "properties"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("pesquisa/", views.PropertyListView.as_view(), name="property_list"),
    path("curadoria/novo/", views.CuratorPropertyCreateView.as_view(), name="curator_create"),
    path(
        "curadoria/<slug:reference>/",
        views.CuratorPropertyDetailView.as_view(),
        name="curator_detail",
    ),
    path(
        "curadoria/<slug:reference>/estado/",
        views.CuratorPropertyTransitionView.as_view(),
        name="curator_transition",
    ),
    path(
        "curadoria/<slug:reference>/fotografias/",
        views.CuratorPropertyImageView.as_view(),
        name="curator_images",
    ),
    path("imovel/<slug:reference>/", views.PropertyDetailView.as_view(), name="property_detail"),
]
