"""Generic REST endpoints driven by the resource registry."""
import csv
import io
import json

from django.db import models, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from . import audit, maker_checker
from .api import BusinessRuleError, VersionConflict, idempotent
from .models import AuditLog
from .rbac import can_delete_records, effective_permissions, has_perm
from .registry import MODULES, REGISTRY, resource_for_model
from .services import suppress_stale_outbox

FIELD_TYPES = [
    (models.ManyToManyField, "fk_multi"),
    (models.ForeignKey, "fk"),
    (models.OneToOneField, "fk"),
    (models.BooleanField, "boolean"),
    (models.DateTimeField, "datetime"),
    (models.DateField, "date"),
    (models.TimeField, "time"),
    (models.DecimalField, "decimal"),
    (models.FloatField, "decimal"),
    (models.IntegerField, "integer"),
    (models.JSONField, "json"),
    (models.TextField, "textarea"),
    (models.EmailField, "email"),
    (models.CharField, "text"),
    (models.UUIDField, "text"),
]


def csv_safe(value):
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR) in exported cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def get_resource(key):
    res = REGISTRY.get(key)
    if res is None:
        raise Http404("Unknown resource")
    return res


def visible_fields(res, user, names):
    perms = effective_permissions(user)
    return [n for n in names if res.field_permissions.get(n) is None or res.field_permissions[n] in perms]


def model_field(res, name):
    try:
        return res.model._meta.get_field(name)
    except Exception:
        return None


def field_meta(res, name, user, writable=False):
    f = model_field(res, name)
    meta = {"name": name, "label": name.replace("_", " ").capitalize(), "type": "text", "readonly": not writable}
    if f is None:
        meta["type"] = "computed"
        return meta
    meta["label"] = str(getattr(f, "verbose_name", name)).capitalize()
    for cls, kind in FIELD_TYPES:
        if isinstance(f, cls):
            meta["type"] = kind
            break
    if getattr(f, "choices", None):
        meta["type"] = "choice"
        meta["choices"] = [{"value": v, "label": str(lbl)} for v, lbl in f.choices]
    if isinstance(f, (models.ForeignKey, models.ManyToManyField)):
        target = resource_for_model(f.related_model)
        meta["target"] = target.key if target else None
        if f.related_model._meta.label == "core.User":
            meta["target"] = "core.userlookup"
    if isinstance(f, models.DecimalField):
        meta["decimals"] = f.decimal_places
    meta["required"] = writable and (name in res.required_fields or (not (f.null or f.blank or f.has_default()) and name not in res.server_defaults))
    if f.help_text:
        meta["help"] = str(f.help_text)
    if name in res.field_help:
        meta["help"] = res.field_help[name]
    if name in res.depends:
        meta["depends"] = res.depends[name]
    if name in res.filter_defaults:
        meta["filter_default"] = res.filter_defaults[name]
    if name in res.show_if:
        meta["show_if"] = res.show_if[name]
    return meta


def resource_meta(res, user):
    perms = effective_permissions(user)
    form = visible_fields(res, user, res.form_fields)
    create_only = visible_fields(res, user, res.create_only_fields)
    listed = visible_fields(res, user, res.list_fields)
    readonly = visible_fields(res, user, res.readonly_fields)
    all_names = list(dict.fromkeys(listed + create_only + form + readonly))
    return {
        "key": res.key,
        "label": res.label,
        "nav": res.in_nav,
        "singular": res.singular or res.label,
        "module": res.module,
        "title_field": res.title_field,
        "status_field": res.status_field if model_field(res, res.status_field) else None,
        "list_fields": listed,
        "form_fields": form,
        "create_only_fields": create_only,
        "fields": {n: field_meta(res, n, user, writable=n in form or n in create_only) for n in all_names},
        "filters": [field_meta(res, n, user) for n in res.filter_fields],
        "searchable": bool(res.search_fields),
        "can_create": res.allow_create and res.perm("create") in perms,
        "can_update": res.allow_update and res.perm("update") in perms,
        "can_export": res.perm("export") in perms,
        "can_delete": can_delete(res, user),
        "actions": [
            {
                "name": a.name, "label": a.label, "fields": a.fields, "reason_required": a.reason_required,
                "confirm": a.confirm, "tone": a.tone, "detail": a.detail,
            }
            for a in res.actions if a.perm(res.key) in perms
        ],
        "inlines": [i for i in res.inlines if i["resource"] in REGISTRY and f"{i['resource']}.view" in perms],
        "help": res.help,
        "defaults": res.form_defaults(user) if res.allow_create and res.perm("create") in perms else {},
        "required_uploads": res.required_uploads,
    }


