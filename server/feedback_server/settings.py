"""Django settings for the IBL feedback server.

All deployment-specific configuration is read from environment variables so the
same code runs locally and on the deployment host. See ``server/.env.example``
for the full list. Sensible, clearly-insecure defaults are used for local
development so the project runs out of the box.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: str = "0") -> bool:
    """Read a boolean-ish environment variable."""
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable into a list."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core security -----------------------------------------------------------
# The fallback key is for local development ONLY and must be overridden in prod.
SECRET_KEY = os.environ.get("IBL_FEEDBACK_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = _env_bool("IBL_FEEDBACK_DEBUG", "0")
ALLOWED_HOSTS = _env_list("IBL_FEEDBACK_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _env_list("IBL_FEEDBACK_CSRF_TRUSTED_ORIGINS", "")

# Shared, write-only secret that clients present to POST feedback.
INGEST_TOKEN = os.environ.get("IBL_FEEDBACK_INGEST_TOKEN", "")

# Reject request bodies larger than this. Keep in sync with the client's
# MAX_PAYLOAD_BYTES and nginx ``client_max_body_size`` (30 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024

# --- Applications ------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "feedback",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "feedback_server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "feedback_server.wsgi.application"

# --- Database (SQLite by default; transcripts live in JSON columns) ----------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("IBL_FEEDBACK_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication redirects ------------------------------------------------
LOGIN_REDIRECT_URL = "session_list"
LOGOUT_REDIRECT_URL = "login"

# --- Behind an nginx TLS terminator in production ----------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
