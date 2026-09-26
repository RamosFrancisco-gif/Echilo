"""Views de autenticação: cadastro, entrada, saída e recuperação de senha."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import views as auth_views
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from apps.core.ratelimit import check_rate_limit, reset_rate_limit

from .forms import (
    ClientRegistrationForm,
    EchiloPasswordResetForm,
    LoginForm,
    SetPasswordForm,
)
from .services import PASSWORD_RESET_NEUTRAL_MESSAGE

LOGIN_RATE_SCOPE = "accounts.login"
REGISTER_RATE_SCOPE = "accounts.register"
RESET_RATE_SCOPE = "accounts.password_reset"


class LoginView(auth_views.LoginView):
    """Entrada por e-mail com limite de tentativas por endereço."""

    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Acrescenta a mensagem de sucesso ao contexto de entrada."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Entrar na sua conta"
        context["view_subtitle"] = "Acompanhe visitas, propostas e conversas com a equipa."
        return context

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Bloqueia o formulário quando o endereço excedeu o limite de tentativas."""
        limit = check_rate_limit(request, scope=LOGIN_RATE_SCOPE, limit=8, window=300)
        if not limit.allowed:
            messages.error(
                request,
                _(
                    "Demasiadas tentativas de entrada. Aguarde alguns minutos "
                    "antes de voltar a tentar."
                ),
            )
            return render(
                request,
                self.template_name,
                {
                    "form": self.get_form_class()(),
                    "view_title": "Entrar na sua conta",
                    "view_subtitle": "Acompanhe visitas, propostas e conversas com a equipa.",
                    "rate_limited": True,
                },
                status=429,
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: LoginForm) -> HttpResponseRedirect:
        """Inicia a sessão, honrando a opção de sessão persistente."""
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)
        self.request.session.modified = True
        reset_rate_limit(self.request, scope=LOGIN_RATE_SCOPE)
        return response


class LogoutView(auth_views.LogoutView):
    """Termina a sessão e devolve o utilizador à página inicial."""

    next_page = reverse_lazy("properties:home")


class RegistrationView(FormView):
    """Cadastro público de clientes, disponível sem autenticação."""

    template_name = "accounts/register.html"
    form_class = ClientRegistrationForm
    success_url = reverse_lazy("accounts:login")

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Fornece os textos da página de cadastro."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Criar a sua conta"
        context["view_subtitle"] = "Gratuito. A equipa valida cada imóvel antes de o publicar."
        return context

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Protege o cadastro contra abuso de envio automático."""
        limit = check_rate_limit(request, scope=REGISTER_RATE_SCOPE, limit=5, window=600)
        if not limit.allowed:
            messages.error(
                request,
                _("Demasiados pedidos de cadastro a partir deste dispositivo. Tente mais tarde."),
            )
            return HttpResponseRedirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: ClientRegistrationForm) -> HttpResponseRedirect:
        """Cria a conta, autentica o utilizador e limpa o contador de tentativas."""
        user = form.save()
        auth_login(
            self.request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        reset_rate_limit(self.request, scope=REGISTER_RATE_SCOPE)
        messages.success(
            self.request,
            _(
                "Conta criada. A equipa do Echilo entra em contacto consigo antes de "
                "publicar qualquer imóvel."
            ),
        )
        return HttpResponseRedirect(self.success_url)


class PasswordResetView(auth_views.PasswordResetView):
    """Recebe o e-mail e envia o link de redefinição sem revelar existência de contas."""

    template_name = "accounts/password_reset.html"
    form_class = EchiloPasswordResetForm
    email_template_name = "accounts/emails/password_reset.txt"
    html_email_template_name = "accounts/emails/password_reset.html"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    from_email = None

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Fornece os textos da página de recuperação."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Recuperar palavra-passe"
        context["view_subtitle"] = "Enviamos um link seguro para o endereço indicado."
        return context

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Limita o número de pedidos de recuperação por endereço."""
        limit = check_rate_limit(request, scope=RESET_RATE_SCOPE, limit=4, window=600)
        if not limit.allowed:
            messages.error(
                request,
                _("Demasiados pedidos de recuperação. Aguarde antes de tentar novamente."),
            )
            return HttpResponseRedirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: EchiloPasswordResetForm) -> HttpResponseRedirect:
        """Envia o e-mail em segundo plano e redirecciona para a confirmação."""
        form.save(request=self.request)
        return HttpResponseRedirect(self.get_success_url())


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    """Confirmação neutra, igual exista ou não a conta (§6)."""

    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Reutiliza a mensagem neutra na página de confirmação."""
        context = super().get_context_data(**kwargs)
        context["neutral_message"] = PASSWORD_RESET_NEUTRAL_MESSAGE
        return context


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Define a nova palavra-passe a partir do link enviado por e-mail."""

    template_name = "accounts/password_reset_confirm.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Fornece os textos da página de definição de palavra-passe."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Definir nova palavra-passe"
        context["view_subtitle"] = "Escolha uma palavra-passe que não use noutro serviço."
        return context

    def form_valid(self, form: SetPasswordForm) -> HttpResponseRedirect:
        """Grava a palavra-passe e autentica o utilizador automaticamente."""
        response = super().form_valid(form)
        auth_login(
            self.request,
            form.user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        messages.success(self.request, _("Palavra-passe actualizada com sucesso."))
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    """Confirma que a palavra-passe foi alterada."""

    template_name = "accounts/password_reset_complete.html"
