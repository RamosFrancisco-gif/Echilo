"""Views de autenticação: cadastro, entrada, saída e recuperação de senha."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, ListView

from apps.core.permissions import require_admin
from apps.core.ratelimit import check_rate_limit, reset_rate_limit

from .forms import (
    ClientCreateForm,
    ClientRegistrationForm,
    EchiloPasswordResetForm,
    LoginForm,
    PasswordChangeForm,
    ProfileForm,
    SetPasswordForm,
    TeamMemberCreateForm,
)
from .models import User
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


class ProfileView(FormView):
    """Edição da conta de quem está com a sessão iniciada.

    Abre por `/conta/perfil/`, que é para onde vai o avatar do cabeçalho. A página
    é de toda a gente com sessão — cliente e equipa — porque o telefone e a
    provinsi interessam à equipa exactamente como interessam a um cliente, e
    dois formulários para a mesma pergunta são duas respostas a ela.

    O `role` não é editável aqui. O §3 diz que a promoção de perfil é feita no
    painel ou por `MANAGER_EMAILS`, e um campo de perfil que deixa a pessoa
    escrever `ADMIN` no seu próprio perfil é um campo que escreve `ADMIN` na
    base de dados.
    """

    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Exige sessão: a página de alguém sem conta não tem dados para mostrar."""
        if not request.user.is_authenticated:
            raise PermissionDenied(_("Entre na sua conta para ver o seu perfil."))
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, object]:
        """Edita a conta em sessão, e não uma conta escolhida pela URL."""
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        return kwargs

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Distingue os dois formulários da página pelo botão que os submeteu.

        A palavra-passe tem o seu próprio formulário e a sua própria URL de
        destino, mas vive na mesma página. O botão que a submete chama-se
        `alterar_senha`, e é esse nome — e não a presença de um campo — que
        decide qual dos dois se valida. Um `POST` a esvaziar a página de outra
        forma faria a palavra-passe ser guardada a partir de um formulário de
        dados onde nem o campo existe.
        """
        if "alterar_senha" not in request.POST:
            return super().post(request, *args, **kwargs)

        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            # Sem isto, trocar a palavra-passe terminava a sessão: o hash que o
            # Django guarda nela é derivado da palavra-passe, e mudá-la
            # invalidaria a sessão de quem acabou de a mudar. A pessoa era
            # deitada fora da página que usou para a mudar.
            update_session_auth_hash(request, form.user)
            messages.success(request, _("Palavra-passe alterada."))
            return HttpResponseRedirect(self.success_url)
        return self.render_to_response(
            self.get_context_data(senha_form=form, formulario="senha")
        )

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Junta à página o resumo da conta e o formulário que não foi tocado.

        O formulário de dados é posto pelo `FormMixin`, e num `POST` de senha
        isso dá um formulário **sem erros**: a página de uma palavra-passe
        recusada tem de mostrar os campos de dados sem os dar como errados,
        porque ninguém os tocou.
        """
        context = super().get_context_data(**kwargs)
        context.setdefault("formulario", "dados")
        context.setdefault("senha_form", PasswordChangeForm(user=self.request.user))
        context.setdefault("view_title", "O seu perfil")
        user = self.request.user
        context["resumo"] = {
            "nome": user.full_name,
            "perfil": dict(User.Role.choices).get(user.role, user.role),
            "entrou": user.date_joined,
            "email": user.email,
            "telefone": user.phone or "—",
            "nif": user.nif or "Não indicado",
        }
        return context

    def form_valid(self, form: ProfileForm) -> HttpResponseRedirect:
        """Guarda o perfil e diz o que mudou, sem recarregar a página."""
        form.save()
        messages.success(self.request, _("Dados actualizados."))
        return super().form_valid(form)


class TeamListView(ListView):
    """Quem pertence à equipa, para o administrador ver quem está registado.

    Existe porque o cadastro de membros é uma das duas coisas que a administração
    faz e não tinha onde entrar. A lista é a resposta a "quem é que tem acesso",
    que é a pergunta que se segue a "criei a conta" e que um formulário de criação
    não responde.
    """

    template_name = "accounts/team_list.html"
    context_object_name = "membros"
    paginate_by = 25

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a lista à administração (§3)."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para continuar.")
        require_admin(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[User]:
        """Devolve a equipa pela ordem em que se lê: quem cria, quem valida, quem curador."""
        return User.objects.filter(is_team_member=True).order_by("role", "full_name")


class TeamMemberCreateView(FormView):
    """Cadastro de membro da equipa, exclusivo da administração (§3)."""

    template_name = "accounts/team_member_form.html"
    form_class = TeamMemberCreateForm
    success_url = reverse_lazy("accounts:team_list")

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe o cadastro à administração (§3)."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para continuar.")
        require_admin(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Descreve a página e o que cada perfil passa a poder fazer."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Cadastrar membro da equipa"
        context["view_subtitle"] = "O perfil escolhido decide o que a pessoa vê no menu."
        return context

    def form_valid(self, form: TeamMemberCreateForm) -> HttpResponseRedirect:
        """Cria a conta e diz o que ficou criado, com o perfil à vista."""
        user = form.save()
        messages.success(
            self.request,
            _("%(nome)s foi cadastrado(a) como %(perfil)s.")
            % {"nome": user.full_name, "perfil": user.get_role_display()},
        )
        return HttpResponseRedirect(str(self.success_url))


class ClientListView(ListView):
    """Clientes registados, para a administração os encontrar e rever."""

    template_name = "accounts/client_list.html"
    context_object_name = "clientes"
    paginate_by = 25

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe a lista à administração (§3)."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para continuar.")
        require_admin(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[User]:
        """Devolve os clientes, filtrando pelo texto pesquisa quando há."""
        base = User.objects.filter(is_team_member=False)
        procura = (self.request.GET.get("q") or "").strip()
        if procura:
            base = base.filter(
                Q(full_name__icontains=procura)
                | Q(email__icontains=procura)
                | Q(nif__icontains=procura)
                | Q(phone__icontains=procura)
            )
        return base.order_by("full_name")

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Devolve também o texto pesquisado, para o campo o repetir."""
        context = super().get_context_data(**kwargs)
        context["procura"] = (self.request.GET.get("q") or "").strip()
        context["total"] = User.objects.filter(is_team_member=False).count()
        return context


class ClientCreateView(FormView):
    """Cadastro de cliente feito pela equipa, sem passar pelo formulário público."""

    template_name = "accounts/client_form.html"
    form_class = ClientCreateForm
    success_url = reverse_lazy("accounts:client_list")

    def dispatch(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Restringe o cadastro à administração (§3)."""
        if not request.user.is_authenticated:
            raise PermissionDenied("Entre na sua conta para continuar.")
        require_admin(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Descreve a página, sem repetir a validação do formulário público."""
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Cadastrar cliente"
        context["view_subtitle"] = "Registe a identidade tal como a pessoa a apresentou."
        return context
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Cadastrar cliente"
        context["view_subtitle"] = "Registe a identidade tal como a pessoa a apresentou."
        return context

    def form_valid(self, form: ClientCreateForm) -> HttpResponseRedirect:
        """Cria a conta de cliente e devolve à lista."""
        user = form.save()
        messages.success(
            self.request,
            _("Cliente %(nome)s cadastrado com o perfil de cliente.")
            % {"nome": user.full_name},
        )
        return HttpResponseRedirect(str(self.success_url))


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
