"""Rotas raiz do projecto Echilo."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views  # noqa: F401 — usado pelos handlers de erro.

urlpatterns = [
    path("admin/", admin.site.urls),
    path("conta/", include("apps.accounts.urls")),
    path("", include("apps.properties.urls")),
    path("atendimento/", include("apps.concierge.urls")),
    path("assistente/", include("apps.assistant.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
