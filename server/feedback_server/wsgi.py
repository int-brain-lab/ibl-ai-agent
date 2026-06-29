"""WSGI entry point for the IBL feedback server (used by gunicorn)."""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedback_server.settings")
application = get_wsgi_application()
