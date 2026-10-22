"""Shared API plumbing: errors, pagination, idempotent posting, optimistic locking."""
import functools
import hashlib
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .models import IdempotencyRecord


class BusinessRuleError(APIException):
    """A domain rule blocked the action (e.g. wrong artwork, QC hold, locked shift)."""

    status_code = 409
    default_code = "rule_violation"

    def __init__(self, message, code="rule_violation", details=None):
        self.detail = {"error": code, "message": message, "details": details or {}}


class VersionConflict(APIException):
    status_code = 409
    default_code = "version_conflict"

    def __init__(self, current_version):
        self.detail = {
            "error": "version_conflict",
            "message": "This record was changed by someone else. Reload and re-apply your change.",
            "current_version": current_version,
        }


class StandardPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 500


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        return Response({"error": "validation", "message": "; ".join(exc.messages)}, status=400)
    if isinstance(exc, IntegrityError):
        return Response({"error": "integrity", "message": "Duplicate or conflicting record."}, status=409)
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "error" not in response.data:
        response.data = {"error": getattr(exc, "default_code", "error"), "message": response.data.get("detail", response.data)}
    return response


def idempotent(view_func):
    """
    Posting endpoints accept an `Idempotency-Key` header. A retry with the same
    key returns the original response instead of posting twice (brief p.4).
    """

    @functools.wraps(view_func)
    def wrapper(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            return view_func(self, request, *args, **kwargs)
        if len(key) > 80:
            return Response({"error": "validation", "message": "Idempotency-Key too long"}, status=400)
        body = json.dumps(request.data if isinstance(request.data, dict) else {}, sort_keys=True, default=str)
        req_hash = hashlib.sha256(f"{request.path}|{body}".encode()).hexdigest()
        existing = IdempotencyRecord.objects.filter(key=key, user=request.user).first()
        if existing:
            if existing.request_hash != req_hash:
                return Response({"error": "idempotency_mismatch", "message": "Key reused with a different request."}, status=422)
            resp = Response(existing.response, status=existing.status_code)
            resp["Idempotent-Replay"] = "true"
            return resp
        try:
            # The posting and its idempotency record commit together or not at all.
            with transaction.atomic():
                response = view_func(self, request, *args, **kwargs)
                if 200 <= response.status_code < 300:
                    IdempotencyRecord.objects.create(
                        key=key, user=request.user, endpoint=request.path, request_hash=req_hash,
                        status_code=response.status_code, response=json.loads(json.dumps(response.data, default=str)),
                    )
        except IntegrityError:
            # A concurrent retry won the race: this attempt was rolled back; replay the winner.
            existing = IdempotencyRecord.objects.filter(key=key, user=request.user).first()
            if not existing:
                raise
            resp = Response(existing.response, status=existing.status_code)
            resp["Idempotent-Replay"] = "true"
            return resp
        return response

    return wrapper


def require_reason(data, field="reason", min_len=5):
    reason = (data.get(field) or "").strip()
    if len(reason) < min_len:
        raise BusinessRuleError("A reason is required for this change.", "reason_required")
    return reason


def ok(data=None, code=status.HTTP_200_OK):
    return Response(data or {"ok": True}, status=code)