def build_read_serializer(res, user, names):
    names = visible_fields(res, user, names)
    concrete = [n for n in names if model_field(res, n) is not None]
    computed = [n for n in names if model_field(res, n) is None]
    fk_names = [n for n in concrete if isinstance(model_field(res, n), models.ForeignKey)]
    m2m_names = [n for n in concrete if isinstance(model_field(res, n), models.ManyToManyField)]
    attrs = {}
    for n in fk_names:
        attrs[f"{n}_label"] = serializers.SerializerMethodField()
        attrs[f"get_{n}_label"] = (lambda fname: lambda self, obj: str(getattr(obj, fname)) if getattr(obj, f"{fname}_id") else None)(n)
    for n in m2m_names:
        # "_label" for lists and exports; "_items" so an edit form can show each chosen record.
        attrs[f"{n}_label"] = serializers.SerializerMethodField()
        attrs[f"get_{n}_label"] = (lambda fname: lambda self, obj: ", ".join(str(o) for o in getattr(obj, fname).all()))(n)
        attrs[f"{n}_items"] = serializers.SerializerMethodField()
        attrs[f"get_{n}_items"] = (lambda fname: lambda self, obj: [{"id": str(o.pk), "label": str(o)} for o in getattr(obj, fname).all()])(n)
    for n in computed:
        attrs[n] = serializers.SerializerMethodField()
        attrs[f"get_{n}"] = (lambda fname: lambda self, obj: _computed(getattr(obj, fname, None)))(n)
    extra = ["id"] + (["row_version"] if model_field(res, "row_version") else [])
    meta = type("Meta", (), {"model": res.model, "fields": list(dict.fromkeys(extra + concrete + list(attrs_keys(attrs))))})
    attrs["Meta"] = meta
    return type(f"{res.model.__name__}ReadSerializer", (serializers.ModelSerializer,), attrs)


def attrs_keys(attrs):
    return [k for k in attrs if not k.startswith("get_")]


def _computed(value):
    if callable(value):
        value = value()
    if hasattr(value, "quantize"):
        return str(value)
    if isinstance(value, models.Model):
        return str(value)
    return value


def build_write_serializer(res, user, creating):
    names = visible_fields(res, user, res.form_fields + (res.create_only_fields if creating else []))
    names = [n for n in names if model_field(res, n) is not None]
    extra = {n: {"required": False} for n in res.server_defaults if n in names}
    meta = type("Meta", (), {"model": res.model, "fields": names, "extra_kwargs": extra})
    return type(f"{res.model.__name__}WriteSerializer", (serializers.ModelSerializer,), {"Meta": meta})


def check_fk_access(request, res, data):
    """A user may only reference records they are allowed to see (no blind ID guessing)."""
    for name, value in data.items():
        f = model_field(res, name)
        if not isinstance(f, (models.ForeignKey, models.ManyToManyField)) or value is None:
            continue
        target = resource_for_model(f.related_model)
        if target is None:
            continue
        pks = [v.pk for v in value] if isinstance(f, models.ManyToManyField) else [value.pk]
        if pks and (not has_perm(request.user, target.perm("view")) or target.get_queryset(request).filter(pk__in=pks).count() != len(set(pks))):
            raise BusinessRuleError(f"You cannot reference this {target.singular or target.label}.", "reference_denied", {"field": name})


def _pop_m2m(res, data):
    return {n: data.pop(n) for n in list(data) if isinstance(model_field(res, n), models.ManyToManyField)}


def _m2m_snapshot(obj, names):
    return {n: sorted(str(o.pk) for o in getattr(obj, n).all()) for n in names}


def scoped_get(request, key, pk, label=None):
    """Resolve an ID supplied in an action payload through the target resource's
    permission and row scope - never a raw lookup (no blind ID guessing)."""
    res = get_resource(key)
    label = label or res.singular or res.label
    if not pk:
        raise BusinessRuleError(f"Select a {label}.", "validation")
    if not has_perm(request.user, res.perm("view")):
        raise BusinessRuleError(f"You cannot reference this {label}.", "reference_denied")
    try:
        obj = res.get_queryset(request).filter(pk=pk).first()
    except (ValueError, Exception):
        obj = None
    if obj is None:
        raise BusinessRuleError(f"Select a valid {label}.", "validation")
    return obj


