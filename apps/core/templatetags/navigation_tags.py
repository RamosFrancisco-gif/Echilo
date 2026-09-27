"""Tags de navegação: expõem `apps.core.navigation` ao template."""

from __future__ import annotations

from django import template

from apps.core import navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def menu(context: object) -> list[dict]:
    """Desenha a barra de navegação própria do perfil de quem está a ver."""
    request = context.get("request")  # type: ignore[attr-defined]
    user = getattr(request, "user", None)
    nome_atual = ""
    if getattr(request, "resolver_match", None) is not None:
        nome_atual = request.resolver_match.url_name or ""
    return navigation.para_template(user, nome_atual=nome_atual)
