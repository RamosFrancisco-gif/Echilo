"""Formulários de cadastro, entrada e recuperação de senha."""

from __future__ import annotations

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import forms as auth_forms
from django.contrib.auth.forms import PasswordResetForm as DjangoPasswordResetForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseStyledForm, BaseStyledModelForm
from apps.core.images import motivo_recusa_imagem
from apps.core.storage import MIME_IMAGEM_ACEITE
from apps.core.validators import (
    normalize_nif,
    validate_adult,
    validate_angolan_phone,
    validate_nif,
)
from apps.properties.reference import ANGOLA_PROVINCES

from .services import PASSWORD_RESET_NEUTRAL_MESSAGE, validate_password_strength

User = get_user_model()

# Um retrato é uma imagem pequena mostrada a 40 px em todas as páginas. O tecto é
# o do envio de ficheiros do §7 e vive aqui, e não no texto do `help_text` que o
# repete: um tecto escrito em dois sítios é duas respostas à mesma pergunta.
LIMITE_FOTO_PERFIL_MB = 5
LADO_MAXIMO_FOTO_PERFIL = 2000


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


class ProfileForm(BaseStyledModelForm):
    """Edição de tudo o que a conta guarda, menos o perfil de acesso.

    Edita-se o nome, o e-mail por onde se entra, o telefone por onde a equipa
    liga, a província, o género, a fotografia do cabeçalho e a identidade
    — NIF, data de nascimento e documento.

    A identidade é editável por decisão do produto, e a consequência fica escrita
    no campo em vez de escondida: o §2.11 ancora a conta a uma pessoa real e a
    equipa usa o NIF e o documento para a reconhecer antes de tratar de um
    pedido. Um NIF corrigido é um NIF que deixa de bater certo com o registo que
    a equipa já viu, e por isso a ajuda do campo manda avisar a equipa. O que
    fica de fora é o `role`, e não por esquecimento: o §3 diz que a promoção de
    perfil é feita no painel, e um campo que deixa a pessoa escrever `ADMIN` no
    próprio perfil é uma escalada de privilégio com caixa de texto.
    """

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text="É por este endereço que entra na conta.",
    )
    phone = forms.CharField(
        label="Telefone",
        max_length=20,
        required=False,
        validators=[validate_angolan_phone],
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
        help_text="A equipa usa este número para marcar visitas.",
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
    nif = forms.CharField(
        label="NIF",
        required=False,
        # Sem `max_length`: um NIF colado com espaços passa dos 14 caracteres e
        # seria recusado antes de a normalização o limpiar. O formato decide.
        validators=[validate_nif],
        widget=forms.TextInput(attrs={"placeholder": "009671373HA093", "inputmode": "text"}),
        help_text=(
            "Número de identificação fiscal: 9 dígitos, 2 letras e 3 dígitos. "
            "A equipa confirma-o contra o seu documento, por isso se o alterar "
            "avise-a."
        ),
    )
    date_of_birth = forms.DateField(
        label="Data de nascimento",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        validators=[validate_adult],
        widget=forms.DateInput(attrs={"type": "date", "autocomplete": "bday"}, format="%Y-%m-%d"),
        help_text="É preciso ter pelo menos 18 anos para ter conta.",
    )
    id_document_type = forms.ChoiceField(
        label="Tipo de documento",
        required=False,
        choices=[("", "Escolher…")] + list(User.IdDocumentType.choices),
        widget=forms.Select(attrs={"autocomplete": "off"}),
    )
    id_document_number = forms.CharField(
        label="Número do documento",
        required=False,
        max_length=30,
        widget=forms.TextInput(attrs={"placeholder": "003456789LA041"}),
    )
    photo = forms.ImageField(
        label="Fotografia",
        required=False,
        widget=forms.FileInput(attrs={"accept": MIME_IMAGEM_ACEITE}),
        help_text=(
            f"JPEG, PNG ou WebP, até {LIMITE_FOTO_PERFIL_MB} MB. "
            "Aparece no lugar das iniciais no cabeçalho."
        ),
    )
    remover_foto = forms.BooleanField(
        label="Remover a fotografia e voltar às iniciais",
        required=False,
        # A caixa de selecção vai pela mesma confirmação das acções destrutivas.
        # O ficheiro vai com a conta e a fotografia é a única que o titular
        # tem; os atributos vivem no formulário porque é lá que se sabe que o
        # campo apaga alguma coisa, e o partial de campo não os sabe.
        widget=forms.CheckboxInput(
            attrs={
                "data-confirmar": (
                    "Remover a fotografia? O ficheiro é apagado e voltas às iniciais."
                ),
                "data-confirmar-titulo": "Remover fotografia",
                "data-confirmar-ok": "Remover",
            }
        ),
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.foto_recusada: str | None = None
        super().__init__(*args, **kwargs)

    class Meta:
        model = User
        # `role` e `is_staff` ficam de fora por §3, e a fotografia é o único campo
        # que não é um dado. `date_of_birth` e `nif` são opcionais porque o modelo
        # os permite vazios: a equipa não é registrada com NIF (§3.2).
        fields = [
            "full_name",
            "email",
            "phone",
            "province",
            "gender",
            "nif",
            "date_of_birth",
            "id_document_type",
            "id_document_number",
            "photo",
        ]

    def clean_email(self) -> str:
        """Normaliza o e-mail e recusa a conta que o usa."""
        email = str(self.cleaned_data["email"]).strip().lower()
        if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean_nif(self) -> str | None:
        """Normaliza o NIF, deixa-o vazio e recusa o de outra conta.

        Vazio é `None` e não `""`: a coluna é `unique`, e duas contas de equipa
        sem NIF com a string vazia colidem na base de dados. É por isso que o
        registo de equipa, que não pede NIF (§3.2), grava `None`.
        """
        em_branco = str(self.cleaned_data.get("nif", "")).strip()
        if not em_branco:
            return None
        nif = normalize_nif(em_branco)
        if User.objects.filter(nif=nif).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Já existe uma conta registada com este NIF.")
        return nif

    def _novo_retrato(self) -> object | None:
        """Devolve o ficheiro que a pessoa escolheu agora, se escolheu algum.

        Não pode ser lido de `cleaned_data["photo"]`. Numa conta que já tem
        retrato, um pedido sem novo ficheiro devolve o retrato que já lá está — o
        `ImageField` devolve o `FieldFile` existente — e validar isso é tentar abrir
        uma fotografia que a pessoa não escolheu. Pior: com a caixa de remover
        marcada, "já tem fotografia" e "escolheu uma nova" são a mesma leitura, e
        o formulário dizia que a pessoa se tinha contradito quando não se
        tinha. A resposta à pergunta é o que chegou em `self.files`.
        """
        return self.files.get(self.add_prefix("photo"))

    def clean_photo(self) -> object | None:
        """Valida o retrato escolhido agora com Pillow, e não pelo cabeçalho.

        O `ImageField` aceita um executável renomeado a `.jpg`, e o retrato é a
        imagem que o browser vai buscar em todas as páginas depois disso.
        """
        if self._novo_retrato() is None:
            return self.cleaned_data.get("photo")
        motivo = motivo_recusa_imagem(
            self._novo_retrato(),
            limite_mb=LIMITE_FOTO_PERFIL_MB,
            lado_maximo=LADO_MAXIMO_FOTO_PERFIL,
        )
        if motivo is not None:
            self.foto_recusada = motivo
            raise ValidationError(motivo)
        return self.cleaned_data.get("photo")

    def clean(self) -> dict[str, object]:
        """Recusa trocar e remover ao mesmo tempo, que é uma intenção só.

        Sem esta regra o `save()` apagava a fotografia que existia e o `remover`
        apagava a que acabava de subir, ou o inverso, conforme a ordem em que as
        linhas fossem escritas. A pessoa pediu uma coisa ou a outra.
        """
        cleaned = super().clean()
        if cleaned.get("remover_foto") and self._novo_retrato() is not None:
            self.add_error(
                "remover_foto",
                "Já escolheu uma fotografia nova. Deixe a caixa por marcar para a manter.",
            )
        return cleaned

    def save(self, commit: bool = True) -> User:
        """Guarda os dados e resolve a fotografia: nova, removida ou como estava.

        O caminho antigo é lido de `self.initial` e não da instância, porque a
        validação já o substituiu. `is_valid()` chama `construct_instance()`, que
        coloca o ficheiro novo no lugar do velho no mesmo objecto; lido depois,
        `user.photo` é a fotografia que subiu, a comparação dá igual e o retrato
        trocado fica no storage a ser pago todos os meses por alguém que já não
        existe. O `initial` é a fotografia que a página mostrou, e essa não muda.

        O ficheiro só é apagado depois de a conta estar guardada, e nunca se a
        escrita falhar — uma conta sem retrato é um erro visível, um ficheiro
        órfão é uma factura.
        """
        anterior = self.initial.get("photo")
        antigo = str(getattr(anterior, "name", "") or "")
        user = super().save(commit=False)
        if self.cleaned_data.get("remover_foto"):
            user.photo = None
        if commit:
            user.save()
            actual = user.photo.name if user.photo else ""
            if antigo and antigo != actual:
                self._apagar_ficheiro(antigo)
        return user

    @staticmethod
    def _apagar_ficheiro(nome: str) -> None:
        """Apaga o retrato que ficou para trás, sem tocar em mais nada."""
        from django.core.files.storage import default_storage

        try:
            default_storage.delete(nome)
        except Exception:  # pragma: no cover - limpar nunca é caminho crítico
            pass


class PasswordChangeForm(BaseStyledForm, auth_forms.PasswordChangeForm):
    """Troca da palavra-passe a partir da sessão, exigindo a de agora.

    Herda do formulário do Django, que é o que sabe o que fazer com uma conta
    sem palavra-passe utilizável e quem confere se as duas palavras coincidem. O
    que entra à mão são os rótulos em português e a regra do projecto — a
    palavra-passe não pode repetir o nome nem o e-mail do titular — que o
    `SetPasswordForm` também aplica. Duas formas de trocar a palavra-passe com
    regras diferentes é a primeira delas a ser frouxa.

    A palavra-passe de agora é obrigatória e é o que separa isto de um pedido de
    escrita cego. Sem ela, quem encontra uma sessão aberta — um computador
    partilhado, um link deixado aberto — troca a palavra-passe e fica com a conta.
    """

    old_password = forms.CharField(
        label="Palavra-passe actual",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "A que está a usar agora", "autocomplete": "current-password"}
        ),
        help_text="É a que está a usar agora. Sem ela, quem encontrasse a sessão aberta trocava-lhe a palavra-passe.",
    )
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

    error_messages = {
        # O `PasswordChangeForm` do Django junta as suas ao `SetPasswordForm` e
        # acrescenta `password_incorrect`. Sobrescrever o dicionário sem o
        # spreads apaga `password_incorrect` e a senha errada deixa de dar
        # mensagem: um `KeyError` na página de quem errou a própria senha.
        **auth_forms.PasswordChangeForm.error_messages,
        "password_mismatch": _("As palavras-passe não coincidem."),
        "password_in_help": _("Use pelo menos 8 caracteres."),
        "password_incorrect": _("A palavra-passe actual está incorrecta. Digite-a novamente."),
    }

    def __init__(self, user: User | None = None, *args: object, **kwargs: object) -> None:
        """Recebe a conta como argumento, e não por atributo, como o `SetPasswordForm`."""
        super().__init__(user, *args, **kwargs)  # type: ignore[arg-type]

    def clean(self) -> dict[str, object]:
        """Confirma a nova e recusa a que reproduz o nome ou o e-mail."""
        cleaned = super().clean()
        nova = cleaned.get("new_password1")
        if nova:
            validate_password_strength(
                str(nova),
                full_name=str(getattr(self.user, "full_name", "")),
                email=str(getattr(self.user, "email", "")),
            )
        return cleaned

    def save(self, commit: bool = True) -> User:
        """Grava a nova palavra-passe. A sessão que a trocou continua válida."""
        user = self.user
        user.set_password(str(self.cleaned_data["new_password1"]))
        if commit:
            user.save(update_fields=["password"])
        return user