def serialize(res, request, obj, names=None):
    names = names or list(dict.fromkeys(res.list_fields + res.create_only_fields + res.form_fields + res.readonly_fields))
    data = build_read_serializer(res, request.user, names)(obj).data
    block = res.edit_block_reason(request, obj)
    perms = effective_permissions(request.user)
    data["_meta"] = {
        "pending_approvals": maker_checker.pending_for(res.key, obj.pk),
        "editable": res.allow_update and res.perm("update") in perms and block is None,
        "locked_reason": block,
        "deletable": can_delete(res, request.user) and res.delete_block_reason(request, obj) is None,
        "actions": [
            a.name for a in res.actions
            if a.detail and a.perm(res.key) in perms and (a.available is None or a.available(obj, request))
        ],
    }
    return data


def perform_create(request, res, raw):
    ser = build_write_serializer(res, request.user, creating=True)(data=raw)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    check_fk_access(request, res, data)
    res.before_create(request, data)
    m2m = _pop_m2m(res, data)
    obj = res.create(request, data)
    for name, values in m2m.items():
        getattr(obj, name).set(values)
    snap = {**audit.snapshot(obj), **_m2m_snapshot(obj, m2m)}
    audit.record("create", res.key, obj, changes=snap)
    res.after_create(request, obj)
    return obj, snap


def store_required_uploads(request, res, obj):
    from .attachments import store_attachment

    for spec in res.required_uploads:
        att, _ = store_attachment(parent=obj, purpose=spec["purpose"], upload=request.FILES[spec["purpose"]], user=request.user)
        allowed = [m.strip() for m in spec.get("accept", "").split(",") if m.strip()]
        if allowed and not any(att.mime_type == m or (m.endswith("/*") and att.mime_type.startswith(m[:-1])) for m in allowed):
            raise BusinessRuleError(f"{spec['label']}: wrong file type ({att.mime_type}).", "invalid_file")
        audit.record("attach", res.key, obj, changes={"attachment": str(att.id), "purpose": spec["purpose"], "sha256": att.sha256})


def perform_update(request, res, obj, raw):
    block = res.edit_block_reason(request, obj)
    if block:
        raise BusinessRuleError(block, "record_locked")
    if hasattr(obj, "row_version"):
        sent = raw.get("row_version")
        if sent is None or int(sent) != obj.row_version:
            raise VersionConflict(obj.row_version)
    ser = build_write_serializer(res, request.user, creating=False)(obj, data=raw, partial=True)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    check_fk_access(request, res, data)
    m2m_names = [n for n in data if isinstance(model_field(res, n), models.ManyToManyField)]
    before = {**audit.snapshot(obj), **_m2m_snapshot(obj, m2m_names)}
    res.before_update(request, obj, data)
    m2m = _pop_m2m(res, data)
    for k, v in data.items():
        setattr(obj, k, v)
    if hasattr(obj, "row_version"):
        obj.row_version += 1
    obj.full_clean(exclude=res._fk_exclusions())
    obj.save()
    for name, values in m2m.items():
        getattr(obj, name).set(values)
    changes = audit.diff(before, {**audit.snapshot(obj), **_m2m_snapshot(obj, m2m_names)})
    if changes:
        audit.record("update", res.key, obj, changes=changes, reason=str(raw.get("reason", "")))
        src_key, src_id = res.outbox_source(obj)
        suppress_stale_outbox(src_key, src_id, f"Source amended by {request.user.username} after the message was drafted")
    res.after_update(request, obj, changes)
    return obj, changes


def can_delete(res, user):
    return res.allow_delete and can_delete_records(user)


def in_use_message(label, protected):
    """'Still used by 3 sales orders (SO-1, SO-2, SO-3) and 1 stock movement' for a blocked delete."""
    groups = {}
    for o in protected:
        groups.setdefault(o._meta.model, []).append(o)
    parts = []
    for model, objs in groups.items():
        name = model._meta.verbose_name if len(objs) == 1 else model._meta.verbose_name_plural
        examples = ", ".join(str(o) for o in objs[:3]) + (", ..." if len(objs) > 3 else "")
        parts.append(f"{len(objs)} {name} ({examples})")
    msg = f"{label} cannot be deleted: it is still used by " + "; ".join(parts) + "."
    if AuditLog in groups:
        return msg + " Audit history is never deleted, so switch this record off (e.g. untick Active) instead."
    return msg + " Delete or change those first, or switch this record off (e.g. untick Active) instead."


