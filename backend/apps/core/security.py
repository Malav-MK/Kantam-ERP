"""
Token and secret handling.

* Access tokens are short-lived EdDSA (Ed25519) JWTs. Only Django holds the
  private key; the Node gateway gets the public key and can verify but never
  mint tokens.
* Refresh tokens and device secrets are random values stored only as SHA-256
  hashes; they are delivered in httpOnly SameSite=Strict cookies.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.utils import timezone

ZT = settings.ZERO_TRUST
DEVICE_COOKIE = "kt_dev"
REFRESH_COOKIE = "kt_rt"

_keys = {}


def _key_paths():
    key_dir = ZT["KEY_DIR"]
    return key_dir / "jwt_ed25519_private.pem", key_dir / "jwt_ed25519_public.pem"


def ensure_keys():
    private_path, public_path = _key_paths()
    if not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        private_path.write_bytes(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        private_path.chmod(0o600)
        public_path.write_bytes(
            key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        )
    return private_path, public_path


def private_key():
    if "private" not in _keys:
        private_path, _ = ensure_keys()
        _keys["private"] = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    return _keys["private"]


def public_key_pem():
    if "public_pem" not in _keys:
        _, public_path = ensure_keys()
        _keys["public_pem"] = public_path.read_text()
    return _keys["public_pem"]


def sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def random_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def constant_eq(a, b):
    return hmac.compare_digest(str(a), str(b))


def issue_access_token(user, session, permissions_hash=""):
    now = timezone.now()
    payload = {
        "iss": ZT["TOKEN_ISSUER"],
        "aud": ZT["TOKEN_AUDIENCE"],
        "sub": str(user.pk),
        "sid": str(session.pk),
        "did": str(session.device_id) if session.device_id else None,
        "rv": user.role_version,
        "mfa": session.mfa_verified,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()) - 5,
        "exp": int((now + timedelta(seconds=ZT["ACCESS_TOKEN_SECONDS"])).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, private_key(), algorithm="EdDSA")


def issue_mfa_token(user, device_id, purpose="mfa"):
    now = timezone.now()
    payload = {
        "iss": ZT["TOKEN_ISSUER"],
        "aud": "kantam-mfa",
        "sub": str(user.pk),
        "did": str(device_id) if device_id else None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "typ": purpose,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, private_key(), algorithm="EdDSA")


def decode_token(token, audience=None, typ="access"):
    public = serialization.load_pem_public_key(public_key_pem().encode())
    payload = jwt.decode(
        token,
        public,
        algorithms=["EdDSA"],
        audience=audience or ZT["TOKEN_AUDIENCE"],
        issuer=ZT["TOKEN_ISSUER"],
        options={"require": ["exp", "iat", "sub", "typ"]},
    )
    if payload.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def parse_device_cookie(raw):
    """Device cookie format: '<device uuid>.<secret>'."""
    if not raw or "." not in raw:
        return None, None
    device_id, secret = raw.split(".", 1)
    try:
        return uuid.UUID(device_id), secret
    except ValueError:
        return None, None


def gateway_signature(secret, timestamp, method, path, client_ip, request_id):
    message = f"{timestamp}\n{method.upper()}\n{path}\n{client_ip}\n{request_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
