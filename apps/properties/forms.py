"""Formulários do app de imóveis: curadoria interna e pesquisa pública."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from PIL import Image, UnidentifiedImageError

from apps.core.forms import BaseStyledForm
from apps.core.storage import FORMATOS_IMAGEM
from apps.core.validators import (
    MAX_SEARCH_RADIUS_M,
    MIN_SEARCH_RADIUS_M,
    validate_angolan_phone,
    validate_latitude,
    validate_longitude,
)

from .models import Property, PropertySubmission
from .reference import ANGOLA_PROVINCES, municipalities_for


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


class PropertyCuratorForm(BaseStyledForm):
    """Ficha de cadastro gerido, preenchida por um curador da equipa."""

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

    province_ref = forms.ChoiceField(label="Província", choices=ANGOLA_PROVINCES)
    municipality = forms.CharField(label="Município", max_length=80)
    locality = forms.CharField(label="Localidade", required=False, max_length=120)
    address_hint = forms.CharField(
        label="Indício de morada (uso interno)",
        required=False,
        max_length=240,
        help_text="Nunca é publicado. Só a equipa o usa para orientar a visita.",
    )

    latitude = forms.DecimalField(
        label="Latitude",
        required=False,
        max_digits=9,
        decimal_places=6,
    )
    longitude = forms.DecimalField(
        label="Longitude",
        required=False,
        max_digits=9,
        decimal_places=6,
    )
    location_accuracy_m = forms.IntegerField(
        label="Precisão da localização (metros)",
        required=False,
        min_value=1,
    )
    map_reference = forms.CharField(
        label="Referência do pin no mapa",
        required=False,
        max_length=40,
        help_text="Resultado da inspeção por satélite, por exemplo 9.5833,13.2344.",
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

    OWNER_FIELDS = {"owner_name", "owner_phone", "owner_id_number", "submission_source"}

    def property_fields(self) -> dict[str, object]:
        """Devolve apenas os campos que pertencem ao modelo `Property`."""
        return {
            key: value
            for key, value in self.cleaned_data.items()
            if key not in self.OWNER_FIELDS
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
        """Aplica as regras de negócio que dependem de vários campos."""
        cleaned = super().clean()
        if cleaned.get("purpose") == Property.Purpose.RENT and not cleaned.get("lease_term_months"):
            self.add_error("lease_term_months", "Indique o prazo do contrato em meses.")
        if cleaned.get("type") == Property.Type.LAND and not cleaned.get("land_area_m2"):
            self.add_error("land_area_m2", "Indique a área do terreno em metros quadrados.")
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")
        if bool(latitude) != bool(longitude):
            self.add_error("longitude", "Indique latitude e longitude em conjunto.")
        return cleaned


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


class PropertyImageUploadForm(BaseStyledForm):
    """Carregamento de uma fotografia do imóvel pela equipa."""

    image = forms.ImageField(
        label="Fotografia",
        help_text="A primeira fotografia é a capa. Mínimo 5, máximo 30 (§2.1).",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.property = kwargs.pop("property", None)
        super().__init__(*args, **kwargs)

    def clean_image(self) -> object:
        """Valida peso, formato e dimensões da fotografia (§6).

        O `ImageField` do Django valida o cabeçalho, não a imagem: um executável
        renomeado a `.jpg` passa. Abrir com Pillow é o que separa uma fotografia
        de um ficheiro         que diz ser uma. E as dimensões importam por si — uma
        fotografia de 40 000 px de lado não é uma fotografia de um imóvel, é um
        erro de alguém, e enche o cartão de catálogo em três lados.

        O re-codificação fica para a Cloudinary, que redimensiona e reescreve o
        ficheiro antes de o servir. Refazer isso aqui gastaria memória e tempo de
        CPU dentro dos 10 s da função para se deitar fora.
        """
        uploaded = self.cleaned_data["image"]
        if uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("A fotografia não pode exceder 5 MB.")

        try:
            with Image.open(uploaded) as imagem:
                largura, altura = imagem.size
                formato = (imagem.format or "").lower()
        except (UnidentifiedImageError, OSError, ValueError) as erro:
            raise forms.ValidationError("O ficheiro enviado não é uma imagem válida.") from erro

        if formato not in FORMATOS_IMAGEM:
            raise forms.ValidationError(
                "Formato não aceite. Use JPEG, PNG ou WebP."
            )
        if max(largura, altura) > 8000:
            raise forms.ValidationError(
                "A fotografia tem mais de 8000 px de lado. Reduza-a antes de enviar."
            )

        uploaded.seek(0)
        return uploaded

    def clean(self) -> dict[str, object]:
        """Impede ultrapassar as trinta fotografias exigidas na captação."""
        cleaned = super().clean()
        if self.property is not None and self.property.images.count() >= 30:
            raise forms.ValidationError("Este imóvel já tem o máximo de 30 fotografias.")
        return cleaned
