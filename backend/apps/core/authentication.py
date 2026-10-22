"""
Per-request continuous verification (never trust, always verify).

For every API call we re-validate: token signature/expiry, the server-side
session (revocation, idle and absolute timeout), the device binding (httpOnly
device cookie must match the token's device claim and be an active enrolled
device), the user's active/lockout state and the role version - so a
permission change or account disablement takes effect on the next request.
"""
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .audit import set_request_context
from .models import AuthSession, Device
from .security import DEVICE_COOKIE, constant_eq, decode_token, parse_device_cookie, sha256

ZT = settings.ZERO_TRUST


class TokenRejected(AuthenticationFailed):
    def __init__(self, code):
        super().__init__(detail={"error": "unauthenticated", "reason": code}, code=code)


def resolve_device(request):
    """Return the active enrolled Device proven by the httpOnly cookie, or None."""
    device_id, secret = parse_device_cookie(request.COOKIES.get(DEVICE_COOKIE))
    if not device_id:
        return None
    device = Device.objects.filter(pk=device_id, status="active").first()
    if not device or not device.secret_hash or not constant_eq(device.secret_hash, sha256(secret)):
        return None
    return device


class ZeroTrustAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer realm="kantam"'

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None
        try:
            claims = decode_token(header[7:].strip())
        except jwt.ExpiredSignatureError:
            raise TokenRejected("token_expired")
        except jwt.InvalidTokenError:
            raise TokenRejected("token_invalid")

        session = AuthSession.objects.select_related("user", "device").filter(pk=claims.get("sid")).first()
        now = timezone.now()
        if not session or str(session.user_id) != claims["sub"]:
            raise TokenRejected("session_unknown")
        if session.revoked_at or session.expires_at <= now:
            raise TokenRejected("session_revoked")
        if session.last_seen + timedelta(seconds=ZT["SESSION_IDLE_SECONDS"]) < now:
            session.revoked_at = now
            session.revoke_reason = "idle_timeout"
            session.save(update_fields=["revoked_at", "revoke_reason"])
            raise TokenRejected("session_idle_timeout")

        user = session.user
        if not user.is_active or (user.locked_until and user.locked_until > now):
            raise TokenRejected("account_disabled")
        if user.active_until and user.active_until < timezone.localdate():
            raise TokenRejected("account_expired")
        if claims.get("rv") != user.role_version:
            raise TokenRejected("permissions_changed")

        device = None
        if ZT["REQUIRE_ENROLLED_DEVICE"] or claims.get("did"):
            device = resolve_device(request)
            if device is None or str(device.pk) != str(claims.get("did")) or session.device_id != device.pk:
                raise TokenRejected("device_not_trusted")
            if device.restricted_to.exists() and not device.restricted_to.filter(pk=user.pk).exists():
                raise TokenRejected("device_not_permitted_for_user")

        if (now - session.last_seen).total_seconds() > 60:
            AuthSession.objects.filter(pk=session.pk).update(last_seen=now)
            if device:
                Device.objects.filter(pk=device.pk).update(last_seen=now, last_ip=getattr(request, "zt_client_ip", None))

        request.zt_session = session
        request.zt_device = device
        set_request_context(user=user, session_id=session.pk, device_id=device.pk if device else None)
        return user, claims
