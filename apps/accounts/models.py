"""Utilizador do Echilo com perfil de acesso (§3 do steering)."""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.validators import validate_adult, validate_angolan_phone, validate_nif
from apps.properties.reference import ANGOLA_PROVINCES

# Largura do retrato no cabeçalho, com folga para ecrãs de densidade alta. Vive
# aqui e não no template porque o mesmo número decide a versão pedida à storage
# e o que o teste mede; nos dois sítios, uma das medidas fica errada sem erro.
AVATAR_LARGURA_PX = 96


class User(AbstractUser):
    """Cliente ou membro da equipa, distinguido pelo campo `role`."""

    class Role(models.TextChoices):
        """Perfis de acesso, do mais restrito ao mais amplo."""

        CLIENT = "CLIENT", "Cliente"
        CURATOR = "CURATOR", "Curador"
        AGENT = "AGENT", "Agente"
        ADMIN = "ADMIN", "Administrador"

    class IdDocumentType(models.TextChoices):
        """Documento de identidade aceite para ancorar a conta a uma pessoa."""

        BI = "BI", "Bilhete de identidade"
        PASSPORT = "PASSPORT", "Passaporte"

    class Gender(models.TextChoices):
        """Género declarado pelo titular; inclui a recusa em responder."""

        MASCULINO = "M", "Masculino"
        FEMININO = "F", "Feminino"
        OUTRO = "O", "Outro"
        NAO_DIZER = "N", "Prefiro não dizer"

    email = models.EmailField("endereço de e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=150)
    phone = models.CharField(
        "telefone",
        max_length=20,
        blank=True,
        validators=[validate_angolan_phone],
    )
    nif = models.CharField(
        "NIF",
        max_length=14,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_nif],
        help_text="9 dígitos, 2 letras e 3 dígitos. Ex.: 009671373HA093",
    )
    date_of_birth = models.DateField(
        "data de nascimento",
        null=True,
        blank=True,
        validators=[validate_adult],
    )
    id_document_type = models.CharField(
        "tipo de documento",
        max_length=12,
        choices=IdDocumentType.choices,
        blank=True,
        default="",
    )
    id_document_number = models.CharField(
        "número do documento",
        max_length=30,
        blank=True,
        default="",
    )
    province = models.CharField(
        "província de residência",
        max_length=20,
        choices=ANGOLA_PROVINCES,
        blank=True,
        default="",
    )
    gender = models.CharField(
        "género",
        max_length=4,
        choices=Gender.choices,
        blank=True,
        default="",
    )
    role = models.CharField(
        "perfil",
        max_length=10,
        choices=Role.choices,
        default=Role.CLIENT,
        db_index=True,
    )
    photo = models.ImageField(
        "fotografia de perfil",
        upload_to="perfis/%Y/%m/",
        null=True,
        blank=True,
    )
    is_team_member = models.BooleanField("membro da equipa", default=False, editable=False)
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "utilizador"
        verbose_name_plural = "utilizadores"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.email})"

    def save(self, *args: object, **kwargs: object) -> None:
        """Deriva a isenção de equipa e o acesso staff a partir do perfil."""
        self.is_team_member = self.role != self.Role.CLIENT
        if not self.is_team_member:
            # Um cliente nunca acede ao painel interno (§3).
            self.is_staff = False
            self.is_superuser = False
        elif self.role in {self.Role.AGENT, self.Role.ADMIN}:
            self.is_staff = True
        if not self.username:
            # `username` vem de AbstractUser mas o login é por e-mail; a coluna
            # única tem de espelhar o e-mail para não colidir entre utilizadores.
            self.username = self.email
        super().save(*args, **kwargs)

    @property
    def initials(self) -> str:
        """Devolve as iniciais usadas no avatar da interface.

        É o que fica no cabeçalho enquanto não há fotografia. Um avatar com
        as iniciais não é uma falha: é a identidade de quem entrou, e só a
        fotografia a refine. Quando há foto, é a foto que o browser pede.
        """
        parts = [part for part in self.full_name.split() if part]
        if not parts:
            return "EC"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return f"{parts[0][0]}{parts[-1][0]}".upper()

    @property
    def avatar_url(self) -> str:
        """Endereço do retrato, ou string vazia quando não há fotografia.

        O cabeçalho pede este endereço em todas as páginas, e um retrato mostrado
        a 28 px não deve pesar como a capa de um imóvel. A storage sabe
        derivar uma versão estreita sem novo ficheiro; quando é um disco local
        não sabe, e o endereço é o do ficheiro inteiro.
        """
        if not self.photo:
            return ""
        derivada = getattr(self.photo.storage, "url_derivada", None)
        if derivada is None:
            return self.photo.url
        return derivada(self.photo.name, largura=AVATAR_LARGURA_PX)

    @property
    def can_curate(self) -> bool:
        """Diz se o utilizador pode registar e editar imóveis."""
        return self.role in {self.Role.CURATOR, self.Role.AGENT, self.Role.ADMIN}

    @property
    def can_validate(self) -> bool:
        """Diz se o utilizador pode validar documentos e aprovar publicação."""
        return self.role in {self.Role.AGENT, self.Role.ADMIN}

    @property
    def can_manage_users(self) -> bool:
        """Diz se o utilizador pode criar contas de equipa e de clientes.

        Só o administrador. Um curador que registe outro curador é uma segunda
        chefia sem ninguém a nomear, e o menu tem de dizer quem cria contas — não
        pode ser "todos os que estão na equipa".
        """
        return self.role == self.Role.ADMIN


@receiver(post_save, sender=User)
def normalise_email(sender: type[User], instance: User, **kwargs: object) -> None:
    """Garante que o e-mail fica sempre em minúsculas para evitar duplicados."""
    if instance.email and instance.email != instance.email.lower():
        User.objects.filter(pk=instance.pk).update(email=instance.email.lower())
        instance.email = instance.email.lower()
