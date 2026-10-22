"""Settings for the automated test suite (secure defaults kept; only secrets/paths/speed differ)."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("KANTAM_TESTING", "1")
os.environ.setdefault("ZT_GATEWAY_SECRET", "test-gateway-secret")

from .settings import *  # noqa: E402,F401,F403
from .settings import ZERO_TRUST  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="kantam-test-"))
ZERO_TRUST["KEY_DIR"] = _tmp / "keys"
ZERO_TRUST["GATEWAY_SHARED_SECRET"] = "test-gateway-secret"
ZERO_TRUST["ALLOWED_CIDRS"] = ["10.0.0.0/8", "127.0.0.0/8"]
MEDIA_ROOT = _tmp / "media"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only; production uses Argon2
LOGGING = {"version": 1, "disable_existing_loggers": True}
