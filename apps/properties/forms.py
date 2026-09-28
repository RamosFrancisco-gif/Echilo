"""Formulários do app de imóveis: curadoria interna e pesquisa pública."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms

from apps.core.forms import BaseStyledForm
from apps.core.images import motivo_recusa_imagem
from apps.core.storage import MIME_IMAGEM_ACEITE
from apps.core.validators import (
    MAX_SEARCH_RADIUS_M,
    MIN_SEARCH_RADIUS_M,
    validate_angolan_phone,
    validate_latitude,
    validate_longitude,
)

from .models import Property, PropertySubmission
from .reference import ANGOLA_PROVINCES, municipalities_for
from .validators import LIMITE_GB, MAX_FOTOS


class CoordenadaField(forms.DecimalField):
    """Coordenada em graus que aceita a vírgula decimal escrita à mão.

    O `DecimalField` só entende ponto, e o teclado de quem está no terreno
    escreve vírgula. Recusar `-8,839` com «Introduza um número» obriga a trocar
    de teclado no meio de uma visita, e é no carro que o seletor de pin costuma
    não ter rede.

    A troca acontece em `to_python`, e não num `clean_latitude`, porque o campo
    interpreta o valor *antes* de o `clean_<campo>` ser chamado: normalizar mais
    tarde nunca chega a correr. O que daqui sai para a base é sempre `Decimal`,
    e o que o JavaScript escreve de volta é sempre com ponto (§2.13).
    """

    default_error_messages = {
        "invalid": "Escreva um número, por exemplo -8.839000.",
    }

    def to_python(self, value: object) -> Decimal | None:
        """Normaliza o separador decimal antes de interpretar a coordenada."""
        if isinstance(value, str):
            value = value.strip().replace(" ", "").replace(",", ".")
        return super().to_python(value)


class PublicSearchForm(BaseStyledForm):
    """Filtros da navegação pública, legíveis como parâmetros de URL."""

    purpose = forms.ChoiceField(
        label="Finalidade",
        required=False,
        choices=[("", "Todas"), (Property.Purpose.RENT, "Arrendar"), (Property.Purpose.SALE, "Comprar")],
    )
    type = forms.ChoiceField(
        label="Tipologia",
        required=False,
        choices=[("", "Todas")] + list(Property.Type.choices),
    )
    province = forms.ChoiceField(
        label="Província",
        required=False,
        choices=[("", "Todas as províncias")] + list(ANGOLA_PROVINCES),
    )
    municipality = forms.CharField(label="Município", required=False, max_length=80)
    min_price = forms.DecimalField(label="Preço mínimo", required=False, max_digits=14, decimal_places=2)
    max_price = forms.DecimalField(label="Preço máximo", required=False, max_digits=14, decimal_places=2)
    min_bedrooms = forms.IntegerField(label="Quartos (mínimo)", required=False, min_value=0, max_value=20)
    min_area_m2 = forms.IntegerField(label="Área mínima (m²)", required=False, min_value=0)
    has_water_tank = forms.BooleanField(label="Com tanque de água", required=False)
    has_generator = forms.BooleanField(label="Com gerador", required=False)
    is_furnished = forms.BooleanField(label="Mobilado", required=False)
    keyword = forms.CharField(label="Pesquisa", required=False, max_length=120)
    # A área desenhada no mapa (§2.3). Ocultos no formulário, mas subjectos aos
    # mesmos limites que o JavaScript impõe, para que a URL não possa travar uma
    # pesquisa com um raio do tamanho do continente.
    center_lat = forms.DecimalField(
        label="Latitude do centro da área",
        required=False,
        max_digits=9,
        decimal_places=6,
        validators=[validate_latitude],
    )
    center_lon = forms.DecimalField(
        label="Longitude do centro da área",
        required=False,
        max_digits=9,
        decimal_places=6,
        validators=[validate_longitude],
    )
    radius_m = forms.IntegerField(
        label="Raio da área em metros",
        required=False,
        min_value=MIN_SEARCH_RADIUS_M,
        max_value=MAX_SEARCH_RADIUS_M,
    )

    def clean(self) -> dict[str, object]:
        """Impede intervalos de preço e área invertidos."""
        cleaned = super().clean()
        minimum = cleaned.get("min_price")
        maximum = cleaned.get("max_price")
        if minimum and maximum and Decimal(str(minimum)) > Decimal(str(maximum)):
            self.add_error("max_price", "O preço máximo tem de ser maior que o mínimo.")
        return cleaned

    def to_filters_kwargs(self) -> dict[str, object]:
        """Traduz o formulário limpo para o vocabulário de `PropertyFilters`."""
        values = {
            key: value
            for key, value in self.cleaned_data.items()
            if value not in (None, "", False)
        }
        if "type" in values:
            values["property_type"] = values.pop("type")
        return values


class BaseGeoFields(BaseStyledForm):
    """Os campos que dizem onde está o imóvel, partilhados pelos dois formulários.

    Ficam juntos porque têm de ficar juntos: a latitude e a longitude são um par,
    e o seletor de pin do mapa escreve as duas mais a precisão e a referência. Se
    os dois formulários divergirem, o mapa funciona num e não no outro — e a ficha
    interna passa a ser um lugar onde se muda o pin sem gravar a precisão.

    Herdam desta base as duas regras de §2.3 que não dependem de mais nada: o pin
    não nasce da morada escrita pelo proprietário, e o par de coordenadas ou vem
    inteiro ou não vem.
    """

    province_ref = forms.ChoiceField(label="Província", choices=ANGOLA_PROVINCES)
    municipality = forms.CharField(label="Município", max_length=80)
    locality = forms.CharField(label="Localidade", required=False, max_length=120)

    # Continuam a ser campos de texto e não `type="number"`: este recusa a
    # vírgula decimal que um teclado angolano produz, e sem JavaScript o mapa
    # desaparece, obrigando a escrever à mão. O seletor de pin escreve aqui, e o
    # campo continua legível e corrigível por quem não tem ecrã táctil.
    # `inputmode` é o que dá o teclado numérico sem impor o formato.
    latitude = CoordenadaField(
        label="Latitude",
        required=False,
        max_digits=9,
        decimal_places=6,
        validators=[validate_latitude],
        widget=forms.TextInput(
            attrs={"inputmode": "decimal", "placeholder": "-8.839000", "autocomplete": "off"}
        ),
    )
    longitude = CoordenadaField(
        label="Longitude",
        required=False,
        max_digits=9,
        decimal_places=6,
        validators=[validate_longitude],
        widget=forms.TextInput(
            attrs={"inputmode": "decimal", "placeholder": "13.289400", "autocomplete": "off"}
        ),
    )
    location_accuracy_m = forms.IntegerField(
        label="Precisão da localização (metros)",
        required=False,
        min_value=1,
        help_text="Raio de confiança. A geolocalização preenche-o, e a equipa pode corrigir.",
    )
    map_reference = forms.CharField(
        label="Referência do pin no mapa",
        required=False,
        max_length=40,
        help_text="O mapa escreve este campo a partir do pin. É o ponto inspecionado, não a morada.",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        instance = getattr(self, "instance", None)
        submitted = self.data.get("province_ref") if self.data else None
        # A ficha interna passa os dados do imóvel em `initial`, e não como
        # instância. Ler só a instância dava uma província vazia, e a lista
        # oferecia os cento e setenta e um nomes a quem via um imóvel de
        # Benguela: um campo que parece funcionar e não sugere nada do que devia.
        inicial = (self.initial or {}).get("province_ref")
        province = submitted or inicial or getattr(instance, "province_ref", "") or ""
        self.fields["municipality"].widget = forms.TextInput(
            attrs={
                "class": "field-control",
                "list": "municipality-options",
                "placeholder": "Talatona",
            }
        )
        self.province_municipality_options = municipalities_for(str(province))

    @property
    def municipality_options(self) -> list[str]:
        """Municípios sugeridos para a província escolhida."""
        return list(self.province_municipality_options)

    def clean(self) -> dict[str, object]:
        """Exige o par de coordenadas inteiro, sem acusar o que já errou."""
        cleaned = super().clean()
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")
        # Só se acusa falta do par quando nenhum dos campos falhou por si. Sem
        # esta guarda, uma latitude fora do intervalo desaparece do `cleaned_data`
        # e o erro de intervalo vem acompanhado de "indique latitude e longitude em
        # conjunto", que é mentira: o par foi indicado, o que está mal é o número.
        if "latitude" not in self.errors and "longitude" not in self.errors:
            if bool(latitude) != bool(longitude):
                self.add_error("longitude", "Indique latitude e longitude em conjunto.")
        return cleaned


class PrazoDeContratoMixin:
    """Regra do §2.6: um arrendamento sem prazo não é um contrato.

    Mixin e não subclasse: esta regra é de negócio e não sabe nada de campos. Os
    dois formulários que editam imóveis aplicam-na sem a escreverem duas vezes, e
    a ordem da `super()` garante que a regra do prazo corre antes da de cada um.
    """

    def clean(self) -> dict[str, object]:
        """Liga a finalidade ao prazo, nos dois formulários que os pedem."""
        cleaned = super().clean()  # type: ignore[misc]
        if cleaned.get("purpose") == Property.Purpose.RENT and not cleaned.get("lease_term_months"):
            self.add_error("lease_term_months", "Indique o prazo do contrato em meses.")
        return cleaned


class InputDeFotografias(forms.FileInput):
    """O input que aceita várias fotografias, e declara-o ao Django.

    A CVE-2023-31047 passou a recusar o atributo `multiple` em `FileInput` e em
    `ClearableFileInput`: um `FileField` sozinho só valida o *último* ficheiro,
    e o browser poder mandar doze era a forma de a validação ser contornada.

    A forma sancionada é o widget declarar `allow_multiple_selected`, e é uma
    classe — não um dicionário de atributos. O Django interroga a classe no
    `__init__`, e escrevê-lo nos `attrs` do campo é escrevê-lo fora do sítio
    onde é lido: o `attrs` diz o que o browser recebe, e o que este Django
    valida é a classe. As duas coisas podem discordar, e quando discordam o
    formulário deixa de existir.

    Sem isto o `ValueError` é levantado ao importar os urls, e a página toda dá
    500 — inclusive o `favicon` — sem que nada na falha mentione fotografias.
    Este campo é que sabe tratar a lista: o `clean_images` de baixo lê
    `self.files` e valida cada ficheiro por separado, um a um, que é
    precisamente o que a CVE pede.
    """

    allow_multiple_selected = True


class CampoDeFotografias(forms.FileField):
    """Um `FileField` que não valida o ficheiro, porque esse trabalho é do `clean_images`.

    A CVE-2023-31047 existe porque um `FileField` só sabe validar o ficheiro que
    lhe chega, e o `BaseForm` chama `field.clean()` **antes** do `clean_<campo>`:
    o valor que o campo recebe vem do `value_from_datadict` do widget, e com o
    opt-in esse valor é a lista inteira. O `to_python` recebe uma lista, não a
    reconhece como ficheiro, e recusa o formulário com «Nenhum ficheiro foi
    submetido» — quando o ficheiro foi, e há doze.

    Não é um salto na validação, é a validação no sítio certo: quem abre cada
    ficheiro e decide se entra é o `clean_images`, com o Pillow, ficheiro a
    ficheiro. Este campo limita-se a não fingir que valida o que não sabe.

    O `required` também deixa de valer aqui, e pelo mesmo motivo: a mensagem
    «Escolha pelo menos uma fotografia» diz o que fazer, e sabe se a ficha ou o
    cadastro é que exige o lote.
    """

    def clean(self, value: object, initial: object | None = None) -> object:
        """Deixa a lista tal como o widget a devolveu."""
        return value


class ImagensDoImovelMixin(BaseStyledForm):
    """As fotografias do imóvel, e a validação que cada ficheiro tem de passar.

    Vive num mixin porque há dois sítios onde a equipa as carrega — o cadastro e
    a ficha — e a regra do que é uma fotografia aceitável é uma só. Duas
    implementações da mesma regra divergem, e a segunda é a que alguém não
    actualiza: passava um PNG onde a ficha o recusava, sem erro nenhum.

    Várias de uma vez, porque a equipa fotografa o imóvel inteiro antes de se
    sentar a carregar. Uma fotografia por pedido obriga a escolher um ficheiro,
    esperar pelo carregamento e escolher o seguinte, quinze vezes.

    O `accept` diz o que o browser deve oferecer e a validação real é a de
    baixo: um `accept` é um pedido, não uma regra, e o que decide é o que o
    Pillow consegue abrir.
    """

    # O `multiple` não é escrito aqui: é o `InputDeFotografias` que o declara,
    # porque é a classe que o Django lê para decidir se o aceita. Repeti-lo nos
    # `attrs` daria duas respostas à mesma pergunta — a que o browser recebe e a
    # que o servidor valida — e a divergência entre as duas só apareceria quando
    # as versões do Django não coincidissem. O `accept` também vem do
    # `MIME_IMAGEM_ACEITE`, e por isso o input e o formulário dizem o mesmo.
    #
    # O campo é o `CampoDeFotografias` e não o `FileField` porque um só
    # `FileField` só valida um ficheiro — que é a razão da CVE-2023-31047 e a
    # razão de o `clean_images` existir.
    images = CampoDeFotografias(
        label="Fotografias",
        widget=InputDeFotografias(attrs={"accept": MIME_IMAGEM_ACEITE}),
        help_text=(
            f"Podem ser várias de uma vez, até {MAX_FOTOS} fotografias por imóvel. "
            f"A primeira é a capa. JPEG, PNG ou WebP, até {LIMITE_GB} MB cada."
        ),
    )
    # A ficha exige o lote porque é lá que a fotografia entra; o cadastro aceita
    # um imóvel sem fotografia porque ele nasce em rascunho e a ficha é onde a
    # equipa volta para a completar. O `required` do campo diz outra coisa, e é
    # por isso que este sinal existe à parte dele.
    images_sao_obrigatorias = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.property = kwargs.pop("property", None)
        self.livres = kwargs.pop("livres", MAX_FOTOS)
        super().__init__(*args, **kwargs)
        if self.livres <= 0:
            self.fields["images"].help_text = (
                f"Este imóvel já tem o máximo de {MAX_FOTOS} fotografias. "
                "Apague uma fotografia para dar lugar a outra."
            )
        self.fields["images"].label = "Fotografias" if self.livres != 1 else "Fotografia"
        if not self.images_sao_obrigatorias:
            self.fields["images"].required = False
            # O `required` também sai do widget, e não só do campo. O
            # `BaseStyledForm` põe o atributo no widget durante o `super()`,
            # que corre antes desta linha: um `required` que fica no HTML bloqueia
            # o envio no browser, e o browser não avisa que o servidor o aceitaria
            # vazio. O formulário diria "escolha uma fotografia" a quem não quer.
            self.fields["images"].widget.attrs.pop("required", None)
            self.fields["images"].help_text = (
                f"Opcional agora, até {MAX_FOTOS} fotografias por imóvel. "
                "A triagem só fecha com pelo menos 5, e pode carregá-las aqui "
                "ou depois na ficha do imóvel."
            )

    def _fotografia_valida(self, uploaded: object) -> str | None:
        """Devolve a razão pela qual a fotografia não entra, ou `None` se entra.

        A validação de cada ficheiro vive isolada para que um retrato escuro
        não leve o resto do lote consigo: a equipa escolheu doze fotografias e
        uma é um PDF disfarçado, e o que ela espera é guardar as onze boas.
        """
        return motivo_recusa_imagem(uploaded, limite_mb=LIMITE_GB)

    def clean_images(self) -> list[object]:
        """Separa as fotografias que entram das que não entram, e diz porquê.

        A validação de peso, formato e dimensões continua a ser a do §6. O
        `ImageField` do Django valida o cabeçalho, não a imagem: um executável
        renomeado a `.jpg` passa, e abrir com Pillow é o que separa uma
        fotografia de um ficheiro que diz ser uma.

        O re-codificação fica para a Cloudinary, que redimensiona e reescreve o
        ficheiro antes de o servir. Refazer isso aqui gastaria memória e tempo de
        CPU dentro dos 10 s da função para se deitar fora.
        """
        enviados = self.files.getlist(self.add_prefix("images"))
        if not enviados:
            if self.images_sao_obrigatorias:
                raise forms.ValidationError("Escolha pelo menos uma fotografia.")
            return []

        aceites: list[object] = []
        recusadas: list[str] = []
        for enviado in enviados:
            motivo = self._fotografia_valida(enviado)
            if motivo is None:
                aceites.append(enviado)
            else:
                recusadas.append(motivo)

        self.recusas = recusadas

        if not aceites:
            raise forms.ValidationError(recusadas[0] if recusadas else "Nenhuma fotografia válida.")

        if self.livres < len(aceites):
            # Excedente vai para `descartadas` em vez de ser ignorado em
            # silêncio: a equipa escolheu quinze e a página diz que guardado.
            self.descartadas = len(aceites) - self.livres
            aceites = aceites[: self.livres]
        else:
            self.descartadas = 0

        for aceite in aceites:
            aceite.seek(0)
        return aceites


class PropertyCuratorForm(ImagensDoImovelMixin, PrazoDeContratoMixin, BaseGeoFields):
    """Ficha de cadastro gerido, preenchida por um curador da equipa.

    O campo das fotografias é o mesmo que a ficha usa, e é opcional: o imóvel
    nasce em rascunho e a equipa pode voltar para a ficha para as carregar. Um
    cadastro que exigisse o lote obrigaria a escolher as fotografias antes de
    existirem os dados do imóvel, e a fotografar antes de o imóvel estar
    registado é o passo que a equipa dá depois de o registar.
    """

    images_sao_obrigatorias = False

    title = forms.CharField(label="Título do anúncio", max_length=140)
    description = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    type = forms.ChoiceField(label="Tipologia", choices=Property.Type.choices)
    purpose = forms.ChoiceField(label="Finalidade do contrato", choices=Property.Purpose.choices)
    price = forms.DecimalField(
        label="Preço em Kwanza",
        max_digits=14,
        decimal_places=2,
        help_text="Use ponto para milhares e vírgula para os cêntimos.",
    )
    lease_term_months = forms.IntegerField(
        label="Prazo do contrato (meses)",
        required=False,
        min_value=1,
        max_value=120,
    )
    accepts_annual_payment = forms.BooleanField(label="Aceita pagamento anual", required=False)

    address_hint = forms.CharField(
        label="Indício de morada (uso interno)",
        required=False,
        max_length=240,
        help_text="Nunca é publicado. Só a equipa o usa para orientar a visita.",
    )

    area_m2 = forms.IntegerField(label="Área construída (m²)", required=False, min_value=1)
    land_area_m2 = forms.IntegerField(label="Área do terreno (m²)", required=False, min_value=1)
    bedrooms = forms.IntegerField(label="Quartos", required=False, min_value=0, max_value=30)
    bathrooms = forms.IntegerField(label="Casas de banho", required=False, min_value=0, max_value=30)

    has_water_tank = forms.BooleanField(label="Tem tanque de água", required=False)
    has_generator = forms.BooleanField(label="Tem gerador", required=False)
    is_furnished = forms.BooleanField(label="Mobilado", required=False)
    has_garden = forms.BooleanField(label="Tem quintal", required=False)
    has_pool = forms.BooleanField(label="Tem piscina", required=False)
    has_parking = forms.BooleanField(label="Tem parque de estacionamento", required=False)

    # Identificação do proprietário, recolhida na captação (§2.1 etapa 2).
    owner_name = forms.CharField(
        label="Nome do proprietário",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Joaquim Ferreira", "autocomplete": "off"}),
    )
    owner_phone = forms.CharField(
        label="Telefone do proprietário",
        max_length=20,
        validators=[validate_angolan_phone],
        widget=forms.TextInput(
            attrs={"placeholder": "+244 923 456 789", "inputmode": "tel", "autocomplete": "off"}
        ),
    )
    owner_id_number = forms.CharField(
        label="Número do documento de identificação",
        max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "004512389LA041"}),
    )
    submission_source = forms.ChoiceField(
        label="Canal de captação",
        required=False,
        choices=PropertySubmission.Source.choices,
        initial=PropertySubmission.Source.WEB_FORM,
    )

    OWNER_FIELDS = {"owner_name", "owner_phone", "owner_id_number", "submission_source"}
    # Campos que o formulário carrega e que não são colunas do `Property`. Ficam
    # de fora por lista, e não por exclusão do resto: uma exclusão passa tudo o
    # que aparecer depois, e `images` a parar numa coluna que não existe dá um
    # erro de base de dados em vez de um formulário que valida.
    CAMPOS_NAO_GUARDADOS = OWNER_FIELDS | {"images"}

    def property_fields(self) -> dict[str, object]:
        """Devolve apenas os campos que pertencem ao modelo `Property`."""
        return {
            key: value
            for key, value in self.cleaned_data.items()
            if key not in self.CAMPOS_NAO_GUARDADOS
        }

    def owner_fields(self) -> dict[str, str]:
        """Devolve os dados de identificação do proprietário recolhidos na captação."""
        return {
            "full_name": str(self.cleaned_data["owner_name"]).strip(),
            "phone": str(self.cleaned_data["owner_phone"]).strip(),
            "id_document_number": str(self.cleaned_data["owner_id_number"]).strip(),
        }

    def clean_price(self) -> Decimal:
        """Aceita o formato angolano de milhares com ponto decimal virgem."""
        raw = str(self.data.get("price") or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise forms.ValidationError("Indique um preço válido em Kwanza.") from exc
        if value <= 0:
            raise forms.ValidationError("O preço tem de ser maior do que zero.")
        return value.quantize(Decimal("0.01"))

    def clean(self) -> dict[str, object]:
        """Exige área de terreno num terreno, e delega o resto às bases.

        O prazo do arrendamento e o par de coordenadas resolvem-se em
        `PrazoDeContratoMixin` e `BaseGeoFields`, para que a ficha interna os
        aplique igual sem os escrever duas vezes.
        """
        cleaned = super().clean()
        if cleaned.get("type") == Property.Type.LAND and not cleaned.get("land_area_m2"):
            self.add_error("land_area_m2", "Indique a área do terreno em metros quadrados.")
        return cleaned


class PropertyQuickEditForm(PrazoDeContratoMixin, BaseGeoFields):
    """Edição rápida na ficha interna: só o que essa página mostra.

    Não é o formulário de captação a mais. A ficha interna reutilizava-o inteiro,
    e daí vinham duas coisas erradas de uma vez: exigia nome, telefone e
    documento do proprietário numa página onde o proprietário não é o que se está
    a editar — e como o template não os desenha, o erro aparecia em campo nenhum
    e a gravação nunca acontecia. Pior depois de corrigido: o `form_valid`
    aplicava todos os campos limpos, e os que a página não desenhava voltavam
    vazios do `POST`, apagando descrição, área, comodidades e o pin verificado
    por causa de uma alteração de preço.

    Um formulário por página é o que torna a segunda impossível: o que a página
    não mostra, o formulário não tem, e o que o formulário não tem não é
    gravado. Inclui a localização inteira — pin, precisão e referência — porque
    o seletor de pin escreve os três e a ficha tem de os mostrar todos.
    """

    title = forms.CharField(label="Título do anúncio", max_length=140)
    type = forms.ChoiceField(label="Tipologia", choices=Property.Type.choices)
    purpose = forms.ChoiceField(label="Finalidade do contrato", choices=Property.Purpose.choices)
    price = forms.DecimalField(
        label="Preço em Kwanza",
        max_digits=14,
        decimal_places=2,
        help_text="Use ponto para milhares e vírgula para os cêntimos.",
    )
    lease_term_months = forms.IntegerField(
        label="Prazo do contrato (meses)",
        required=False,
        min_value=1,
        max_value=120,
    )
    bedrooms = forms.IntegerField(label="Quartos", required=False, min_value=0, max_value=30)
    bathrooms = forms.IntegerField(label="Casas de banho", required=False, min_value=0, max_value=30)

    CAMPOS_NAO_GUARDADOS: set[str] = {"localizacao_confirmada"}

    localizacao_confirmada = forms.BooleanField(
        label="Confirmei a localização por satélite",
        required=False,
        help_text=(
            "Marque depois de confirmar o ponto na imagem de satélite. Largar o pin no "
            "mapa não confirma nada: o imóvel perde a confirmação e deixa de poder ser "
            "publicado até alguém a fazer."
        ),
    )

    def property_fields(self) -> dict[str, object]:
        """Devolve os campos do imóvel, menos a caixa que não é coluna."""
        return {
            key: value
            for key, value in self.cleaned_data.items()
            if key not in self.CAMPOS_NAO_GUARDADOS
        }

    def confirmou_localizacao(self) -> bool:
        """Diz se a equipa afirma ter inspeccionado o ponto por satélite (§2.3).

        O método não pode chamar-se como o campo. Numa `Form`, o atributo de
        classe do campo é o que o template resolve em `form.<nome>`, e um método
        com o mesmo nome ficava por cima dele: o `{{ form.localizacao_confirmada }}`
        da ficha desenhava o resultado de `()`, e a página levantava um
        `AttributeError` de `cleaned_data` em vez de mostrar a caixa.
        """
        return bool(self.cleaned_data.get("localizacao_confirmada"))

    def clean_price(self) -> Decimal:
        """Aceita o mesmo formato angolano de milhares que a captação."""
        raw = str(self.data.get("price") or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise forms.ValidationError("Indique um preço válido em Kwanza.") from exc
        if value <= 0:
            raise forms.ValidationError("O preço tem de ser maior do que zero.")
        return value.quantize(Decimal("0.01"))


class PropertyTransitionForm(BaseStyledForm):
    """Transição de estado accionada pela equipa, sempre com justificação (§2.8)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.property = kwargs.pop("property")
        super().__init__(*args, **kwargs)
        allowed = [
            (target, label)
            for target, label in Property.Status.choices
            if self.property.transition_allowed(target)
        ]
        self.fields["to_status"].choices = allowed

    to_status = forms.ChoiceField(label="Novo estado", choices=[])
    reason = forms.CharField(
        label="Justificação",
        max_length=240,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": "Obrigatória em publicação e arquivo"}
        ),
        help_text="Fica registada no histórico do imóvel.",
    )

    def clean(self) -> dict[str, object]:
        """Exige justificação nos estados que a regra marca como sensíveis."""
        cleaned = super().clean()
        target = str(cleaned.get("to_status") or "")
        if target in Property.REASONS_REQUIRED and not str(cleaned.get("reason") or "").strip():
            self.add_error("reason", "Este estado exige uma justificação.")
        return cleaned

    @property
    def ha_transicoes(self) -> bool:
        """Diz se há para onde ir a partir do estado actual."""
        return bool(self.fields["to_status"].choices)

    @property
    def razao_e_sempre_obrigatoria(self) -> bool:
        """Diz se todos os estados oferecidos exigem justificação.

        A justificação é obrigatória a publicar e a arquivar, e facultativa nos
        restantes, e o `required` do HTML só pode ser posto quando é verdade para
        todos os estados que o `select` oferece. Num imóvel publicado a única saída
        é o arquivo, o campo pode levar o atributo, e o browser recusa o envio
        antes de gastar um pedido — o que devolve a justificação escrita à pessoa
        em vez de a perder num redireccionamento.

        Com mais de um estado oferecido, o atributo fica de fora: o browser não
        sabe qual está escolhido, e um `required` aqui bloquearia uma transição
        que o servidor aceita. Nesse caso quem responde é a mensagem, que diz o
        que falta.
        """
        alvos = [str(codigo) for codigo, _label in self.fields["to_status"].choices]
        return bool(alvos) and all(alvo in Property.REASONS_REQUIRED for alvo in alvos)


