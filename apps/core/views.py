"""Views de serviço do projecto: páginas de erro e utilitários sem domínio."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def page_not_found(request: HttpRequest, exception: object) -> HttpResponse:
    """Renderiza a página 404 com a identidade do Echilo."""
    return render(request, "errors/404.html", status=404)


def permission_denied(request: HttpRequest, exception: object) -> HttpResponse:
    """Renderiza a página 403 e explica que a área é interna da equipa."""
    return render(request, "errors/403.html", status=403)


def server_error(request: HttpRequest) -> HttpResponse:
    """Renderiza a página 500 sem revelar detalhes internos."""
    return render(request, "errors/500.html", status=500)