def _delete_tree(request, res, obj, reason, count):
    for spec in res.inlines:
        child = REGISTRY.get(spec["resource"])
        owned = child.deleted_with_parent if child is not None and child.deleted_with_parent is not None else (child is not None and not child.in_nav)
        if child is None or not owned or child.model is res.model:
            continue  # only true child lines (kept out of the menu) go with their parent
        for line in child.model._default_manager.filter(**{spec["fk"]: obj}):
            _delete_tree(request, child, line, reason, count)
    block = res.delete_block_reason(request, obj)
    if block:
        raise BusinessRuleError(block, "delete_not_allowed")
    res.before_delete(request, obj)
    audit.record("delete", res.key, obj, changes=audit.snapshot(obj), reason=reason)
    label = f"{res.singular or res.label} {obj}"
    try:
        with transaction.atomic():
            res.destroy(request, obj)
    except (models.ProtectedError, models.RestrictedError) as exc:
        objs = getattr(exc, "protected_objects", None) or getattr(exc, "restricted_objects", None) or []
        raise BusinessRuleError(in_use_message(label, list(objs)), "record_in_use")
    count[0] += 1


def perform_delete(request, res, obj, reason):
    if not can_delete(res, request.user):
        raise BusinessRuleError("Only the owner or an administrator can delete records.", "not_authorised")
    if len(reason.strip()) < 5:
        raise BusinessRuleError("Give a reason (at least 5 characters) for deleting.", "reason_required")
    count = [0]
    _delete_tree(request, res, obj, reason, count)
    return count[0]


def perform_action(request, res, act, obj, data):
    if act.reason_required and len(str(data.get("reason", "")).strip()) < 5:
        raise BusinessRuleError("A reason is required for this action.", "reason_required")
    if act.detail and act.available is not None and not act.available(obj, request):
        raise BusinessRuleError(f"'{act.label}' is not available in the current state.", "invalid_state")
    before = audit.snapshot(obj) if obj is not None else {}
    result = act.handler(request, obj, data)
    after = audit.snapshot(obj) if obj is not None else {}
    return result, audit.diff(before, after)


def action_response(request, res, result):
    if isinstance(result, models.Model):
        target = res if isinstance(result, res.model) else (resource_for_model(type(result)) or res)
        payload = serialize(target, request, result)
        payload["_resource"] = target.key
        return Response(payload)
    return Response(result if result is not None else {"ok": True})


class ResourceBase(APIView):
    def res(self):
        return get_resource(self.kwargs["key"])

    def get_object(self, res, request, lock=False):
        qs = res.get_queryset(request)
        if lock:
            qs = qs.select_for_update()
        obj = qs.filter(pk=self.kwargs["pk"]).first()
        if obj is None:
            raise Http404("Not found")
        return obj


class MetaView(APIView):
    required_permission = "authenticated"

    def get(self, request):
        perms = effective_permissions(request.user)
        modules = []
        for code, label in MODULES:
            items = [resource_meta(r, request.user) for r in REGISTRY.values() if r.module == code and r.perm("view") in perms]
            if items:
                modules.append({"key": code, "label": label, "resources": items})
        return Response({"modules": modules})


class ResourceMetaView(ResourceBase):
    def get_required_permission(self, request):
        return self.res().perm("view")

    def get(self, request, key):
        return Response(resource_meta(self.res(), request.user))


class ResourceListView(ResourceBase):
    def get_required_permission(self, request):
        return self.res().perm("view" if request.method == "GET" else "create")

    def filtered(self, request, res):
        qs = res.get_queryset(request)
        params = request.query_params
        for name in res.filter_fields + ["id"]:
            value = params.get(name)
            if value in (None, ""):
                continue
            if "," in value:
                qs = qs.filter(**{f"{name}__in": value.split(",")})
            else:
                qs = qs.filter(**{name: value})
        search = (params.get("search") or "").strip()
        if search and res.search_fields:
            cond = Q()
            for sf in res.search_fields:
                cond |= Q(**{f"{sf}__icontains": search})
            qs = qs.filter(cond)
        if params.get("created_after"):
            qs = qs.filter(created_at__date__gte=params["created_after"])
        if params.get("created_before"):
            qs = qs.filter(created_at__date__lte=params["created_before"])
        ordering = params.get("ordering")
        allowed = set(res.list_fields) | {"created_at"}
        if ordering and ordering.lstrip("-") in allowed and model_field(res, ordering.lstrip("-")):
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by(*res.ordering)
        return qs

    def get(self, request, key):
        res = self.res()
        qs = self.filtered(request, res)
        paginator = self.paginator
        page = paginator.paginate_queryset(qs, request, view=self)
        names = res.list_fields
        ser = build_read_serializer(res, request.user, names)
        return paginator.get_paginated_response(ser(page, many=True).data)

    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            from .api import StandardPagination
            self._paginator = StandardPagination()
        return self._paginator

    @idempotent
    def post(self, request, key):
        res = self.res()
        if not res.allow_create:
            raise BusinessRuleError("Records of this type are created by their workflow, not directly.", "create_not_allowed")
        raw = request.data if isinstance(request.data, dict) else {}
        if request.content_type.startswith("multipart/"):
            # A form with required files: the fields travel as JSON in "payload", each file under its purpose.
            try:
                raw = json.loads(request.data.get("payload") or "{}")
            except ValueError:
                raise BusinessRuleError("The form could not be read. Reload the page and try again.", "validation")
        missing = [u["label"] for u in res.required_uploads if not request.FILES.get(u["purpose"])]
        if missing:
            raise BusinessRuleError(f"Attach: {', '.join(missing)}.", "upload_required", {"missing": missing})
        with transaction.atomic():
            obj, _ = perform_create(request, res, raw)
            store_required_uploads(request, res, obj)
        payload = serialize(res, request, obj)
        payload["_extra"] = res.extra_detail(request, obj)
        return Response(payload, status=201)


