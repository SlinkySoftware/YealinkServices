from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = env(name)
    if value is None:
        return default
    return int(value)


def env_list(name: str, default: list[str]) -> list[str]:
    value = env(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    ["localhost", "127.0.0.1"],
)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "diversion",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "yealinkService.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [],
        },
    }
]

WSGI_APPLICATION = "yealinkService.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "django.sqlite3",
    }
}

LANGUAGE_CODE = "en-au"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

PHONE_SERVICES_BASE_URL = env(
    "PHONE_SERVICES_BASE_URL",
    "http://localhost:8001/services/",
)
PHONE_SERVICES_ENABLE_ROOT_MOUNT = env_bool("PHONE_SERVICES_ENABLE_ROOT_MOUNT", False)
PHONE_SERVICES_COMPANY_NAME = env("PHONE_SERVICES_COMPANY_NAME", "ExampleCorp")
PHONE_SERVICES_HEADER_LOGO_URL = env("PHONE_SERVICES_HEADER_LOGO_URL")
PHONE_SERVICES_FULLSCREEN_LOGO_URL = env("PHONE_SERVICES_FULLSCREEN_LOGO_URL")

PHONE_MANAGER_DEVICE_CONTEXT_URL = env(
    "PHONE_MANAGER_DEVICE_CONTEXT_URL",
    "http://127.0.0.1:8000/internal/device-context/",
)
PHONE_MANAGER_NORMALIZE_NUMBER_URL = env(
    "PHONE_MANAGER_NORMALIZE_NUMBER_URL",
    "http://127.0.0.1:8000/internal/normalize-number/",
)
PHONE_MANAGER_TIMEOUT_SECONDS = env_int("PHONE_MANAGER_TIMEOUT_SECONDS", 5)

CUCM_AXL_HOST = env("CUCM_AXL_HOST", "cucm-publisher.example.internal")
CUCM_AXL_PORT = env_int("CUCM_AXL_PORT", 8443)
CUCM_AXL_USERNAME = env("CUCM_AXL_USERNAME", "svc_phone_diversion_axl")
CUCM_AXL_PASSWORD = env("CUCM_AXL_PASSWORD", "change-me")
CUCM_AXL_VERIFY_TLS = env_bool("CUCM_AXL_VERIFY_TLS", False)
CUCM_AXL_TIMEOUT_SECONDS = env_int("CUCM_AXL_TIMEOUT_SECONDS", 10)
CUCM_AXL_LEGACY_TLS_COMPATIBILITY = env_bool("CUCM_AXL_LEGACY_TLS_COMPATIBILITY", False)
CUCM_AXL_LEGACY_TLS_CIPHERS = env(
    "CUCM_AXL_LEGACY_TLS_CIPHERS",
    "AES128-SHA:@SECLEVEL=0",
)
CUCM_ROUTE_PARTITION = env("CUCM_ROUTE_PARTITION", "INTERNAL")
CUCM_APPLY_LINE_AFTER_UPDATE = env_bool("CUCM_APPLY_LINE_AFTER_UPDATE", True)
AXL_WSDL_ROOT = BASE_DIR / "wsdl"

CFA_CACHE_TTL_SECONDS = env_int("CFA_CACHE_TTL_SECONDS", 3600)
DRY_RUN = env_bool("DRY_RUN", False)

LOG_FILE = env("LOG_FILE", str(BASE_DIR / "diversion.log"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
