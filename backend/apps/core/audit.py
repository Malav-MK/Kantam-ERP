"""
Tamper-evident audit trail.

Each entry stores the hash of the previous entry, so deleting or editing a past
row breaks the chain and `verify_chain()` reports the first broken link.
"""
import contextvars
import datetime
import decimal
import hashlib
import json
import uuid

from django.db import models, transaction
from django.utils import timezone

from .models import AuditChainHead, AuditLog

_request_ctx = contextvars.ContextVar("kantam_request_ctx", default=None)


def set_request_context(**values):
    current = dict(_request_ctx.get() or {})
    current.update(values)
    _request_ctx.set(current)


def clear_request_context():
    _request_ctx.set(None)


def request_context():
    return _request_ctx.get() or {}


def _jsonable(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(instance, fields=None):
    """Serialise concrete field values of a model instance."""
    data = {}
    for field in instance._meta.concrete_fields:
        if fields and field.name not in fields:
            continue
        data[field.name] = _jsonable(getattr(instance, field.attname))
    return data


def diff(before, after):
    changes = {}
    for key in set(before) | set(after):
        if key in {"updated_at", "row_version"}:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = [old, new]
    return changes


def _hash_entry(prev_hash, payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def record(action, resource, obj=None, *, object_id="", doc_no="", changes=None, reason="", approval_ref="", actor=None, version=None):
    ctx = request_context()
    actor = actor or ctx.get("user")
    approval_ref = approval_ref or ctx.get("approval_ref", "")
    if obj is not None:
        object_id = object_id or str(obj.pk)
        doc_no = doc_no or getattr(obj, "doc_no", "") or ""
        version = version if version is not None else getattr(obj, "row_version", None)
    changes = _jsonable(changes or {})
    at = timezone.now()
    with transaction.atomic():
        head, _ = AuditChainHead.objects.select_for_update().get_or_create(id=1)
        payload = {
            "at": at.isoformat(),
            "actor": str(actor.pk) if actor else None,
            "action": action,
            "resource": resource,
            "object_id": str(object_id),
            "doc_no": doc_no,
            "version": version,
            "changes": changes,
            "reason": reason,
            "approval_ref": str(approval_ref or ""),
        }
        entry_hash = _hash_entry(head.last_hash, payload)
        entry = AuditLog.objects.create(
            at=at,
            actor=actor,
            actor_label=str(actor) if actor else "system",
            action=action,
            resource=resource,
            object_id=str(object_id),
            doc_no=doc_no,
            record_version=version,
            changes=changes,
            reason=reason,
            approval_ref=str(approval_ref or ""),
            ip=ctx.get("ip"),
            device_id=ctx.get("device_id"),
            session_id=ctx.get("session_id"),
            request_id=ctx.get("request_id", ""),
            prev_hash=head.last_hash,
            entry_hash=entry_hash,
        )
        head.last_hash = entry_hash
        head.count += 1
        head.save(update_fields=["last_hash", "count"])
    return entry


def verify_chain():
    prev = "0" * 64
    checked = 0
    for entry in AuditLog.objects.order_by("id").iterator():
        payload = {
            "at": entry.at.isoformat(),
            "actor": str(entry.actor_id) if entry.actor_id else None,
            "action": entry.action,
            "resource": entry.resource,
            "object_id": entry.object_id,
            "doc_no": entry.doc_no,
            "version": entry.record_version,
            "changes": entry.changes,
            "reason": entry.reason,
            "approval_ref": entry.approval_ref,
        }
        if entry.prev_hash != prev or _hash_entry(prev, payload) != entry.entry_hash:
            return {"ok": False, "checked": checked, "broken_at": entry.id}
        prev = entry.entry_hash
        checked += 1
    head = AuditChainHead.objects.filter(id=1).first()
    if head and head.last_hash != prev:
        return {"ok": False, "checked": checked, "broken_at": "head (entries removed from the end)"}
    return {"ok": True, "checked": checked}
