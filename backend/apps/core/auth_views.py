"""
Authentication endpoints.

Flow: enrolled device (httpOnly cookie) -> username/password -> TOTP MFA for
owner/admin/manager levels -> server-side session bound to the device ->
10-minute EdDSA access token + rotating refresh cookie. Refresh-token reuse
revokes the whole session family (stolen-token detection).
"""
import secrets
from datetime import timedelta

import jwt
import pyotp
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import audit
from .api import BusinessRuleError
from .authentication import resolve_device
from .models import AuthSession, Device, LoginAttempt, User
from .rbac import effective_permissions, role_codes, role_levels
from .security import (
    DEVICE_COOKIE, REFRESH_COOKIE, decode_token, issue_access_token, issue_mfa_token, public_key_pem, random_token, sha256,
)
from .shifts import shift_for

ZT = settings.ZERO_TRUST
DUMMY_HASH = make_password("kantam-timing-equaliser")
GENERIC_LOGIN_ERROR = "Invalid username or password, or the account is temporarily locked."


def healthz(request):
    return JsonResponse({"status": "ok"})


def _ip(request):
    return getattr(request, "zt_client_ip", None)


def _require_xhr(request):
    # Cookie-authenticated endpoints require a custom header that cannot be sent
    # cross-site without a CORS preflight (which the gateway refuses).
    if request.headers.get("X-Requested-With") != "kantam":
        raise BusinessRuleError("Missing request header.", "csrf")


class PublicKeyView(APIView):
    """Gateway fetches the token verification key (request is gateway-signed)."""

    public_endpoint = True
    authentication_classes = []

    def get(self, request):
        return Response({"alg": "EdDSA", "public_key_pem": public_key_pem(), "issuer": ZT["TOKEN_ISSUER"], "audience": ZT["TOKEN_AUDIENCE"]})


class DeviceEnrollView(APIView):
    public_endpoint = True
    authentication_classes = []

    def post(self, request):
        _require_xhr(request)
        code = str(request.data.get("code", "")).strip().upper().replace("-", "").replace(" ", "")
        ip = _ip(request)
        recent_failures = LoginAttempt.objects.filter(ip=ip, success=False, reason="enroll", at__gte=timezone.now() - timedelta(minutes=15)).count()
        if recent_failures >= 10:
            return Response({"error": "throttled", "message": "Too many attempts. Try later."}, status=429)
        now = timezone.now()
        device = Device.objects.filter(status="pending", enrollment_code_hash=sha256(code), enrollment_expires__gt=now).first() if code else None
        if device is None:
            LoginAttempt.objects.create(username="(device)", ip=ip, success=False, reason="enroll")
            return Response({"error": "invalid_code", "message": "Enrolment code is invalid or expired."}, status=400)
        secret = random_token(32)
        device.secret_hash = sha256(secret)
        device.status = "active"
        device.enrollment_code_hash = ""
        device.enrolled_at = now
        device.enrolled_ip = ip
        device.save()
        audit.record("device_enrolled", "core.device", device, changes={"ip": ip, "name": device.name})
        resp = Response({"device": {"id": str(device.id), "name": device.name, "kind": device.kind}})
        resp.set_cookie(
            DEVICE_COOKIE, f"{device.id}.{secret}", max_age=400 * 86400, httponly=True,
            secure=ZT["COOKIE_SECURE"], samesite="Strict", path="/",
        )
        return resp


class DeviceStatusView(APIView):
    public_endpoint = True
    authentication_classes = []

    def get(self, request):
        device = resolve_device(request)
        return Response({
            "enrolled": device is not None,
            "required": ZT["REQUIRE_ENROLLED_DEVICE"],
            "device": {"id": str(device.id), "name": device.name, "kind": device.kind} if device else None,
        })


def _mfa_required(user):
    levels = set(ZT["MFA_REQUIRED_ROLE_LEVELS"])
    return bool(levels & set(role_levels(user)))


