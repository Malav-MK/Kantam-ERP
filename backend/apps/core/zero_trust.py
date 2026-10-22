"""
Zero-trust request middleware for the Django service.

The backend never trusts the network it sits on:
1. Every request must be HMAC-signed by the Node gateway (the only ingress),
   with a fresh timestamp - so the API cannot be called by bypassing the
   gateway's network/device/rate-limit policy (micro-segmentation).
2. The original client IP reported by the gateway is re-checked against the
   approved factory CIDRs (defence in depth; fail closed).
3. Responses are marked no-store so sensitive payloads are never cached.
"""
import ipaddress
import time
import uuid

from django.conf import settings
from django.http import JsonResponse

from .audit import clear_request_context, set_request_context
from .security import constant_eq, gateway_signature

ZT = settings.ZERO_TRUST
EXEMPT_PATHS = {"/healthz"}


def _networks():
    nets = []
    for cidr in ZT["ALLOWED_CIDRS"]:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return nets


ALLOWED_NETWORKS = _networks()


def ip_allowed(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in ALLOWED_NETWORKS)


def _deny(reason, status=403):
    return JsonResponse({"error": "zero_trust_denied", "reason": reason}, status=status)


class GatewayTrustMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        clear_request_context()
        if request.path in EXEMPT_PATHS:
            return self.get_response(request)

        client_ip = request.META.get("REMOTE_ADDR")
        request_id = uuid.uuid4().hex
        if ZT["REQUIRE_GATEWAY"]:
            secret = ZT["GATEWAY_SHARED_SECRET"]
            if not secret:
                return _deny("gateway secret not configured", 503)
            ts = request.headers.get("X-ZT-Timestamp", "")
            sig = request.headers.get("X-ZT-Signature", "")
            client_ip = request.headers.get("X-ZT-Client-IP", "")
            request_id = request.headers.get("X-ZT-Request-Id", "")
            try:
                skew = abs(time.time() - int(ts))
            except ValueError:
                return _deny("missing gateway timestamp")
            if skew > ZT["GATEWAY_MAX_SKEW_SECONDS"]:
                return _deny("stale gateway signature")
            expected = gateway_signature(secret, ts, request.method, request.get_full_path(), client_ip, request_id)
            if not sig or not constant_eq(sig, expected):
                return _deny("invalid gateway signature")

        if not ip_allowed(client_ip):
            return _deny("network not approved")

        request.zt_client_ip = client_ip
        request.zt_request_id = request_id
        set_request_context(ip=client_ip, request_id=request_id)
        try:
            response = self.get_response(request)
        finally:
            # Never let one request's identity leak into later work on this thread.
            clear_request_context()
        response["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Cache-Control", "no-store, max-age=0")
        response["Pragma"] = "no-cache"
        if not response.get("Content-Security-Policy"):
            response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
