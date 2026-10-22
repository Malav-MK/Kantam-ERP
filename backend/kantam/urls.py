from django.urls import include, path

from apps.core.auth_views import PublicKeyView, healthz

urlpatterns = [
    path("healthz", healthz),
    path("internal/jwt-public-key", PublicKeyView.as_view()),
    path("api/", include("apps.core.urls")),
    path("api/dash/", include("apps.dashboards.urls")),
    path("api/docs/", include("apps.dashboards.doc_urls")),
]
