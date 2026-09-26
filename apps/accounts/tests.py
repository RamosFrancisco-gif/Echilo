"""Testes de cadastro, entrada e recuperação de senha (§3 e §6)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import make_user, select_options
from apps.core.validators import normalize_nif
from apps.properties.reference import ANGOLA_PROVINCES

User = get_user_model()

PASSWORD = "Tchapo-forte-2026"


class RateLimitFreeTestCase(TestCase):
    """Base que zera os contadores de tentativas, isolando cada teste."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()


def registration_payload(**overrides: object) -> dict[str, object]:
    """Devolve um cadastro válido; cada teste altera só o campo que lhe interessa."""
    payload: dict[str, object] = {
        "full_name": "Ana Maria dos Santos",
        "date_of_birth": "1990-04-12",
        "nif": "009671373HA093",
        "id_document_type": "BI",
        "id_document_number": "003456789LA041",
        "email": "ana@exemplo.ao",
        "phone": "+244 923 456 789",
        "province": "LUANDA",
        "gender": "F",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        "terms_accepted": "on",
    }
    payload.update(overrides)
    return payload


class RegistrationTests(RateLimitFreeTestCase):
    """O registo público cria apenas clientes, nunca membros da equipa."""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("accounts:register")

    def test_registration_creates_a_client(self) -> None:
        """Uma conta nova é sempre `CLIENT` e fica autenticada."""
        response = self.client.post(self.url, registration_payload(email="Ana@Exemplo.AO"))

        self.assertEqual(response["Location"], reverse("accounts:login"))
        user = User.objects.get(email="ana@exemplo.ao")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_o_registo_oferece_as_vinte_e_uma_provincias(self) -> None:
        """Quem se regista escolhe de entre as 21, e não de entre as que têm imóveis.

        O campo herda as escolhas de `User.province`, e essa herança é o que
        mantém a lista igual à do catálogo. Um registo que oferecer um terço do
        país obriga quem vive em Moxico Leste a escrever a província à mão.
        """
        response = self.client.get(self.url)

        opcoes = select_options(response.content.decode(), "id_province")
        self.assertEqual(opcoes, [""] + [codigo for codigo, _ in ANGOLA_PROVINCES])

    def test_registration_persists_the_declared_identity(self) -> None:
        """A identidade declarada no formulário fica na conta, normalizada."""
        self.client.post(
            self.url,
            registration_payload(
                nif=" 009671373 ha 093 ",
                id_document_number=" 003456789LA041 ",
                gender="N",
            ),
        )

        user = User.objects.get(email="ana@exemplo.ao")
        self.assertEqual(user.nif, "009671373HA093")
        self.assertEqual(user.date_of_birth, date(1990, 4, 12))
        self.assertEqual(user.id_document_type, User.IdDocumentType.BI)
        self.assertEqual(user.id_document_number, "003456789LA041")
        self.assertEqual(user.province, "LUANDA")
        self.assertEqual(user.gender, User.Gender.NAO_DIZER)

    def test_registration_accepts_a_passport_and_optional_choices(self) -> None:
        """Província e género são opcionais; o passaporte é tão válido como o BI."""
        self.client.post(
            self.url,
            registration_payload(
                id_document_type="PASSPORT",
                id_document_number="AA1234567",
                province="",
                gender="",
            ),
        )

        user = User.objects.get(email="ana@exemplo.ao")
        self.assertEqual(user.id_document_type, User.IdDocumentType.PASSPORT)
        self.assertEqual(user.province, "")
        self.assertEqual(user.gender, "")

    def test_registration_refuses_a_minor(self) -> None:
        """Menores de idade não criam conta, mesmo faltando um dia para os 18."""
        tomorrow = timezone.now().date() + timedelta(days=365 * 18)
        birthday = tomorrow.replace(year=tomorrow.year - 18)
        response = self.client.post(
            self.url,
            registration_payload(date_of_birth=birthday.isoformat()),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ana@exemplo.ao").exists())
        self.assertIn("pelo menos 18 anos", response.content.decode())

    def test_registration_refuses_a_birth_date_in_the_future(self) -> None:
        """Uma data futura é sempre erro de digitação."""
        future = (timezone.now().date() + timedelta(days=400)).isoformat()
        response = self.client.post(self.url, registration_payload(date_of_birth=future))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ana@exemplo.ao").exists())
        self.assertIn("futuro", response.content.decode())

    def test_registration_refuses_a_malformed_nif(self) -> None:
        """O NIF segue o formato angolano, com ou sem espaços."""
        for invalid in ("00671373HA093", "009671373H093", "009671373hag93", "abc"):
            with self.subTest(nif=invalid):
                response = self.client.post(
                    self.url,
                    registration_payload(nif=invalid, email=f"{abs(hash(invalid))}@exemplo.ao"),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(User.objects.filter(nif=normalize_nif(invalid)).exists())
                self.assertIn("NIF válido", response.content.decode())

    def test_registration_refuses_a_duplicate_nif(self) -> None:
        """Um NIF identifica uma pessoa: não há duas contas com o mesmo."""
        make_user(email="outra@exemplo.ao", nif="009671373HA093")
        response = self.client.post(self.url, registration_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(nif="009671373HA093").count(), 1)
        self.assertFalse(User.objects.filter(email="ana@exemplo.ao").exists())
        self.assertIn("Já existe uma conta registada com este NIF", response.content.decode())

    def test_registration_refuses_duplicate_email(self) -> None:
        """Não há duas contas com o mesmo endereço."""
        make_user(email="ana@exemplo.ao")
        response = self.client.post(self.url, registration_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="ana@exemplo.ao").count(), 1)
        self.assertIn("Já existe uma conta", response.content.decode())

    def test_registration_refuses_password_repeating_the_name(self) -> None:
        """A palavra-passe não pode reproduzir o nome do titular."""
        response = self.client.post(
            self.url,
            registration_payload(password="Ana-Maria-2026", password_confirm="Ana-Maria-2026"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ana@exemplo.ao").exists())

    def test_registration_refuses_non_angolan_phone(self) -> None:
        """O telefone segue o formato de Angola."""
        response = self.client.post(self.url, registration_payload(phone="0812345678"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="ana@exemplo.ao").exists())


class LoginTests(RateLimitFreeTestCase):
    """A entrada valida as credenciais sem revelar se a conta existe."""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("accounts:login")
        self.user = make_user(email="ana@exemplo.ao")

    def test_login_succeeds_with_correct_password(self) -> None:
        """Credenciais correctas abrem sessão."""
        response = self.client.post(
            self.url, {"email": "ana@exemplo.ao", "password": PASSWORD, "remember_me": "on"}
        )
        self.assertRedirects(response, "/pesquisa/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_failure_is_generic(self) -> None:
        """E-mail errado e senha errada dão a mesma mensagem (§6)."""
        wrong_password = self.client.post(
            self.url, {"email": "ana@exemplo.ao", "password": "errada-123456"}
        )
        unknown_email = self.client.post(
            self.url, {"email": "ninguem@exemplo.ao", "password": "errada-123456"}
        )

        self.assertEqual(wrong_password.status_code, 200)
        self.assertEqual(unknown_email.status_code, 200)
        marker = "E-mail ou palavra-passe incorrectos."
        self.assertIn(marker, wrong_password.content.decode())
        self.assertIn(marker, unknown_email.content.decode())


class PasswordResetTests(RateLimitFreeTestCase):
    """Recuperação neutra, com link utilizável e sem revelar existência de contas."""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("accounts:password_reset")
        self.user = make_user(email="ana@exemplo.ao")

    def _link_from_mail(self, message: object) -> str:
        """Extrai o caminho de redefinição do corpo do e-mail."""
        match = re.search(r"(https?://[^\s]+/conta/recuperar/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+/)", message.body)
        assert match is not None, "O e-mail não contém um link de redefinição."
        return urlparse(match.group(1)).path

    def _confirm_path(self) -> str:
        """Segue o redireccionamento do Django e devolve o caminho final do formulário."""
        response = self.client.post(self.url, {"email": "ana@exemplo.ao"})
        self.assertEqual(response.status_code, 302)
        link = self._link_from_mail(mail.outbox[0])
        followed = self.client.get(link, follow=True)
        return followed.redirect_chain[-1][0]

    def test_reset_sends_email_with_working_link(self) -> None:
        """O e-mail leva um link absoluto que abre o formulário de nova palavra-passe."""
        response = self.client.post(self.url, {"email": "ana@exemplo.ao"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

        absolute = self._link_from_mail(mail.outbox[0])
        self.assertTrue(absolute.startswith("/conta/recuperar/"), absolute)
        self.assertNotIn("http://http", mail.outbox[0].body)

        confirm = self.client.get(absolute, follow=True)
        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, "Guardar nova palavra-passe")

    def test_reset_confirm_changes_the_password(self) -> None:
        """O link permite definir uma palavra-passe diferente."""
        path = self._confirm_path()

        new_password = "Nova-Tchapo-2026"
        response = self.client.post(
            path,
            {"new_password1": new_password, "new_password2": new_password},
        )

        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.assertFalse(self.user.check_password(PASSWORD))

    def test_reset_refuses_mismatched_confirmation(self) -> None:
        """A confirmação tem de coincidir com a nova palavra-passe."""
        path = self._confirm_path()

        response = self.client.post(
            path,
            {"new_password1": "Nova-Tchapo-2026", "new_password2": "Outra-Tchapo-2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_reset_rejects_invalid_token(self) -> None:
        """Um token adulterado não abre o formulário."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse("accounts:password_reset_confirm", args=[uid, "token-invalido"])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "já foi usado ou expirou", status_code=200)

    def test_reset_is_neutral_for_unknown_email(self) -> None:
        """A resposta é a mesma exista ou não a conta (§6)."""
        known = self.client.post(self.url, {"email": "ana@exemplo.ao"})
        mail.outbox.clear()
        unknown = self.client.post(self.url, {"email": "ninguem@exemplo.ao"})

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known["Location"], unknown["Location"])
        self.assertEqual(len(mail.outbox), 0)

        done = self.client.get(reverse("accounts:password_reset_done"))
        self.assertContains(done, "Se existir uma conta associada")


class ProfilePermissionTests(RateLimitFreeTestCase):
    """Perfis e permissões seguem a tabela de §3."""

    def test_public_registration_never_promotes_to_staff(self) -> None:
        """`is_staff` só é verdade para quem a equipa diferencia explicitamente."""
        self.client.post(reverse("accounts:register"), registration_payload())
        self.assertFalse(User.objects.get(email="ana@exemplo.ao").is_staff)
