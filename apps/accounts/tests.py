"""Testes de cadastro, entrada e recuperação de senha (§3 e §6)."""

from __future__ import annotations

import io
import os
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.forms import LIMITE_FOTO_PERFIL_MB
from apps.core.pagination import PAGINA_PADRAO
from apps.core.permissions import is_team_member
from apps.core.storage import (
    LADO_RETRATO_PX,
    CloudinaryAvatarStorage,
    storage_perfis,
)
from apps.core.testing import jpeg_bytes, make_user, select_options
from apps.core.validators import normalize_nif
from apps.properties.reference import ANGOLA_PROVINCES
from apps.properties.validators import (
    ORCAMENTO_PERFIL_MB,
    config_perfil,
)

User = get_user_model()

PASSWORD = "Tchapo-forte-2026"

# A mesma credencial dos testes da storage: uma storage da Cloudinary recusa
# arrancar sem `CLOUDINARY_URL`, e um teste que constrói uma tem de lha dar.
CREDENCIAL = "cloudinary://123456789012345:abcdefghijklmnopqrstuvwxyz123456@echiloteste"

APENAS_NUVEM = override_settings(
    CLOUDINARY_URL=CREDENCIAL,
    CLOUDINARY_PUBLICAO=True,
    MEDIA_PERFIS_BACKEND="apps.core.storage.CloudinaryAvatarStorage",
)


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

    def test_um_perfil_equivoca_ao_perfil_inteiro(self) -> None:
        """`can_curate` é dos três que curam e `can_manage_users` é só do chefe.

        A tabela de §3 é uma lista de perfis, não uma escala. Se `can_curate`
        fosse verdade para o cliente, o menu haveria de mostrar a curadoria a toda
        a gente que entrasse; e se `can_manage_users` fosse verdade para a
        equipa, qualquer curador criava administradores.
        """
        for perfil in (User.Role.CURATOR, User.Role.AGENT, User.Role.ADMIN):
            with self.subTest(perfil=perfil):
                user = make_user(role=perfil, email=f"{perfil.lower()}@echilo.ao")
                self.assertTrue(user.can_curate)
                self.assertEqual(user.can_manage_users, perfil == User.Role.ADMIN)

        cliente = make_user(email="so-cliente@echilo.ao")
        self.assertFalse(cliente.can_curate)
        self.assertFalse(cliente.can_manage_users)

    def test_um_perfil_que_nada_lhe_oferece_a_equipa_nao_entra(self) -> None:
        """Um perfil novo não entra na equipa por existir.

        A regra é uma lista branca, `is_team_role`, e é o que o `save()`, a guarda
        e o menu leem. Isto fixa o motivo de ser uma lista: com `role != CLIENT` um
        valor novo escrito no `Role` — o `OWNER` do portal do proprietário, por
        exemplo — entrava como equipa sem uma linha de código ser revista, e o
        próprio `save()` gravava isso na conta. A armadilha não dava erro, e o
        primeiro sinal era um proprietário dentro da curadoria.
        """
        # Um valor fora do `Role` é o que o `TextChoices` ainda não conhece, e é
        # também o que a regra tem de recusar: `choices` não valida no `save()`.
        for perfil in (User.Role.CLIENT, "TERCEIRO", "", "owner"):
            with self.subTest(perfil=perfil):
                user = make_user(email=f"{perfil or 'vazio'}@echilo.ao")
                user.role = perfil
                user.save()

                self.assertFalse(user.is_team_role)
                self.assertFalse(user.is_team_member)
                self.assertFalse(is_team_member(user))

        for perfil in (User.Role.CURATOR, User.Role.AGENT, User.Role.ADMIN):
            with self.subTest(perfil=perfil):
                user = make_user(role=perfil, email=f"equipa-{perfil.lower()}@echilo.ao")
                self.assertTrue(user.is_team_role)
                self.assertTrue(user.is_team_member)
                self.assertTrue(is_team_member(user))

    def test_a_equipa_e_um_perfil_dos_que_curam_e_um_que_administra(self) -> None:
        """Cada perfil da equipa fica dentro do produto, e o cliente fora dele.

        Três sítios respondem a "é da equipa": o `save()` que grava o campo, a
        guarda das views e o contexto dos templates. Se divergissem, a entrada do
        menu apareceria a quem a página recusa, ou o contrário. Ler a mesma
        propriedade nos três é o que impede a segunda resposta.
        """
        cliente = make_user(email="fora@echilo.ao")
        self.assertEqual(
            [cliente.is_team_member, is_team_member(cliente), cliente.can_curate],
            [False, False, False],
        )
        for perfil in (User.Role.CURATOR, User.Role.AGENT, User.Role.ADMIN):
            with self.subTest(perfil=perfil):
                user = make_user(role=perfil, email=f"dentro-{perfil.lower()}@echilo.ao")
                self.assertEqual(
                    [user.is_team_member, is_team_member(user), user.can_curate],
                    [True, True, True],
                )


