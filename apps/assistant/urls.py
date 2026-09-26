"""Rotas do assistente sob o prefixo `/assistente/`."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("", views.chat_window, name="chat"),
    path("mensagem/", views.chat_message, name="chat_message"),
    path("estado/", views.chat_health, name="health"),
]
