"""
Kantam ERP + CRM - Django settings.

All security-relevant behaviour is driven by environment variables so the same
build runs in dev, test and the factory production server. Defaults are the
*secure* choice; development relaxations must be switched on explicitly.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or env_bool("KANTAM_TESTING"):
        SECRET_KEY = "dev-only-insecure-key-never-use-in-production"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set outside DEBUG mode")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "apps.core",
    "apps.masters",
    "apps.crm",
    "apps.sales",
    "apps.planning",
    "apps.production",
    "apps.inventory",
    "apps.purchase",
    "apps.quality",
    "apps.fg",
    "apps.gate",
    "apps.tasks",
    "apps.dashboards",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.zero_trust.GatewayTrustMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.zero_trust.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "kantam.urls"
WSGI_APPLICATION = "kantam.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# --- Database -------------------------------------------------------------
# PostgreSQL is the production database (row locks, MVCC, concurrent users).
# SQLite is accepted only for local development and the automated test suite.
if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "kantam"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "db"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "ATOMIC_REQUESTS": False,
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": env("POSTGRES_SSLMODE", "prefer")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env("SQLITE_NAME", "kantam_dev.sqlite3"),
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_USER_MODEL = "core.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"  # plant display time; storage is UTC
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_ROOT = Path(env("KANTAM_MEDIA_ROOT", str(BASE_DIR / "protected_media")))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.authentication.ZeroTrustAuthentication"],
    # Deny by default: every view must declare what it requires.
    "DEFAULT_PERMISSION_CLASSES": ["apps.core.rbac.DenyByDefault"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.api.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "apps.core.api.exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# --- Security headers (backend is internal; gateway also sets these) ------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# --- Zero-trust policy ----------------------------------------------------
ZERO_TRUST = {
    # Requests must carry a valid HMAC signature from the Node gateway.
    "REQUIRE_GATEWAY": env_bool("ZT_REQUIRE_GATEWAY", True),
    "GATEWAY_SHARED_SECRET": env("ZT_GATEWAY_SECRET", ""),
    "GATEWAY_MAX_SKEW_SECONDS": int(env("ZT_GATEWAY_MAX_SKEW", "30")),
    # Factory networks allowed to reach the application (checked on the client IP
    # reported by the gateway, after the gateway itself has enforced it).
    "ALLOWED_CIDRS": env_list("ZT_ALLOWED_CIDRS", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"),
    # Every session is bound to an enrolled device.
    "REQUIRE_ENROLLED_DEVICE": env_bool("ZT_REQUIRE_DEVICE", True),
    # Token / session lifetimes.
    "ACCESS_TOKEN_SECONDS": int(env("ZT_ACCESS_TTL", "600")),
    "SESSION_IDLE_SECONDS": int(env("ZT_SESSION_IDLE", "1800")),
    "SESSION_ABSOLUTE_SECONDS": int(env("ZT_SESSION_ABSOLUTE", str(13 * 3600))),
    "MFA_REQUIRED_ROLE_LEVELS": env_list("ZT_MFA_LEVELS", "owner,admin,manager"),
    "MAX_FAILED_LOGINS": int(env("ZT_MAX_FAILED_LOGINS", "5")),
    "LOCKOUT_SECONDS": int(env("ZT_LOCKOUT_SECONDS", "900")),
    "KEY_DIR": Path(env("ZT_KEY_DIR", str(BASE_DIR / "keys"))),
    "TOKEN_ISSUER": "kantam-erp",
    "TOKEN_AUDIENCE": "kantam-gateway",
    "COOKIE_SECURE": env_bool("ZT_COOKIE_SECURE", not DEBUG),
}

# --- Plant operating rules (brief page 3) ---------------------------------
KANTAM = {
    "PLANT_TZ": "Asia/Kolkata",
    "DAY_SHIFT_START_HOUR": 9,   # 09:00 - 21:00
    "NIGHT_SHIFT_START_HOUR": 21,  # 21:00 - 09:00 next day
    "SHIFT_HOURS": 12,
    "SHIFT_CLOSE_GRACE_MINUTES": int(env("KANTAM_SHIFT_GRACE_MIN", "120")),
    "SO_LOCK_HOURS": 24,
    "NEXT_MONTH_PREP_DAY": 20,
    "ATTACHMENT_MAX_BYTES": 8 * 1024 * 1024,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"level": "WARNING"}, "kantam.security": {"level": "WARNING"}},
}