def _start_session(request, user, device, mfa_verified):
    refresh = random_token(48)
    now = timezone.now()
    session = AuthSession.objects.create(
        user=user, device=device, refresh_hash=sha256(refresh), mfa_verified=mfa_verified, ip=_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300], last_seen=now,
        expires_at=now + timedelta(seconds=ZT["SESSION_ABSOLUTE_SECONDS"]),
    )
    user.failed_logins = 0
    user.locked_until = None
    user.last_login = now
    user.save(update_fields=["failed_logins", "locked_until", "last_login"])
    audit.set_request_context(user=user, session_id=session.pk, device_id=device.pk if device else None)
    audit.record("login", "core.session", session, changes={"device": str(device.pk) if device else None, "mfa": mfa_verified})
    resp = Response(_session_payload(user, session))
    _set_refresh_cookie(resp, refresh)
    return resp


def _set_refresh_cookie(resp, refresh):
    resp.set_cookie(
        REFRESH_COOKIE, refresh, max_age=ZT["SESSION_ABSOLUTE_SECONDS"], httponly=True,
        secure=ZT["COOKIE_SECURE"], samesite="Strict", path="/api/auth",
    )


def _session_payload(user, session):
    return {
        "access_token": issue_access_token(user, session),
        "expires_in": ZT["ACCESS_TOKEN_SECONDS"],
        "session_expires_at": session.expires_at,
        "user": profile(user),
    }


def profile(user):
    slot = shift_for()
    return {
        "id": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "department": user.department.name if user.department_id else None,
        "job_title": user.job_title,
        "roles": role_codes(user),
        "levels": sorted(role_levels(user)),
        "permissions": sorted(effective_permissions(user)),
        "mfa_enabled": user.mfa_enabled,
        "must_change_password": user.must_change_password,
        "ui_preferences": user.ui_preferences or {},
        "current_shift": {"date": slot.shift_date, "shift": slot.shift, "label": slot.label},
    }


def _resolve_login_device(request):
    device = resolve_device(request)
    if ZT["REQUIRE_ENROLLED_DEVICE"] and device is None:
        raise BusinessRuleError("This device is not enrolled. Ask IT to enrol it before signing in.", "device_not_enrolled")
    return device


class LoginView(APIView):
    public_endpoint = True
    authentication_classes = []

    def post(self, request):
        _require_xhr(request)
        username = str(request.data.get("username", "")).strip().lower()[:60]
        password = str(request.data.get("password", ""))
        ip = _ip(request)
        device = _resolve_login_device(request)
        now = timezone.now()
        ip_failures = LoginAttempt.objects.filter(ip=ip, success=False, at__gte=now - timedelta(minutes=15)).count()
        if ip_failures >= 25:
            return Response({"error": "throttled", "message": "Too many failed attempts from this network address."}, status=429)

        user = User.objects.filter(username=username).select_related("department").first()
        if user and user.locked_until and user.locked_until > now:
            LoginAttempt.objects.create(username=username, ip=ip, device_id=getattr(device, "pk", None), success=False, reason="locked")
            check_password(password, DUMMY_HASH)  # equal timing
            # Same response as bad credentials: do not reveal that the account exists or is locked (OWASP ASVS 2.2).
            return Response({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR}, status=401)
        valid = check_password(password, user.password) if user else check_password(password, DUMMY_HASH) and False
        if not user or not valid or not user.is_active:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= ZT["MAX_FAILED_LOGINS"]:
                    user.locked_until = now + timedelta(seconds=ZT["LOCKOUT_SECONDS"])
                    audit.record("account_locked", "core.user", user, actor=None, changes={"failed_logins": user.failed_logins})
                user.save(update_fields=["failed_logins", "locked_until"])
            LoginAttempt.objects.create(username=username, ip=ip, device_id=getattr(device, "pk", None), success=False, reason="bad_credentials")
            return Response({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR}, status=401)
        if user.active_until and user.active_until < timezone.localdate():
            return Response({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR}, status=401)
        if device and device.restricted_to.exists() and not device.restricted_to.filter(pk=user.pk).exists():
            LoginAttempt.objects.create(username=username, ip=ip, device_id=device.pk, success=False, reason="device_restricted")
            return Response({"error": "device_not_permitted", "message": "This account may not sign in on this device."}, status=403)

        LoginAttempt.objects.create(username=username, ip=ip, device_id=getattr(device, "pk", None), success=True)
        if _mfa_required(user):
            purpose = "mfa" if user.mfa_enabled else "mfa_setup"
            return Response({"mfa_required": True, "mfa_setup_required": not user.mfa_enabled, "mfa_token": issue_mfa_token(user, getattr(device, "pk", None), purpose)})
        return _start_session(request, user, device, mfa_verified=False)


