from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import audit
from .api import StandardPagination
from .models import AuditLog, Notification, Permission, Role
from .rbac import has_perm


class NotificationsView(APIView):
    required_permission = "authenticated"

    def get(self, request):
        qs = Notification.objects.filter(user=request.user)
        if request.query_params.get("unseen") == "1":
            qs = qs.filter(seen_at__isnull=True)
        items = qs.order_by("seen_at", "-created_at")[:100]
        return Response({
            "unseen": Notification.objects.filter(user=request.user, seen_at__isnull=True).count(),
            "items": [
                {"id": str(n.id), "title": n.title, "body": n.body, "severity": n.severity, "resource": n.resource,
                 "object_id": n.object_id, "next_action": n.next_action, "due_at": n.due_at, "created_at": n.created_at,
                 "seen_at": n.seen_at}
                for n in items
            ],
        })


class NotificationSeenView(APIView):
    required_permission = "authenticated"

    def post(self, request, pk=None):
        qs = Notification.objects.filter(user=request.user, seen_at__isnull=True)
        if pk:
            qs = qs.filter(pk=pk)
        n = qs.update(seen_at=timezone.now())
        return Response({"updated": n})


class AuditListView(APIView):
    required_permission = "audit.view"

    def get(self, request):
        qs = AuditLog.objects.all()
        p = request.query_params
        if p.get("resource"):
            qs = qs.filter(resource=p["resource"])
        if p.get("action"):
            qs = qs.filter(action=p["action"])
        if p.get("actor"):
            qs = qs.filter(actor_id=p["actor"])
        if p.get("search"):
            qs = qs.filter(Q(doc_no__icontains=p["search"]) | Q(object_id=p["search"]) | Q(actor_label__icontains=p["search"]))
        if p.get("date_from"):
            qs = qs.filter(at__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(at__date__lte=p["date_to"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs.order_by("-id"), request, view=self)
        # Owner sees everything; others see commercial fields masked.
        mask = not has_perm(request.user, "commercial.price.view")
        sensitive = {"price", "rate", "amount", "value", "unit_price"}
        rows = []
        for e in page:
            changes = e.changes or {}
            if mask and isinstance(changes, dict):
                changes = {k: ("•••" if any(s in k for s in sensitive) else v) for k, v in changes.items()}
            rows.append({
                "id": e.id, "at": e.at, "actor": e.actor_label, "action": e.action, "resource": e.resource,
                "object_id": e.object_id, "doc_no": e.doc_no, "version": e.record_version, "changes": changes,
                "reason": e.reason, "approval_ref": e.approval_ref, "ip": e.ip, "device_id": e.device_id,
                "entry_hash": e.entry_hash[:16],
            })
        return paginator.get_paginated_response(rows)


class AuditVerifyView(APIView):
    required_permission = "audit.verify"

    def post(self, request):
        result = audit.verify_chain()
        audit.record("audit_chain_verified", "core.audit", object_id="*", changes=result)
        return Response(result)


class LookupView(APIView):
    """Small static option lists used by admin action forms."""

    required_permission = "security.roles.manage"

    def get(self, request, source):
        if source == "roles":
            return Response([{"value": r.code, "label": f"{r.name} ({r.level})"} for r in Role.objects.order_by("level", "name")])
        if source == "permissions":
            return Response([{"value": p.code, "label": f"{p.code} - {p.description}", "module": p.module} for p in Permission.objects.order_by("code")])
        return Response([])
