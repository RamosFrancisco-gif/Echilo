"""Formulários reutilizados entre apps."""

from __future__ import annotations

from django import forms


class _CamposEstilizados:
    """Aplica as classes CSS do sistema de design a todos os campos.

    Vive como mixin e não como método só de `BaseStyledForm` porque a página do
    perfil precisa de um `ModelForm` com o mesmo aspecto. Duas bases com o mesmo
    laço de estilo são duas listas de campos que divergem: um campo novo sai com
    a classe numa página e sem a classe na outra, e o que se vê é um formulário
    que parece de outra aplicação.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                widget.attrs.setdefault("class", "checkbox")
            else:
                widget.attrs.setdefault("class", "field-control")
            if field.required:
                widget.attrs.setdefault("required", "required")
            widget.attrs.setdefault("autocomplete", "off")


class BaseStyledForm(_CamposEstilizados, forms.Form):
    """Base com as classes CSS do sistema de design aplicadas aos campos."""


class BaseStyledModelForm(_CamposEstilizados, forms.ModelForm):
    """O mesmo aspecto, sobre um modelo — usado na edição de conta."""
