"""Formulários de cadastro, entrada e recuperação de senha."""

from __future__ import annotations

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import PasswordResetForm as DjangoPasswordResetForm
from django.core.exceptions import ValidationError

from apps.core.forms import BaseStyledForm
from apps.core.validators import (
    normalize_nif,
    validate_adult,
    validate_angolan_phone,
    validate_nif,
)
from apps.properties.reference import ANGOLA_PROVINCES

from .services import PASSWORD_RESET_NEUTRAL_MESSAGE, validate_password_strength

User = get_user_model()


class ClientRegistrationForm(BaseStyledForm):
    """Cadastro público que cria exclusivamente o perfil `CLIENT` (§3)."""

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Ana Maria dos Santos", "autocomplete": "name"}),
    )
    date_of_birth = forms.DateField(
        label="Data de nascimento",
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        validators=[validate_adult],
        widget=forms.DateInput(attrs={"type": "date", "autocomplete": "bday"}, format="%Y-%m-%d"),
        help_text="É preciso ter pelo menos 18 anos para criar uma conta.",
    )
    nif = forms.CharField(
        label="NIF",
        # Sem `max_length`: um NIF colado com espaços passa dos 14 caracteres e
        # seria recusado antes de a normalização o limpiar. O formato decide.
        validators=[validate_nif],
        widget=forms.TextInput(attrs={"placeholder": "009671373HA093", "inputmode": "text"}),
        help_text="Número de identificação fiscal: 9 dígitos, 2 letras e 3 dígitos.",
    )
    id_document_type = forms.ChoiceField(
        label="Tipo de documento",
        choices=[("", "Escolher…")] + list(User.IdDocumentType.choices),
        widget=forms.Select(attrs={"autocomplete": "off"}),
    )
    id_document_number = forms.CharField(
        label="Número do documento",
        max_length=30,
        widget=forms.TextInput(attrs={"placeholder": "003456789LA041"}),
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"placeholder": "ana@exemplo.ao", "autocomplete": "email"}),
    )
    phone = forms.CharField(
        label="Telefone",
        max_length=20,
        validators=[validate_angolan_phone],
        widget=forms.TextInput(
            attrs={"placeholder": "+244 923 456 789", "autocomplete": "tel", "inputmode": "tel"}
        ),
    )
    province = forms.ChoiceField(
        label="Província de residência",
        required=False,
        choices=[("", "Escolher…")] + list(ANGOLA_PROVINCES),
        widget=forms.Select(attrs={"autocomplete": "address-level1"}),
    )
    gender = forms.ChoiceField(
        label="Género",
        required=False,
        choices=[("", "Escolher…")] + list(User.Gender.choices),
        widget=forms.Select(attrs={"autocomplete": "sex"}),
    )
    password = forms.CharField(
        label="Palavra-passe",
        strip=False,
        min_length=8,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Mínimo 8 caracteres", "autocomplete": "new-password"}
        ),
    )
    password_confirm = forms.CharField(
        label="Confirmar palavra-passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Repita a palavra-passe", "autocomplete": "new-password"}
        ),
    )
    terms_accepted = forms.BooleanField(
        label="Li e aceito a política de privacidade e os termos de uso.",
        required=True,
    )

    def clean_nif(self) -> str:
        """Normaliza o NIF e recusa contas distintas com a mesma identidade."""
        nif = normalize_nif(self.cleaned_data["nif"])
        if User.objects.filter(nif=nif).exists():
            raise ValidationError("Já existe uma conta registada com este NIF.")
        return nif

    def clean_email(self) -> str:
        """Normaliza o e-mail e recusa contas já existentes."""
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean(self) -> dict[str, object]:
        """Confirma a palavra-passe e reforça a regra de robustez."""
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("password_confirm")
        if password and confirm and password != confirm:
            self.add_error("password_confirm", "As palavras-passe não coincidem.")
        if password:
            validate_password_strength(
                password,
                full_name=str(cleaned.get("full_name", "")),
                email=str(cleaned.get("email", "")),
            )
        return cleaned

    def save(self) -> User:
        """Cria o utilizador com perfil de cliente e a identidade verificada."""
        from .services import register_client

        return register_client(
            full_name=str(self.cleaned_data["full_name"]),
            email=str(self.cleaned_data["email"]),
            phone=str(self.cleaned_data.get("phone", "")),
            password=str(self.cleaned_data["password"]),
            nif=str(self.cleaned_data["nif"]),
            date_of_birth=self.cleaned_data["date_of_birth"],
            id_document_type=str(self.cleaned_data.get("id_document_type", "")),
            id_document_number=str(self.cleaned_data.get("id_document_number", "")),
            province=str(self.cleaned_data.get("province", "")),
            gender=str(self.cleaned_data.get("gender", "")),
        )


