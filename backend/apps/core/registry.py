"""
Dynamic resource registry.

Each department record type is declared once as a `Resource`. The registry:
* generates its permission codes (<key>.view/.create/.update/.export/.<action>),
* exposes field/filter/action metadata so the React UI can render workspaces
  without hard-coded forms ("dynamic ERP"),
* applies row-level scoping and field-level projections (e.g. prices hidden
  from stores/production) on *every* read, write, search and export.

Business rules live in each app's services; resources call them from hooks
and actions so every entry route goes through the same server-side checks.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from django.db import models

REGISTRY = {}

MODULES = [
    ("dashboard", "Dashboards"),
    ("crm", "Sales & CRM"),
    ("sales", "Sales orders"),
    ("masters", "Masters"),
    ("planning", "Planning"),
    ("production", "Production"),
    ("inventory", "Stores & labels"),
    ("purchase", "Purchase"),
    ("quality", "Quality"),
    ("fg", "Packing & dispatch"),
    ("gate", "Gate & security"),
    ("tasks", "Tasks & maintenance"),
    ("core", "Administration"),
]


@dataclass
class Action:
    name: str
    label: str
    handler: Callable
    permission: Optional[str] = None
    fields: list = field(default_factory=list)
    reason_required: bool = False
    confirm: str = ""
    tone: str = "default"  # default | primary | danger
    available: Optional[Callable] = None  # (obj, request) -> bool
    detail: bool = True  # False => collection-level action

    def perm(self, resource_key):
        return self.permission or f"{resource_key}.{self.name}"


class Resource:
    key = ""
    model = None
    label = ""
    singular = ""
    module = ""
    title_field = "doc_no"
    list_fields = []
    form_fields = []
    create_only_fields = []
    readonly_fields = []
    search_fields = []
    filter_fields = []
    ordering = ["-created_at"]
    field_permissions = {}
    field_help = {}
    depends = {}
    actions = []
    inlines = []
    allow_create = True
    allow_update = True
    # Owner / admin permanent delete. Child lines (inlines that are not in the menu) go with their parent;
    # anything else still pointing at the record blocks the delete.
    allow_delete = True
    # Writable fields the server fills in before_create when omitted.
    server_defaults = []
    # Optional at model level (old rows) but required for new entries; enforced in hooks, flagged in the form.
    required_fields = []
    # False for child records reached through their parent's tabs (book UI-11: keep them out of the sidebar).
    in_nav = True
    # Lines that belong to one parent and are deleted with it (defaults to "not in the menu").
    deleted_with_parent = None
    # Dropdown filter preselected for a field, which the user may change, e.g. {"supplier": {"category": "labels"}}.
    filter_defaults = {}
    # Show a form field only when another field has a value, e.g. {"finish_other": {"finish": "other"}}.
    show_if = {}
    # Files the create form must include, e.g. [{"purpose": "design_pdf", "label": "Designed PDF", "accept": "application/pdf"}].
    required_uploads = []
    help = {}
    status_field = "status"

    # ---- scoping & hooks -------------------------------------------------
    def base_queryset(self):
        return self.model._default_manager.all()

    def scope(self, request, qs):
        return qs

    def get_queryset(self, request):
        return self.scope(request, self.base_queryset())

    def edit_block_reason(self, request, obj):
        """Return a message if the record is immutable for this user, else None."""
        return None

    def before_create(self, request, data):
        pass

    def create(self, request, data):
        obj = self.model(**data)
        if hasattr(obj, "created_by_id") and not obj.created_by_id:
            obj.created_by = request.user
        if hasattr(obj, "owner_id") and not getattr(obj, "owner_id", None):
            obj.owner = request.user
        obj.full_clean(exclude=self._fk_exclusions())
        obj.save()
        return obj

    def after_create(self, request, obj):
        pass

    def before_update(self, request, obj, data):
        pass

    def after_update(self, request, obj, changes):
        pass

    def form_defaults(self, user):
        """Values filled into a new record's form, e.g. the next free code."""
        return {}

    def delete_block_reason(self, request, obj):
        """Return a message if the owner must not delete this record, else None."""
        return None

    def before_delete(self, request, obj):
        """Clean-up that must happen before the row goes (e.g. take a movement off the balances)."""
        pass

    def destroy(self, request, obj):
        obj.delete()

    def extra_detail(self, request, obj):
        return {}

    def outbox_source(self, obj):
        """The document whose outbound messages become stale when this record is amended.
        Child lines point at their parent document (e.g. a PO line -> its PO)."""
        return self.key, obj.pk

    def _fk_exclusions(self):
        return [f.name for f in self.model._meta.concrete_fields if isinstance(f, models.ForeignKey) and f.name in {"created_by", "owner"}]

    # Map verbs to existing capability permissions (e.g. all verbs of the user
    # admin resource -> security.users.manage) instead of generated codes.
    permission_map = {}
    # Several resources may share a model; only the canonical one is used for
    # FK reference checks and attachment parents.
    canonical = True

    # ---- permissions -----------------------------------------------------
    def perm(self, verb):
        if verb in self.permission_map:
            return self.permission_map[verb]
        return f"{self.key}.{verb}"

    def permission_codes(self):
        codes = {
            self.perm("view"): f"View {self.label}",
            self.perm("export"): f"Export {self.label}",
        }
        if self.allow_create:
            codes[self.perm("create")] = f"Create {self.singular or self.label}"
        if self.allow_update:
            codes[self.perm("update")] = f"Edit {self.singular or self.label}"
        for action in self.actions:
            codes.setdefault(action.perm(self.key), f"{action.label} ({self.singular or self.label})")
        if self.permission_map:
            for verb in ("view", "export", "create", "update"):
                codes.pop(f"{self.key}.{verb}", None)
        return codes

    def get_action(self, name):
        for action in self.actions:
            if action.name == name:
                return action
        return None


def register(cls):
    instance = cls()
    if instance.key in REGISTRY:
        raise RuntimeError(f"duplicate resource {instance.key}")
    REGISTRY[instance.key] = instance
    return cls


def resource_for_model(model):
    for res in REGISTRY.values():
        if res.model is model and res.canonical:
            return res
    return None


def all_permission_codes():
    from .rbac_matrix import CAPABILITIES

    codes = {}
    for res in REGISTRY.values():
        for code, desc in res.permission_codes().items():
            codes[code] = (res.module, desc)
    for code, (module, desc) in CAPABILITIES.items():
        codes[code] = (module, desc)
    return codes
