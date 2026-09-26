"""Rotas de autenticação sob o prefixo `/conta/`."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
    path("registo/", views.RegistrationView.as_view(), name="register"),
    path("recuperar/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "recuperar/enviado/",
        views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "recuperar/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "recuperar/concluido/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