def _user_from_mfa_token(request, token, purposes):
    for purpose in purposes:
        try:
            claims = decode_token(token, audience="kantam-mfa", typ=purpose)
            break
        except jwt.InvalidTokenError:
            claims = None
    if not claims:
        raise BusinessRuleError("MFA step expired. Sign in again.", "mfa_expired")
    user = User.objects.filter(pk=claims["sub"], is_active=True).first()
    if not user:
        raise BusinessRuleError("MFA step expired. Sign in again.", "mfa_expired")
    device = _resolve_login_device(request)
    if str(getattr(device, "pk", None)) != str(claims.get("did")) and claims.get("did"):
        raise BusinessRuleError("Device changed during sign-in.", "device_mismatch")
    return user, device, claims["typ"]


class MfaSetupView(APIView):
    public_endpoint = True
    authentication_classes = []

    def post(self, request):
        _require_xhr(request)
        user, _, typ = _user_from_mfa_token(request, str(request.data.get("mfa_token", "")), ["mfa_setup"])
        if user.mfa_enabled:
            raise BusinessRuleError("MFA already enrolled.", "invalid_state")
        if not user.mfa_secret:
            user.mfa_secret = pyotp.random_base32()
            user.save(update_fields=["mfa_secret"])
        uri = pyotp.TOTP(user.mfa_secret).provisioning_uri(name=user.username, issuer_name="Kantam ERP")
        return Response({"otpauth_uri": uri, "secret": user.mfa_secret})


class MfaVerifyView(APIView):
    public_endpoint = True
    authentication_classes = []

    def post(self, request):
        _require_xhr(request)
        user, device, typ = _user_from_mfa_token(request, str(request.data.get("mfa_token", "")), ["mfa", "mfa_setup"])
        code = str(request.data.get("code", "")).strip()
        if not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1):
            user.failed_logins += 1
            if user.failed_logins >= ZT["MAX_FAILED_LOGINS"]:
                user.locked_until = timezone.now() + timedelta(seconds=ZT["LOCKOUT_SECONDS"])
            user.save(update_fields=["failed_logins", "locked_until"])
            return Response({"error": "invalid_code", "message": "Invalid authentication code."}, status=401)
        if typ == "mfa_setup" and not user.mfa_enabled:
            user.mfa_enabled = True
            user.save(update_fields=["mfa_enabled"])
            audit.record("mfa_enrolled", "core.user", user, actor=user)
        return _start_session(request, user, device, mfa_verified=True)


class RefreshView(APIView):
    public_endpoint = True
    authentication_classes = []

    def post(self, request):
        _require_xhr(request)
        raw = request.COOKIES.get(REFRESH_COOKIE, "")
        if not raw:
            return Response({"error": "unauthenticated", "reason": "no_session"}, status=401)
        h = sha256(raw)
        now = timezone.now()
        with transaction.atomic():
            session = AuthSession.objects.select_for_update().select_related("user").filter(refresh_hash=h).first()
            if session is None:
                reused = AuthSession.objects.filter(previous_refresh_hash=h).first()
                if reused:
                    AuthSession.objects.filter(family=reused.family, revoked_at__isnull=True).update(revoked_at=now, revoke_reason="refresh_reuse_detected")
                    audit.record("refresh_reuse_detected", "core.session", reused, actor=reused.user)
                return self._fail("session_invalid")
            if session.revoked_at or session.expires_at <= now:
                return self._fail("session_revoked")
            if session.last_seen + timedelta(seconds=ZT["SESSION_IDLE_SECONDS"]) < now:
                session.revoked_at = now
                session.revoke_reason = "idle_timeout"
                session.save(update_fields=["revoked_at", "revoke_reason"])
                return self._fail("session_idle_timeout")
            user = session.user
            if not user.is_active or (user.locked_until and user.locked_until > now):
                return self._fail("account_disabled")
            device = resolve_device(request)
            if ZT["REQUIRE_ENROLLED_DEVICE"] and (device is None or device.pk != session.device_id):
                return self._fail("device_not_trusted")
            if _mfa_required(user) and not session.mfa_verified:
                return self._fail("mfa_required")
            new_refresh = random_token(48)
            session.previous_refresh_hash = h
            session.refresh_hash = sha256(new_refresh)
            session.last_seen = now
            session.save(update_fields=["previous_refresh_hash", "refresh_hash", "last_seen"])
        resp = Response(_session_payload(user, session))
        _set_refresh_cookie(resp, new_refresh)
        return resp

    def _fail(self, reason):
        resp = Response({"error": "unauthenticated", "reason": reason}, status=401)
        resp.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        return resp


