"""Top-level URL configuration for the IBL feedback server."""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Built-in login/logout/password views (reviewers authenticate here).
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("feedback.urls")),
]
