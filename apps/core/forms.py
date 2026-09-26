"""Formulários reutilizados entre apps."""

from __future__ import annotations

from django import forms


class BaseStyledForm(forms.Form):
    """Base com as classes CSS do sistema de design aplicadas aos campos."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                widget.attrs.setdefault("class", "checkbox")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "field-control")
            else:
                widget.attrs.setdefault("class", "field-control")
            if field.required:
                widget.attrs.setdefault("required", "required")
            widget.attrs.setdefault("autocomplete", "off")