class PerfilDaContaTests(RateLimitFreeTestCase):
    """A página que se abre pelo avatar: edita o contacto, a identidade e a senha."""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("accounts:profile")
        self.user = make_user(email="ana@exemplo.ao")
        self.client.force_login(self.user)

    def _dados(self, **overrides: object) -> dict[str, object]:
        dados: dict[str, object] = {
            "full_name": "Ana Maria dos Santos",
            "email": "ana@exemplo.ao",
            "phone": "+244 923 456 789",
            "province": "LUANDA",
            "gender": "F",
        }
        dados.update(overrides)
        return dados

    def _retrato(self, nome: str = "ana.jpg") -> SimpleUploadedFile:
        return SimpleUploadedFile(nome, jpeg_bytes(), content_type="image/jpeg")

    def test_quem_nao_tem_sessao_nao_ve_o_perfil(self) -> None:
        """A página é de quem entrou; sem sessão não há dados para mostrar."""
        self.client.logout()

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 403)

    def test_edita_o_nome_o_telefone_e_a_provincia(self) -> None:
        resposta = self.client.post(
            self.url,
            self._dados(
                full_name="Ana Maria dos Santos Ferreira",
                phone="+244 934 111 222",
                province="BENGUELA",
            ),
        )

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Ana Maria dos Santos Ferreira")
        self.assertEqual(self.user.phone, "+244 934 111 222")
        self.assertEqual(self.user.province, "BENGUELA")

    def test_a_fotografia_substitui_as_iniciais_no_cabecalho(self) -> None:
        """O que a pessoa carregou é o que o resto do produto mostra."""
        self.client.post(self.url, {**self._dados(), "photo": self._retrato()})

        self.user.refresh_from_db()
        self.assertTrue(self.user.photo)
        html = self.client.get(reverse("properties:home")).content.decode("utf-8")
        with self.subTest(esperado=self.user.photo.name):
            self.assertIn(self.user.photo.url, html)
        self.assertNotIn(
            f'aria-hidden="true">{self.user.initials}<', html
        )

    def test_o_retrato_recusa_o_que_nao_e_imagem(self) -> None:
        """Um `.jpg` que é um executável passa no cabeçalho e no Pillow não."""
        falso = SimpleUploadedFile("foto.jpg", b"MZ executable", content_type="image/jpeg")

        resposta = self.client.post(self.url, {**self._dados(), "photo": falso})

        self.assertEqual(resposta.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.photo)

    def test_o_retrato_recusa_o_que_passa_do_tecto(self) -> None:
        grande = SimpleUploadedFile(
            "gigante.jpg",
            b"\xff\xd8" + b"0" * (LIMITE_FOTO_PERFIL_MB * 1024 * 1024),
            content_type="image/jpeg",
        )

        resposta = self.client.post(self.url, {**self._dados(), "photo": grande})

        self.assertEqual(resposta.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.photo)

    def test_a_caixa_de_remover_some_quando_nao_ha_fotografia(self) -> None:
        """Marcada sem foto, ela diria "remover" e não aconteceria nada."""
        html = self.client.get(self.url).content.decode("utf-8")

        self.assertNotIn('name="remover_foto" type="checkbox"', html)
        self.assertIn('name="remover_foto" value=""', html)

    def test_remover_a_fotografia_devolve_as_iniciais(self) -> None:
        self.client.post(self.url, {**self._dados(), "photo": self._retrato()})
        self.user.refresh_from_db()
        self.assertTrue(self.user.photo)

        self.client.post(self.url, {**self._dados(), "remover_foto": "on"})

        self.user.refresh_from_db()
        self.assertFalse(self.user.photo)

    def test_trocar_e_remover_ao_mesmo_tempo_e_recusado(self) -> None:
        """A pessoa pediu uma coisa ou a outra, e a intenção é uma só."""
        self.client.post(self.url, {**self._dados(), "photo": self._retrato()})
        self.user.refresh_from_db()
        antes = self.user.photo.name

        resposta = self.client.post(
            self.url, {**self._dados(), "photo": self._retrato("nova.jpg"), "remover_foto": "on"}
        )

        self.assertEqual(resposta.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.photo.name, antes)

    def test_a_identidade_edita_se(self) -> None:
        """NIF, nascimento e documento editam-se, e a página mostra os campos."""
        self.client.force_login(self.user)

        html = self.client.get(self.url).content.decode("utf-8")
        for campo in ("nif", "date_of_birth", "id_document_type", "id_document_number"):
            self.assertIn(f'name="{campo}"', html)

        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "nif": "009671373HA093",
                "date_of_birth": "1988-02-03",
                "id_document_type": "BI",
                "id_document_number": "003456789LA041",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nif, "009671373HA093")
        self.assertEqual(self.user.date_of_birth, date(1988, 2, 3))
        self.assertEqual(self.user.id_document_type, "BI")
        self.assertEqual(self.user.id_document_number, "003456789LA041")

    def test_o_nif_editado_normaliza_se(self) -> None:
        """Com hyphens e minúsculas, o NIF gravado é o canónico.

        O registo faz o mesmo e o NIF identifica uma pessoa: dois formatos do
        mesmo número são duas contas a quem a equipa tenta confirmar.
        """
        resposta = self.client.post(self.url, {**self._dados(), "nif": "006-713 173ha 093"})

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nif, "006713173HA093")

    def test_o_nif_de_outra_conta_e_recusado(self) -> None:
        """O NIF identifica uma pessoa, e a unicidade é o que garante isso."""
        make_user(email="outro@echilo.ao", nif="009671373HA093")

        resposta = self.client.post(self.url, {**self._dados(), "nif": "009671373HA093"})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Já existe uma conta registada com este NIF.")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.nif)

    def test_um_nif_mal_formado_e_recusado(self) -> None:
        """Editar o campo não afrouxa o formato que o §2.11 impõe."""
        resposta = self.client.post(self.url, {**self._dados(), "nif": "abc"})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "9 dígitos")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.nif)

    def test_um_nascimento_de_menor_e_recusado(self) -> None:
        """A idade mínima do §2.11 continua a valer quando o campo é editável."""
        resposta = self.client.post(self.url, {**self._dados(), "date_of_birth": "2015-01-01"})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "18 anos")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.date_of_birth)

    def test_o_nif_em_branco_guarda_none_e_nao_a_string_vazia(self) -> None:
        """`nif` é `unique`, e duas contas de equipa sem NIF colidiriam em `""`.

        A equipa não é registrada com NIF (§3.2), logo a coluna é `NULL` para
        ela. Voltar a pôr `""` ao limpar o campo faria a segunda conta de equipa
        falhar com um erro de base de dados em vez de um formulário.
        """
        self.user.nif = "009671373HA093"
        self.user.save(update_fields=["nif"])
        make_user(email="colega@echilo.ao")

        resposta = self.client.post(self.url, {**self._dados(), "nif": ""})

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.nif)

    def test_o_perfil_nao_promove_a_si_proprio(self) -> None:
        """O §3 diz que a promoção é feita no painel, e um campo de perfil que a
        deixasse escrever era um campo que escrevia `ADMIN` na base de dados."""
        resposta = self.client.post(self.url, {**self._dados(), "role": "ADMIN"})

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.CLIENT)

    def test_o_e_mail_ja_usado_por_outra_conta_e_recusado(self) -> None:
        make_user(email="ocupado@echilo.ao")

        resposta = self.client.post(self.url, self._dados(email="ocupado@echilo.ao"))

        self.assertEqual(resposta.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "ana@exemplo.ao")

    def test_o_avatar_aponta_para_o_perfil(self) -> None:
        """O caminho de entrada é o avatar, e sem ele a página não tem porta."""
        html = self.client.get(reverse("properties:home")).content.decode("utf-8")

        self.assertIn(f'href="{self.url}"', html)

    def test_a_pagina_tem_os_dois_formularios(self) -> None:
        """Dados e palavra-passe vivem na mesma página, em formulários separados.

        Num só formulário, o botão de um acabava por submeter o outro: guardar o
        nome podia mandar a palavra-passe, e trocar a senha podia gravar os
        campos que ninguém preencheu. O que os separa é o `</form>` entre os
        dois campos, e é isso que o teste mede — não o número de `<form>` da
        página, que inclui o do menu e o do rodapé.
        """
        html = self.client.get(self.url).content.decode("utf-8")

        self.assertIn('name="old_password"', html)
        self.assertIn('name="new_password1"', html)
        self.assertIn('name="alterar_senha"', html)
        self.assertEqual(html.count('name="alterar_senha"'), 1)
        self.assertIn("Identificação", html)

        entre = html[html.index('name="full_name"') : html.index('name="old_password"')]
        self.assertIn("</form>", entre, "os dados e a senha não podem ser o mesmo formulário")

    def test_a_palavra_passe_actual_e_exigida(self) -> None:
        """Trocar a senha sem saber a de agora não é mudar a senha, é dar o comando.

        Sem este campo, quem encontrasse a sessão aberta — um computador
        partilhado, um separador deixado aberto — trocava a palavra-passe da
        conta e ficava com ela.
        """
        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "new_password1": "OutraSenhaBoa#2026",
                "new_password2": "OutraSenhaBoa#2026",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Este campo é obrigatório")
        self.assertTrue(self.user.check_password(PASSWORD), "a senha actual não pode mudar")

    def test_a_palavra_passe_actual_errada_nao_muda_nada(self) -> None:
        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "old_password": "nao-e-a-senha",
                "new_password1": "OutraSenhaBoa#2026",
                "new_password2": "OutraSenhaBoa#2026",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "palavra-passe actual está incorrecta")
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_palavra_passe_troca_se(self) -> None:
        """A senha nova é a que entra a seguir, e a sessão continua de pé."""
        nova = "OutraSenhaBoa#2026"

        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "old_password": PASSWORD,
                "new_password1": nova,
                "new_password2": nova,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(nova))
        self.assertFalse(self.user.check_password(PASSWORD))
        # A pessoa não é deitada fora do sítio por trocar a própria senha.
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_as_duas_palavras_passe_tem_de_coincidir(self) -> None:
        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "old_password": PASSWORD,
                "new_password1": "OutraSenhaBoa#2026",
                "new_password2": "SenhaDiferente#2026",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "As palavras-passe não coincidem")
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_senha_nova_recusa_a_que_repite_o_nome(self) -> None:
        """A regra do projecto vale aqui como vale no registo e na recuperação."""
        repetida = "ana-maria-santos"

        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "old_password": PASSWORD,
                "new_password1": repetida,
                "new_password2": repetida,
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "8 caracteres")
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_o_botao_dos_dados_nao_troca_a_senha(self) -> None:
        """Guardar os dados não pode mexer na palavra-passe.

        O formulário dos dados e o da senha dividem a página, e o que diz qual
        deles foi submetido é o nome do botão. Se a decisão fosse por outro
        lado, um `POST` dos dados acabava por validar o formulário errado.
        """
        resposta = self.client.post(self.url, self._dados())

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_senha_recusada_nao_marca_os_dados_como_erro(self) -> None:
        """Quem errou a senha não deve ver os campos de dados em vermelho.

        O formulário de dados volta sem erros porque ninguém o tocou, e é o que
        a pessoa lê primeiro: seis campos marcados a vermelho numa página em que
        só se errou a senha fazem-na procurar o problema no sítio errado.
        """
        resposta = self.client.post(
            self.url,
            {
                **self._dados(),
                "alterar_senha": "1",
                "old_password": "errada",
                "new_password1": "OutraSenhaBoa#2026",
                "new_password2": "OutraSenhaBoa#2026",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        # O cartão da senha é o único a ser assinalado, e os campos de dados
        # continuam sem `has-error`: ninguém os tocou.
        self.assertContains(resposta, "is-recusado")
        html = resposta.content.decode("utf-8")
        trecho = html[html.index('class="settings-form"') : html.index("</form>")]
        self.assertNotIn("has-error", trecho)

    def test_guardar_o_texto_mantem_a_fotografia(self) -> None:
        """Mandar o nome sem escolher ficheiro não pode apagar o retrato.

        Com `ClearableFileInput` o formulário ligado devolve o ficheiro que já
        estava em `cleaned_data['photo']`, e validar isso é tentar abrir uma
        fotografia que a pessoa não escolheu. A conta ficava sem retrato por
        causa de um campo que não foi mexido.
        """
        self.client.post(self.url, {**self._dados(), "photo": self._retrato()})
        self.user.refresh_from_db()
        antes = self.user.photo.name

        resposta = self.client.post(self.url, self._dados(phone="+244 934 555 666"))

        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+244 934 555 666")
        self.assertEqual(self.user.photo.name, antes)

    def test_trocar_a_fotografia_apaga_a_antiga_do_armazenamento(self) -> None:
        """Um retrato trocado deixado no storage é uma factura mensal de ninguém."""
        self.client.post(self.url, {**self._dados(), "photo": self._retrato("antiga.jpg")})
        self.user.refresh_from_db()
        caminho_antigo = self.user.photo.path

        self.client.post(self.url, {**self._dados(), "photo": self._retrato("nova.jpg")})

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.photo.name, "perfis")
        self.assertNotIn("antiga", self.user.photo.name)
        self.assertFalse(os.path.exists(caminho_antigo))

    def test_a_equipa_tambem_edita_o_seu_perfil(self) -> None:
        """A página é a mesma para cliente e equipa; o telefone interessa igual."""
        agente = make_user(role="AGENT", email="agente@echilo.ao")
        self.client.force_login(agente)

        resposta = self.client.post(self.url, self._dados(email="agente@echilo.ao", phone="+244 912 000 111"))

        self.assertEqual(resposta.status_code, 302)
        agente.refresh_from_db()
        self.assertEqual(agente.phone, "+244 912 000 111")


class RetratoNaNuvemTests(RateLimitFreeTestCase):
    """O retrato vai para a Cloudinary, pequeno, e nunca como a pessoa o enviou.

    Estas são as três garantias que o §6 pede para qualquer imagem e que o campo
    `photo` cumpria só pela metade: ia para a pasta das capas, guardava-se aos
    2000 px, e recusava uma fotografia boa por ter lados a mais.
    """

    def setUp(self) -> None:
        super().setUp()
        self.user = make_user(email="ana@echilo.ao", full_name="Ana Maria dos Santos")
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    @staticmethod
    def _grande(lado: int = 2600, altura: int = 1950) -> SimpleUploadedFile:
        """Uma fotografia de telemóvel: lado grande e um ficheiro de vários megabytes.

        O conteúdo é ruído, e não uma cor lisa: um JPEG de uma cor só tem 190 KB
        mesmo com 4000 px de lado, e um teste que compara o tamanho do que entra
        com o do que fica passava a medir a compressão de uma cor só.

        Os 2600 px são de propósito: passam do tecto antigo de 2000 que esta
        fotografia chumbava, e ficam dentro dos 4 MB que o formulário aceita.
        Ruído a 4000 px dá 12 MB, que o tecto recusa — e recusa bem, porque esse
        é um ficheiro que não devia ir num pedido de um retrato.
        """
        ruido = Image.effect_noise((lado, altura), 120).convert("RGB")
        buffer = io.BytesIO()
        ruido.save(buffer, "JPEG", quality=80)
        return SimpleUploadedFile(
            "telemovel.jpg", buffer.getvalue(), content_type="image/jpeg"
        )

    def _dados(self) -> dict[str, str]:
        return {
            "full_name": self.user.full_name,
            "email": self.user.email,
            "nif": "009671373HA093",
            "birth_date": "1990-04-02",
            "id_document_type": "BI",
            "id_document_number": "004512389LA041",
        }

    def _guarda(self, ficheiro: SimpleUploadedFile) -> None:
        resposta = self.client.post(self.url, {**self._dados(), "photo": ficheiro})
        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()

    def test_o_retrato_trocado_apaga_se_pela_storage_que_o_guardou(self) -> None:
        """A pasta dos retratos não é a das capas, e apagar pela errada é apagar em vão.

        O `default_storage` é o backend das capas. O retrato vive em `echilo/perfis`,
        e apagar pelo `default_storage` só acertava porque o `destroy` da Cloudinary
        leva o `public_id` com a pasta dentro. Um `MEDIA_PERFIS_BACKEND` apontado
        para outra conta apaga na conta errada, e o `except` engolia a resposta.
        """
        self._guarda(self._grande())
        antigo = self.user.photo.name

        apagados: list[str] = []
        storage_do_campo = User._meta.get_field("photo").storage
        with mock.patch.object(storage_do_campo, "delete", side_effect=apagados.append):
            self._guarda(self._grande())

        self.assertEqual(apagados, [antigo], "o retrato anterior não foi apagado")

    def test_apagar_o_retrato_não_passa_pela_storage_das_capas(self) -> None:
        """O `default_storage` não é a storage do campo, e o teste diz isso."""
        self._guarda(self._grande())

        with mock.patch.object(default_storage, "delete") as errado:
            self._guarda(self._grande())

        errado.assert_not_called()

    def test_uma_fotografia_de_telemóvel_é_aceite(self) -> None:
        """O lado deixou de ser motivo de recusa: reduz-se, e recusa-se por escrever.

        Uma fotografia de 2600 px é uma boa fotografia. O código antigo recusava-a
        com «tem mais de 2000 px de lado», e a pessoa ficava sem retrato por causa
        da câmara, não por causa do retrato.
        """
        self._guarda(self._grande())

        self.assertTrue(self.user.photo)

    def test_o_que_fica_guardado_e_quadrado_e_pequeno(self) -> None:
        """É o que o cabeçalho mostra a 28 px, e cabe no orçamento que o browser recebeu.

        A comparação que interessa não é com um rácio bonito, é com o número que o
        `data-orcamento-mb` vai dizer à pessoa: o que fica guardado tem de caber
        nesse meio megabyte, senão o browser reduziu e o servidor não.
        """
        entrada = self._grande()
        tamanho_entrada = len(entrada.file.getvalue())
        self._guarda(entrada)

        with Image.open(self.user.photo.path) as guardada:
            self.assertEqual(guardada.size, (LADO_RETRATO_PX, LADO_RETRATO_PX))
            self.assertEqual(guardada.format, "JPEG")
            self.assertEqual(guardada.mode, "RGB")
        self.assertGreater(tamanho_entrada, 1_000_000, "o retrato de teste não é grande")
        self.assertLess(
            os.path.getsize(self.user.photo.path),
            int(ORCAMENTO_PERFIL_MB * 1024 * 1024),
            "o que ficou guardado não cabe no orçamento que o redutor recebeu",
        )
    def test_o_nome_guardado_diz_jpeg(self) -> None:
        """A storage procura o ficheiro pelo nome, e um PNG com bytes JPEG não abre."""
        self._guarda(self._grande())

        self.assertTrue(self.user.photo.name.endswith(".jpg"))

    def test_um_retrato_pequeno_não_e_ampliado(self) -> None:
        """Ampliar não traz a fotografia que não está lá, só a mesma interpolação.

        Este teste afirmava `(200, 200)` para uma entrada de 200×100, e por isso
        fixava o defeito em vez de o travar: `ImageOps.fit` cobre o quadrado pedido
        e por isso amplia. A propriedade que interessa é que nenhuma dimensão
        ultrapasse a que a fotografia tinha.
        """
        buffer = io.BytesIO()
        Image.new("RGB", (200, 100), (10, 20, 30)).save(buffer, "JPEG")
        self._guarda(SimpleUploadedFile("pequeno.jpg", buffer.getvalue(), "image/jpeg"))

        with Image.open(self.user.photo.path) as guardada:
            self.assertEqual(guardada.size, (100, 100), "ampliar o lado curto não devolve a fotografia")
            self.assertLessEqual(guardada.size[0], 200)
            self.assertLessEqual(guardada.size[1], 100)

    def test_nenhum_retrato_sai_maior_do_que_entrou(self) -> None:
        """A propriedade vale para todas as formas, e não para uma só."""
        for entrada, esperado in (((4000, 3000), (512, 512)), ((600, 600), (512, 512))):
            with self.subTest(entrada=entrada):
                buffer = io.BytesIO()
                Image.new("RGB", entrada, (10, 20, 30)).save(buffer, "JPEG")
                self._guarda(SimpleUploadedFile("x.jpg", buffer.getvalue(), "image/jpeg"))

                with Image.open(self.user.photo.path) as guardada:
                    self.assertEqual(guardada.size, esperado)
                    self.assertLessEqual(max(guardada.size), max(entrada))

    def test_a_orientação_da_câmara_é_corrigida(self) -> None:
        """Um retrato deitado de lado é deitado pelo EXIF, e ninguém o endireita."""
        buffer = io.BytesIO()
        imagem = Image.new("RGB", (3000, 2000), (200, 40, 40))
        # A orientação 6 é "rodar 90° para a direita", que é o que a câmara de um
        # telemóvel deitada escreve. A imagem guarda-se deitada para a orientação
        # ter efeito: é assim que a câmara a produz, e não o contrário.
        exif = imagem.getexif()
        exif[0x0112] = 6
        imagem.save(buffer, "JPEG", exif=exif)

        self._guarda(SimpleUploadedFile("deitado.jpg", buffer.getvalue(), "image/jpeg"))

        with Image.open(self.user.photo.path) as guardada:
            largura, altura = guardada.size
            self.assertEqual(largura, altura, "o retrato tem de sair quadrado depois do EXIF")

    def test_a_pasta_dos_retratos_não_é_a_das_capas(self) -> None:
        """Sem pasta própria, um retrato vai para `echilo/imoveis` e segue as capas."""
        with APENAS_NUVEM:
            storage = CloudinaryAvatarStorage()

            self.assertEqual(storage.pasta, "echilo/perfis")
            self.assertEqual(storage.transformacao["crop"], "fill")
            self.assertEqual(storage.transformacao["gravity"], "face")
            self.assertEqual(storage.transformacao["width"], LADO_RETRATO_PX)
            self.assertEqual(storage.transformacao["height"], LADO_RETRATO_PX)

    def test_a_transparência_vira_branco_e_não_preto(self) -> None:
        """O JPEG não tem alfa, e o que era transparente saía preto sem o fundo."""
        buffer = io.BytesIO()
        Image.new("RGBA", (800, 800), (0, 0, 0, 0)).save(buffer, "PNG")
        self._guarda(SimpleUploadedFile("recorte.png", buffer.getvalue(), "image/png"))

        with Image.open(self.user.photo.path) as guardada:
            self.assertEqual(guardada.mode, "RGB")
            # `getextrema` devolve uma faixa por canal, e é o mínimo de todas que
            # diz se a imagem ficou preta.
            mais_escuro = min(pior for pior, _ in guardada.getextrema())
            self.assertGreater(mais_escuro, 200)

    def test_o_formulário_publica_o_orçamento_do_retrato(self) -> None:
        """O `data-` é o contrato entre o Python e o redutor do browser.

        Sem o `data-orcamento-mb` não há redução nenhuma, e um retrato de 4 MB vai
        num pedido que a plataforma recusa: o mesmo 413 que as fotografias dos
        imóveis já tiveram, num caminho que ninguém tinha reparado.
        """
        config = config_perfil()
        html = self.client.get(self.url).content.decode("utf-8")
        form = re.search(r"<form[^>]*settings-form.*?>", html, re.DOTALL)
        self.assertIsNotNone(form, "o formulário do perfil não tem settings-form")
        atributos = form.group(0)  # type: ignore[union-attr]

        self.assertIn(f'data-orcamento-mb="{config["orcamento_perfil_mb"]}"', atributos)
        self.assertIn(
            f'data-lado-maximo="{config["lado_maximo_perfil_cliente"]}"', atributos
        )
        # O redutor procura `images` e este campo chama-se `photo`: sem o nome, o
        # JavaScript liga-se ao input errado e não reduz nada.
        self.assertIn('data-input-ficheiros="photo"', atributos)
        # §2.13: um número que viaja sai invariante. Em `pt-AO`, `0.5` escreve-se
        # `0,5`, e `Number("0,5")` é `NaN` — um redutor com orçamento `NaN`
        # compara com `NaN` e nunca reduz. A fotografia ia crua, e o `413` que
        # estes números existem para evitar voltava, em silêncio e sem erro.
        self.assertNotIn(",", atributos, "vírgula decimal num atributo lido por Number()")
        self.assertIn(".", atributos)

    def test_o_orçamento_do_retrato_cabe_no_pedido(self) -> None:
        """O meio megabyte é o número que evita o 413, e vale a pena escrevê-lo."""
        self.assertLess(ORCAMENTO_PERFIL_MB, LIMITE_FOTO_PERFIL_MB)
        self.assertEqual(
            config_perfil()["lado_maximo_perfil_cliente"],
            LADO_RETRATO_PX,
            "o browser e o servidor têm de reduzir para o mesmo lado",
        )

    def test_o_backend_de_retratos_segue_a_setting(self) -> None:
        """O campo declara a sua storage, e a storage é a que as settings escolheram."""
        with APENAS_NUVEM:
            self.assertIsInstance(storage_perfis(), CloudinaryAvatarStorage)

    def test_a_storage_do_retrato_vem_da_setting(self) -> None:
        """O campo não repete a escolha: pede-a a `storage_perfis`, como os documentos.

        A `FileField` chama a sua `storage` **uma vez**, ao definir o campo. Sem
        isso, um retrato em produção acabaria na pasta das capas — que é o
        `default` — e ninguém veria o erro: a imagem aparecia na mesma.
        """
        campo = User._meta.get_field("photo")
        deconstruido = campo.deconstruct()

        self.assertIs(
            deconstruido[3].get("storage"),
            storage_perfis,
            "o campo tem de pedir a storage à função, e não guardá-la",
        )
        self.assertEqual(campo.storage.__class__.__name__, "FileSystemStorage")

    def test_a_migration_que_muda_o_retrato_leva_a_referencia(self) -> None:
        """A migration é o que dá a storage certa ao ambiente que a vai aplicar.

        O `MEDIA_PERFIS_BACKEND` é lido no arranque, e a base de produção aplica a
        migration antes do primeiro pedido. Se a migration guardar a *instância* em
        vez da referência, é o disco de quem a aplicou que fica no estado da base
        de dados, e a Cloudinary nunca chega a ser usada.
        """
        caminho = Path(__file__).resolve().parent / "migrations" / "0005_alter_user_photo.py"

        self.assertIn("apps.core.storage.storage_perfis", caminho.read_text("utf-8"))

    def test_sem_a_nuvem_o_retrato_vai_para_disco(self) -> None:
        """Em desenvolvimento não há Cloudinary, e o retrato tem de ficar guardável.

        O caminho inverso também é um caminho: um backend configurado que não
        resolve devolve um disco, e `storage_documentacao` — que é o mesmo padrão
        — escolhe o disco quando a setting não aponta para a nuvem. O que não pode
        é rebentar no arranque de uma máquina de desenvolvimento.
        """
        with override_settings(MEDIA_PERFIS_BACKEND="apps.core.files.StorageNaoExiste"):
            self.assertIsInstance(storage_perfis(), FileSystemStorage)


class AccountAdminTests(RateLimitFreeTestCase):
    """A administração cadastra membros da equipa e clientes (§3)."""

    def setUp(self) -> None:
        super().setUp()
        self.admin = make_user(role=User.Role.ADMIN, email="admin@echilo.ao")
        self.curator = make_user(role=User.Role.CURATOR, email="curador@echilo.ao")
        self.agent = make_user(role=User.Role.AGENT, email="agente@echilo.ao")
        self.cliente = make_user(email="cliente@echilo.ao")

    def test_as_paginas_de_contas_recusam_visitante_cliente_e_agente(self) -> None:
        """Só o administrador passa. As três restantes respostas são 403.

        A guarda está na vista e não só no menu (§3): esconder o link a quem não
        pode é cortesia, e quem escreve o endereço à mão não é cortesia.
        """
        paginas = [
            reverse("accounts:team_list"),
            reverse("accounts:team_member_create"),
            reverse("accounts:client_list"),
            reverse("accounts:client_create"),
        ]
        for url in paginas:
            with self.subTest(url=url, quem="visitante"):
                self.assertEqual(self.client.get(url).status_code, 403)
            for quem, user in (("cliente", self.cliente), ("agente", self.agent)):
                self.client.force_login(user)
                with self.subTest(url=url, quem=quem):
                    self.assertEqual(self.client.get(url).status_code, 403)
                self.client.logout()

    def test_o_administrador_abre_as_paginas_de_contas(self) -> None:
        """As quatro páginas abrem para quem tem `can_manage_users`."""
        self.client.force_login(self.admin)

        for url in (
            reverse("accounts:team_list"),
            reverse("accounts:team_member_create"),
            reverse("accounts:client_list"),
            reverse("accounts:client_create"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_lista_da_equipa_traz_a_equipa_e_nao_os_clientes(self) -> None:
        """Duas listas, uma pergunta cada uma. Misturar as duas não responde a nenhuma."""
        self.client.force_login(self.admin)

        response = self.client.get(reverse("accounts:team_list"))

        self.assertContains(response, "curador@echilo.ao")
        self.assertContains(response, "agente@echilo.ao")
        self.assertNotContains(response, "cliente@echilo.ao")

    def test_a_lista_de_clientes_mostra_seis_de_seis(self) -> None:
        """A lista de clientes pagina de seis em seis, como todas as outras."""
        # O `setUp` já deixou um cliente, e seis novos dão os sete que fazem duas
        # páginas. Somar `PAGINA_PADRAO + 1` ao que já lá está dava oito e a
        # segunda página vinha com dois, que também é correcto e não testa nada.
        for indice in range(PAGINA_PADRAO):
            make_user(email=f"cliente{indice:02d}@exemplo.ao")

        self.client.force_login(self.admin)
        primeira = self.client.get(reverse("accounts:client_list"))
        segunda = self.client.get(reverse("accounts:client_list"), {"page": 2})

        self.assertEqual(len(primeira.context["clientes"]), PAGINA_PADRAO)
        self.assertEqual(len(segunda.context["clientes"]), 1)
        self.assertEqual(primeira.context["page_obj"].paginator.num_pages, 2)

    def test_a_lista_da_equipa_mostra_seis_de_seis(self) -> None:
        """A lista da equipa pagina com a mesma medida, sem filtro nenhum."""
        # O `setUp` deixou três membros — administrador, curador e agente —, e
        # quatro curadores novo chegam aos sete.
        for indice in range(4):
            make_user(role=User.Role.CURATOR, email=f"extra{indice:02d}@exemplo.ao")

        self.client.force_login(self.admin)
        primeira = self.client.get(reverse("accounts:team_list"))
        segunda = self.client.get(reverse("accounts:team_list"), {"page": 2})

        self.assertEqual(len(primeira.context["membros"]), PAGINA_PADRAO)
        self.assertEqual(len(segunda.context["membros"]), 1)
        # A caixa de navegação não depende de nenhum filtro, por isso o `include`
        # passa só o rótulo: é a prova de que uma chave de filtro em falta não
        # parte a caixa.
        self.assertContains(primeira, "Paginação da equipa")
        self.assertContains(primeira, "page=2")

    def test_a_pagina_dos_clientes_guarda_a_pesquisa(self) -> None:
        """Mudar de página a perder a pesquisa é devolver a lista de surpresa."""
        for indice in range(PAGINA_PADRAO + 1):
            make_user(email=f"procurado{indice:02d}@exemplo.ao")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:client_list"), {"q": "procurado"})

        # Sete clientes com o mesmo nome de família e nenhum outro: o paginador
        # diz sete e a página dá seis, e a pesquisa sobrevive ao link.
        self.assertEqual(response.context["page_obj"].paginator.count, PAGINA_PADRAO + 1)
        self.assertEqual(len(response.context["clientes"]), PAGINA_PADRAO)
        self.assertContains(response, "q=procurado")
        self.assertContains(response, "page=2")

    def test_a_lista_de_clientes_traz_os_clientes_e_nao_a_equipa(self) -> None:
        """O inverso do teste anterior, pelo mesmo motivo."""
        self.client.force_login(self.admin)

        response = self.client.get(reverse("accounts:client_list"))

        self.assertContains(response, "cliente@echilo.ao")
        self.assertNotContains(response, "curador@echilo.ao")

    def test_a_lista_de_clientes_procura_por_nif_e_por_nome(self) -> None:
        """A pesquisa responde ao que a equipa tem à mão: o NIF ou o nome."""
        make_user(email="maria@exemplo.ao", full_name="Maria Ferreira", nif="004321987HA044")
        self.client.force_login(self.admin)
        url = reverse("accounts:client_list")

        por_nif = self.client.get(url + "?q=004321987HA044")
        por_nome = self.client.get(url + "?q=Maria")
        sem_acerto = self.client.get(url + "?q=ninguem")

        self.assertContains(por_nif, "maria@exemplo.ao")
        self.assertNotContains(por_nif, "cliente@echilo.ao")
        self.assertContains(por_nome, "maria@exemplo.ao")
        self.assertNotContains(sem_acerto, "cliente@echilo.ao")

    def test_a_lista_de_clientes_repete_a_pesquisa_no_campo(self) -> None:
        """O campo vazio depois de uma pesquisa obriga a escrever tudo outra vez."""
        self.client.force_login(self.admin)

        response = self.client.get(reverse("accounts:client_list") + "?q=Maria")

        self.assertContains(response, 'value="Maria"')

    def test_cadastrar_membro_cria_a_conta_com_o_perfil_escolhido(self) -> None:
        """O perfil vem do formulário, e a palavra-passe é a que foi escrita."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Bruno Mateus",
                "email": "novo.agente@echilo.ao",
                "phone": "+244 912 345 678",
                "role": User.Role.AGENT,
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], reverse("accounts:team_list"))
        user = User.objects.get(email="novo.agente@echilo.ao")
        self.assertEqual(user.role, User.Role.AGENT)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertTrue(user.is_team_member)
        self.assertFalse(user.can_manage_users)

    def test_cadastrar_membro_normaliza_o_email(self) -> None:
        """Com maiúsculas e espaços à volta, o e-mail é o mesmo e não duplica."""
        self.client.force_login(self.admin)

        self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Carla Bengui",
                "email": "  Carla@Echilo.AO ",
                "role": User.Role.CURATOR,
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )

        self.assertEqual(User.objects.filter(email="carla@echilo.ao").count(), 1)

    def test_cadastrar_membro_recusa_email_ja_existente(self) -> None:
        """A conta já existe: o cadastro devolve o formulário, não uma segunda conta."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Outro Curador",
                "email": "CURADOR@echilo.ao",
                "role": User.Role.CURATOR,
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertFormError(
            resposta.context["form"],
            "email",
            "Já existe uma conta com este e-mail.",
        )
        self.assertEqual(User.objects.filter(email="curador@echilo.ao").count(), 1)

    def test_cadastrar_membro_recusa_perfil_de_cliente(self) -> None:
        """`CLIENT` não é uma opção: a equipa não se promove a si própria por engano."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Falso Curador",
                "email": "falso@echilo.ao",
                "role": User.Role.CLIENT,
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("role", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(email="falso@echilo.ao").exists())

    def test_cadastrar_membro_recusa_palavras_passe_diferentes(self) -> None:
        """A confirmação não é decoração."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Ana Quissanga",
                "email": "ana.admin@echilo.ao",
                "role": User.Role.ADMIN,
                "password": PASSWORD,
                "password_confirm": "Outra-coisa-2026",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("password_confirm", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(email="ana.admin@echilo.ao").exists())

    def test_cadastrar_membro_recusa_password_fraca(self) -> None:
        """A equipa entra com contas de cliente: a palavra-passe fraca não passa."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:team_member_create"),
            {
                "full_name": "Curador Fraco",
                "email": "fraco@echilo.ao",
                "role": User.Role.CURATOR,
                "password": "abc",
                "password_confirm": "abc",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("password", resposta.context["form"].errors)

    def test_cadastrar_cliente_cria_a_conta_com_a_identidade_do_formulario_publico(self) -> None:
        """O cadastro de cliente não pede os termos, e pede tudo o resto."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:client_create"),
            registration_payload(email="cliente.novo@exemplo.ao"),
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], reverse("accounts:client_list"))
        user = User.objects.get(email="cliente.novo@exemplo.ao")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertEqual(user.nif, "009671373HA093")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.can_curate)

    def test_cadastrar_cliente_ainda_exige_a_identidade(self) -> None:
        """Tirar a caixa dos termos não tira a data de nascimento nem o NIF."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:client_create"),
            registration_payload(email="sem.nif@exemplo.ao", nif=""),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("nif", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(email="sem.nif@exemplo.ao").exists())

    def test_cadastrar_cliente_recusa_menor_de_idade(self) -> None:
        """A regra dos 18 anos é a mesma na página pública e na de administração."""
        self.client.force_login(self.admin)

        resposta = self.client.post(
            reverse("accounts:client_create"),
            registration_payload(email="menor@exemplo.ao", date_of_birth="2015-01-01"),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("date_of_birth", resposta.context["form"].errors)
        self.assertFalse(User.objects.filter(email="menor@exemplo.ao").exists())

    def test_a_pagina_de_clientes_nao_tem_a_caixa_dos_termos(self) -> None:
        """A caixa dos termos é do registo público. Aqui não há nada a aceitar."""
        self.client.force_login(self.admin)

        html = self.client.get(reverse("accounts:client_create")).content.decode()

        self.assertNotIn('name="terms_accepted"', html)
        self.assertIn('name="nif"', html)