class PropertyDeleteForm(BaseStyledForm):
    """Confirmação de apagamento, com o motivo escrito e a referência repetida.

    Um botão que apaga não pede a segunda confirmação do browser — a que o
    `window.confirm()` dá, que é um `alert` do sistema e não diz o que vai
    acontecer. Aqui o que se pede é o que fica: o motivo, que sobrevive em
    `PropertyDeletion`, e a referência escrita à mão, para que ninguém apague o
    imóvel errado a partir de uma ficha com o título parecido.

    A referência é comparada depois de normalizada porque a pessoa a copia da
    ficha, onde está em maiúsculas, e escreve-a em minúsculas. A mesma letra em
    duas caixas não pode ser o que separa "apagar" de "não apagar".
    """

    reference = forms.CharField(
        label="Referência do imóvel",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )
    reason = forms.CharField(
        label="Motivo do apagamento",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Fica registado com o seu nome e a data, mesmo depois de o imóvel "
            "deixar de existir."
        ),
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.property = kwargs.pop("property")
        super().__init__(*args, **kwargs)

    def clean_reference(self) -> str:
        """Recusa a referência errada antes de o motivo ser lido."""
        escrita = str(self.cleaned_data["reference"]).strip().upper()
        if escrita != self.property.reference.upper():
            raise forms.ValidationError(
                "A referência não corresponde a este imóvel. "
                f"O que está na ficha é {self.property.reference}."
            )
        return escrita

    def clean_reason(self) -> str:
        """Motivo é obrigatório: um apagamento sem explicação não se distingue de um erro."""
        motivo = str(self.cleaned_data["reason"]).strip()
        if not motivo:
            raise forms.ValidationError("Indique o motivo do apagamento.")
        return motivo


class PropertyImageUploadForm(ImagensDoImovelMixin, BaseStyledForm):
    """Carregamento das fotografias do imóvel pela equipa, a partir da ficha."""