class ResourceDetailView(ResourceBase):
    def get_required_permission(self, request):
        # DELETE is checked against the owner / admin level in perform_delete.
        return self.res().perm("view" if request.method in ("GET", "DELETE") else "update")

    def get(self, request, key, pk):
        res = self.res()
        obj = self.get_object(res, request)
        data = serialize(res, request, obj)
        data["_extra"] = res.extra_detail(request, obj)
        return Response(data)

    @idempotent
    def patch(self, request, key, pk):
        res = self.res()
        if not res.allow_update:
            raise BusinessRuleError("This record type cannot be edited directly.", "update_not_allowed")
        raw = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            obj = self.get_object(res, request, lock=True)
            obj, _ = perform_update(request, res, obj, raw)
        return Response(serialize(res, request, obj))

    def delete(self, request, key, pk):
        res = self.res()
        raw = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            obj = self.get_object(res, request, lock=True)
            label = str(obj)
            count = perform_delete(request, res, obj, str(raw.get("reason", "")))
        extra = f" with {count - 1} line(s)" if count > 1 else ""
        return Response({"ok": True, "message": f"{res.singular or res.label} {label} deleted{extra}."})


class ResourceActionView(ResourceBase):
    def action(self):
        res = self.res()
        action = res.get_action(self.kwargs["action"])
        if action is None:
            raise Http404("Unknown action")
        return res, action

    def get_required_permission(self, request):
        res, action = self.action()
        return action.perm(res.key)

    @idempotent
    def post(self, request, key, action, pk=None):
        res, act = self.action()
        data = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            obj = self.get_object(res, request, lock=True) if act.detail else None
            result, _ = perform_action(request, res, act, obj, data)
        return action_response(request, res, result)


class ResourceHistoryView(ResourceBase):
    def get_required_permission(self, request):
        return self.res().perm("view")

    def get(self, request, key, pk):
        res = self.res()
        obj = self.get_object(res, request)
        entries = AuditLog.objects.filter(resource=res.key, object_id=str(obj.pk)).order_by("-id")[:200]
        hidden = {f for f, p in res.field_permissions.items() if not has_perm(request.user, p)}
        return Response([
            {
                "at": e.at, "actor": e.actor_label, "action": e.action, "reason": e.reason, "version": e.record_version,
                "approval_ref": e.approval_ref,
                "changes": {k: v for k, v in (e.changes or {}).items() if k not in hidden},
            }
            for e in entries
        ])


class ResourceExportView(ResourceListView):
    http_method_names = ["get", "options"]

    def get_required_permission(self, request):
        return self.res().perm("export")

    def get(self, request, key):
        res = self.res()
        qs = self.filtered(request, res)[:20000]
        names = visible_fields(res, request.user, res.list_fields)
        ser = build_read_serializer(res, request.user, names)
        buf = io.StringIO()
        writer = csv.writer(buf)
        safe = csv_safe
        stamp = timezone.localtime().strftime("%d-%m-%Y %H:%M")
        writer.writerow([f"CONFIDENTIAL - Kantam Kontainers LLP - exported by {request.user.full_name} ({request.user.username}) at {stamp} IST"])
        writer.writerow(["id"] + names)
        for row in ser(qs, many=True).data:
            writer.writerow([row.get("id")] + [safe(row.get(f"{n}_label") or row.get(n)) for n in names])
        audit.record("export", res.key, object_id="*", changes={"rows": qs.count(), "filters": dict(request.query_params)})
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{res.key}-{timezone.localdate()}.csv"'
        return resp
