"""Rotas de autenticação sob o prefixo `/conta/`."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
    path("registo/", views.RegistrationView.as_view(), name="register"),
    # Perfil. Abre pelo avatar do cabeçalho e é de toda a gente com sessão: a
    # guarda está na vista, ao lado das outras contas.
    path("perfil/", views.ProfileView.as_view(), name="profile"),
    # Gestão de contas. As vistas exigem `require_admin` (§3): a rota ser
    # declarada não a torna pública, e a guarda está na vista para ficar ao
    # lado do resto da regra e não espalhada pelo ficheiro de rotas.
    path("equipa/", views.TeamListView.as_view(), name="team_list"),
    path("equipa/novo/", views.TeamMemberCreateView.as_view(), name="team_member_create"),
    path("clientes/", views.ClientListView.as_view(), name="client_list"),
    path("clientes/novo/", views.ClientCreateView.as_view(), name="client_create"),
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