class LogoutView(APIView):
    required_permission = "authenticated"
    allow_during_password_change = True

    def post(self, request):
        session = request.zt_session
        session.revoked_at = timezone.now()
        session.revoke_reason = "logout"
        session.save(update_fields=["revoked_at", "revoke_reason"])
        audit.record("logout", "core.session", session)
        resp = Response({"ok": True})
        resp.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        return resp


class MeView(APIView):
    required_permission = "authenticated"
    allow_during_password_change = True

    def get(self, request):
        data = profile(request.user)
        data["session"] = {"id": str(request.zt_session.pk), "expires_at": request.zt_session.expires_at, "mfa": request.zt_session.mfa_verified}
        data["device"] = {"id": str(request.zt_device.pk), "name": request.zt_device.name} if request.zt_device else None
        return Response(data)


class ChangePasswordView(APIView):
    required_permission = "authenticated"
    allow_during_password_change = True

    def post(self, request):
        user = request.user
        if not check_password(str(request.data.get("current_password", "")), user.password):
            raise BusinessRuleError("Current password is incorrect.", "invalid_credentials")
        new = str(request.data.get("new_password", ""))
        if check_password(new, user.password):
            raise BusinessRuleError("The new password must differ from the current one.", "password_reuse")
        password_validation.validate_password(new, user)
        user.set_password(new)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        # Revoke every other session for this user.
        AuthSession.objects.filter(user=user, revoked_at__isnull=True).exclude(pk=request.zt_session.pk).update(revoked_at=timezone.now(), revoke_reason="password_changed")
        audit.record("password_changed", "core.user", user)
        return Response({"ok": True})


def new_enrollment_code():
    raw = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}", sha256(raw)


THEME_MODES = {"light", "dark", "system"}
THEME_PRESETS = {"kantam", "book", "steel", "blue", "green", "custom"}
SIDEBAR_STYLES = {"dark", "navy", "light", "brand"}
DENSITIES = {"comfortable", "compact"}
TEXT_SIZES = {"normal", "large", "xlarge"}
HEX = __import__("re").compile(r"^#[0-9a-fA-F]{6}$")


class PreferencesView(APIView):
    """Per-user display preferences. Strictly validated - values end up in CSS variables."""

    required_permission = "authenticated"

    def patch(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        prefs = dict(request.user.ui_preferences or {})
        checks = {"mode": THEME_MODES, "preset": THEME_PRESETS, "sidebar": SIDEBAR_STYLES, "density": DENSITIES, "text": TEXT_SIZES}
        for key, allowed in checks.items():
            if key in data:
                if data[key] not in allowed:
                    raise BusinessRuleError(f"Invalid {key}.", "validation")
                prefs[key] = data[key]
        if "accent" in data:
            if not isinstance(data["accent"], str) or not HEX.match(data["accent"]):
                raise BusinessRuleError("Accent must be a #RRGGBB colour.", "validation")
            prefs["accent"] = data["accent"].lower()
        if data.get("reset"):
            prefs = {}
        request.user.ui_preferences = prefs
        request.user.save(update_fields=["ui_preferences"])
        return Response({"ui_preferences": prefs})
