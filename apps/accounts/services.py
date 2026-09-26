"""Regras de negócio do app de contas, isoladas das views."""

from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from apps.core.validators import normalize_nif

User = get_user_model()

PASSWORD_RESET_SUBJECT = "Redefinir a sua palavra-passe — Echilo"

# Resposta única para qualquer pedido, exista ou não a conta (§6).
PASSWORD_RESET_NEUTRAL_MESSAGE = _(
    "Se existir uma conta associada a este endereço, enviámos um link para "
    "redefinir a palavra-passe."
)


def register_client(
    *,
    full_name: str,
    email: str,
    phone: str,
    password: str,
    nif: str,
    date_of_birth: date,
    id_document_type: str = "",
    id_document_number: str = "",
    province: str = "",
    gender: str = "",
) -> User:
    """Cria uma conta de cliente — o registo público nunca promove o perfil (§3)."""
    user = User(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        phone=phone.strip(),
        # `None` e não `""`: o NIF é único, e vazios repetidos colidiriam entre si.
        nif=normalize_nif(nif) or None,
        date_of_birth=date_of_birth,
        id_document_type=id_document_type,
        id_document_number=id_document_number.strip(),
        province=province,
        gender=gender,
        role=User.Role.CLIENT,
    )
    user.set_password(password)
    user.save()
    return user


@transaction.atomic
def send_password_reset_email(*, user: User) -> bool:
    """Envia o link de redefinição. Devolve `False` quando a conta não existe."""
    if user is None or not user.is_active or not user.email:
        return False

    from django.conf import settings
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    protocol = "http" if settings.DEBUG else "https"
    site_url = settings.SITE_URL.rstrip("/")
    if "://" not in site_url:
        site_url = f"{protocol}://{site_url}"

    context: dict[str, object] = {
        "user": user,
        "protocol": protocol,
        "site_url": site_url,
        "site_name": "Echilo",
        "uid": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user),
    }

    body = render_to_string("accounts/emails/password_reset.txt", context)
    html_body = render_to_string("accounts/emails/password_reset.html", context)
    subject = render_to_string(
        "accounts/emails/password_reset_subject.txt", context
    ).strip()

    message = EmailMultiAlternatives(
        subject=subject or str(PASSWORD_RESET_SUBJECT),
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)
    return True


def validate_password_strength(password: str, *, full_name: str, email: str) -> None:
    """Exige que a palavra-passe não reproduza o nome ou o e-mail do titular."""
    lowered = password.lower()
    tokens = {token for token in f"{full_name} {email}".lower().split() if len(token) > 3}
    if any(token in lowered for token in tokens):
        raise ValidationError("A palavra-passe não pode repetir o seu nome ou e-mail.")