class ClientCreateForm(ClientRegistrationForm):
    """Cadastro de cliente feito pela administração, sem termo de aceitação.

    O formulário público exige que o próprio carregue na caixa dos termos. Aqui
    quem responde por isso é a administração, que está a identificar a pessoa à
    vista (§2.11); pedir-lhe que aceite termos em nome de outro seria um atalho
    para uma conta que ninguém confirmou.

    Tudo o resto é o formulário público, campo a campo. A data de nascimento, o
    NIF e o documento continuam obrigatórios, porque a identidade de um cliente
    não fica menos válida por ter sido registada por outra pessoa.
    """

    terms_accepted = None  # type: ignore[assignment]

    def save(self) -> User:
        """Cria a conta de cliente com a identidade verificada pela equipa."""
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


class TeamMemberCreateForm(BaseStyledForm):
    """Cadastro de membro da equipa feito pela administração.

    Os dados pessoais que a equipa precisa são os de contacto e o perfil. Não se
    pede NIF nem data de nascimento: a equipa interna é identificada pelo
    e-mail e pela nomeação, e pedir um NIF a um curador seria inventar uma
    obrigação que o §2.11 não tem.
    """

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(
            attrs={"placeholder": "Ana Maria dos Santos", "autocomplete": "name"}
        ),
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"placeholder": "equipa@echilo.ao", "autocomplete": "email"}),
        help_text="É com este endereço que a pessoa entra.",
    )
    phone = forms.CharField(
        label="Telefone",
        max_length=20,
        required=False,
        validators=[validate_angolan_phone],
        widget=forms.TextInput(
            attrs={"placeholder": "+244 923 456 789", "autocomplete": "tel", "inputmode": "tel"}
        ),
    )
    role = forms.ChoiceField(
        label="Perfil de acesso",
        choices=[
            (User.Role.CURATOR, "Curador — regista e edita imóveis"),
            (User.Role.AGENT, "Agente — valida documentos e confirma visitas"),
            (User.Role.ADMIN, "Administrador — gere contas e tudo o mais"),
        ],
        help_text="O perfil decide o que a pessoa vê no menu e o que pode abrir.",
    )
    password = forms.CharField(
        label="Palavra-passe inicial",
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
        """Cria a conta de equipa com o perfil escolhido.

        O `role` vem do formulário e não da visita: o formulário só chega aqui
        depois de `require_admin`, e o perfil é a única coisa que faz de um
        membro da equipa uma chefia.

        A conta é montada e guardada como `register_client` faz, e não com
        `create_user`: o `save()` do modelo é quem deriva `is_team_member` e o
        acesso staff a partir do perfil, e um `create_user` que não passe por
        ele deixaria um agente sem acesso ao painel.
        """
        user = User(
            full_name=str(self.cleaned_data["full_name"]).strip(),
            email=str(self.cleaned_data["email"]),
            phone=str(self.cleaned_data.get("phone", "")).strip(),
            role=str(self.cleaned_data["role"]),
        )
        user.set_password(str(self.cleaned_data["password"]))
        user.save()
        return user


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
