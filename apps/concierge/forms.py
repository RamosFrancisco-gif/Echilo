"""Formulários do atendimento: adesão de proprietários, visita e proposta."""

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.core.forms import BaseStyledForm
from apps.core.validators import validate_angolan_phone
from apps.properties.models import Property

from .models import Lead

PROPERTY_CHOICES: list[tuple[str, str]] = [
    ("", "Ainda não sei"),
    (Property.Purpose.RENT, "Quero arrendar o imóvel"),
    (Property.Purpose.SALE, "Quero vender o imóvel"),
    (Property.Type.LAND, "É um terreno"),
]


class OwnerIntakeForm(BaseStyledForm):
    """Etapa 1 da captação: contacto directo do proprietário, sem autenticação."""

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Joaquim Ferreira", "autocomplete": "name"}),
    )
    phone = forms.CharField(
        label="Telefone",
        max_length=20,
        validators=[validate_angolan_phone],
        widget=forms.TextInput(
            attrs={"placeholder": "+244 923 456 789", "inputmode": "tel", "autocomplete": "tel"}
        ),
    )
    email = forms.EmailField(
        label="E-mail",
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "joaquim@exemplo.ao", "autocomplete": "email"}),
    )
    purpose_interest = forms.ChoiceField(
        label="O que pretende fazer com o imóvel?",
        required=False,
        choices=PROPERTY_CHOICES,
    )
    message = forms.CharField(
        label="Breve descrição do imóvel",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Ex.: casa T3 no Kilamba, com quintal, para arrendar.",
            }
        ),
    )

    def clean_phone(self) -> str:
        """Devolve o telefone já normalizado para gravação."""
        return " ".join(str(self.cleaned_data["phone"]).split())


class VisitRequestForm(BaseStyledForm):
    """Pedido de visita; a equipa confirma depois (§2.10)."""

    visit_date = forms.DateField(
        label="Data pretendida",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    visit_time = forms.TimeField(
        label="Hora",
        widget=forms.TimeInput(attrs={"type": "time"}),
        initial="10:00",
    )
    notes = forms.CharField(
        label="Observações para a equipa",
        required=False,
        max_length=240,
        widget=forms.TextInput(
            attrs={"placeholder": "Ex.: vou com a minha companheira."}
        ),
    )

    def clean(self) -> dict[str, object]:
        """Recusa datas no passado e combina a data com a hora."""
        cleaned = super().clean()
        date = cleaned.get("visit_date")
        hour = cleaned.get("visit_time")
        if date and hour:
            moment = timezone.make_aware(
                timezone.datetime.combine(date, hour),
                timezone.get_current_timezone(),
            )
            if moment < timezone.now():
                self.add_error("visit_date", "Escolha uma data futura para a visita.")
            cleaned["scheduled_for"] = moment
        return cleaned


class OfferForm(BaseStyledForm):
    """Proposta formal de preço para imóveis à venda (§2.5)."""

    amount = forms.CharField(
        label="Valor proposto em Kwanza",
        max_length=32,
        widget=forms.TextInput(attrs={"placeholder": "82.000.000", "inputmode": "decimal"}),
    )
    message = forms.CharField(
        label="Mensagem para a equipa",
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": "Tenho disponibilidade para fechar esta semana."}
        ),
    )

    def clean_amount(self) -> object:
        """Aceita o formato pt-AO (82.000.000) e recusa valores não positivos."""
        from decimal import Decimal, InvalidOperation

        raw = str(self.data.get("amount") or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise forms.ValidationError("Indique um valor válido em Kwanza.") from exc
        if value <= 0:
            raise forms.ValidationError("O valor proposto tem de ser maior do que zero.")
        quantised = value.quantize(Decimal("0.01"))
        if quantised >= Decimal("100000000000.00"):
            raise forms.ValidationError("O valor proposto excede o limite aceite.")
        return quantised


class LeadAdminFilterForm(BaseStyledForm):
    """Filtros da fila de contactos no painel interno."""

    lead_type = forms.ChoiceField(
        label="Tipo",
        required=False,
        choices=[("", "Todos")] + list(Lead.Type.choices),
    )
    status = forms.ChoiceField(
        label="Estado",
        required=False,
        choices=[("", "Todos")] + list(Lead.Status.choices),
    )