class LoginForm(BaseStyledForm):
    """Autenticação por e-mail com validação de credenciais no próprio formulário."""

    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"placeholder": "ana@exemplo.ao", "autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Palavra-passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "A sua palavra-passe", "autocomplete": "current-password"}
        ),
    )
    remember_me = forms.BooleanField(
        label="Manter sessão iniciada",
        required=False,
        initial=True,
    )

    def __init__(self, request: object = None, *args: object, **kwargs: object) -> None:
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("autocomplete", "off")

    def clean(self) -> dict[str, object]:
        """Devolve um erro genérico sem revelar se a conta existe (§6)."""
        cleaned = super().clean()
        email = str(cleaned.get("email", "")).strip().lower()
        password = str(cleaned.get("password", ""))
        if email and password:
            self.user = authenticate(self.request, username=email, password=password)
            if self.user is None:
                raise ValidationError("E-mail ou palavra-passe incorrectos.")
            if not self.user.is_active:
                raise ValidationError("E-mail ou palavra-passe incorrectos.")
        return cleaned

    def get_user(self) -> User | None:
        """Devolve o utilizador autenticado, como o `LoginView` do Django espera."""
        return self.user


class EchiloPasswordResetForm(BaseStyledForm, DjangoPasswordResetForm):
    """Formulário de recuperação que nunca revela a existência da conta."""

    # O `PasswordResetForm` do Django traz o rótulo "Email" em inglês.
    email = forms.EmailField(
        label="E-mail",
        max_length=254,
        widget=forms.EmailInput(attrs={"placeholder": "ana@exemplo.ao", "autocomplete": "email"}),
    )

    def get_users(self, email: str) -> list[User]:
        """Filtra apenas contas activas, ignorando o caso do e-mail."""
        return list(User.objects.filter(email__iexact=email, is_active=True))

    def send_mail(
        self,
        subject_template_name: str,
        email_template_name: str,
        context: dict[str, object],
        from_email: str | None,
        to_email: str,
        html_email_template_name: str | None = None,
        extra_email_context: dict[str, object] | None = None,
    ) -> None:
        """Delega o envio ao serviço para que a mensagem siga a identidade da marca."""
        from .services import send_password_reset_email

        user = self.get_users(to_email)
        send_password_reset_email(user=user[0] if user else None)


class SetPasswordForm(BaseStyledForm):
    """Redefinição da palavra-passe a partir de um token válido."""

    new_password1 = forms.CharField(
        label="Nova palavra-passe",
        strip=False,
        min_length=8,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Mínimo 8 caracteres", "autocomplete": "new-password"}
        ),
    )
    new_password2 = forms.CharField(
        label="Confirmar nova palavra-passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Repita a nova palavra-passe", "autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args: object, user: User | None = None, **kwargs: object) -> None:
        """Aceita o `user` que a view do Django injeta, como no `SetPasswordForm` nativo."""
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, object]:
        """Confirma a nova palavra-passe e mantém a regra de robustez do registo."""
        cleaned = super().clean()
        first = cleaned.get("new_password1")
        second = cleaned.get("new_password2")
        if first and second and first != second:
            self.add_error("new_password2", "As palavras-passe não coincidem.")
        if first and self.user is not None:
            validate_password_strength(
                str(first),
                full_name=str(getattr(self.user, "full_name", "")),
                email=str(getattr(self.user, "email", "")),
            )
        return cleaned

    def save(self, *args: object, **kwargs: object) -> User:
        """Grava a nova palavra-passe; a assinatura é a esperada pela view do Django."""
        user = self.user
        user.set_password(str(self.cleaned_data["new_password1"]))
        user.save(update_fields=["password"])
        return user
