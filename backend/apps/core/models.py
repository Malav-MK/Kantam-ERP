"""
Foundation models shared by every department workspace.

Brief p.4: every operational record has a permanent internal ID, a readable
document number, status, owner, creation time and change history.
"""
import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Organisation (brief p.62: company -> site -> department -> team -> user)
# ---------------------------------------------------------------------------
class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.name


class Site(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="sites")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="teams")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_departments")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        if not username:
            raise ValueError("username is required")
        user = self.model(username=username.strip().lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """Named personal account. There are no shared department passwords (p.27)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=60, unique=True)
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="members")
    reporting_manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    job_title = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)
    # MFA (TOTP). The secret never leaves the server after enrolment.
    mfa_secret = models.CharField(max_length=64, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    # Bumped on every role/permission/active change; tokens carrying an old
    # value are rejected so permission changes take effect immediately.
    role_version = models.PositiveIntegerField(default=1)
    failed_logins = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    preferred_language = models.CharField(max_length=10, default="en")
    # Personal display settings (theme mode, accent colour, sidebar style, density).
    ui_preferences = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name"]
    objects = UserManager()

    def __str__(self):
        return f"{self.full_name} ({self.username})"

    def bump_role_version(self):
        User.objects.filter(pk=self.pk).update(role_version=models.F("role_version") + 1)
        self.refresh_from_db(fields=["role_version"])


class Permission(models.Model):
    code = models.CharField(max_length=80, primary_key=True)
    module = models.CharField(max_length=40)
    description = models.CharField(max_length=200)

    def __str__(self):
        return self.code


class Role(models.Model):
    LEVELS = [
        ("owner", "Owner / top management"),
        ("admin", "Security / IT administration"),
        ("manager", "Department manager"),
        ("staff", "Department staff / operator"),
        ("viewer", "Viewer / TV"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    level = models.CharField(max_length=10, choices=LEVELS)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")
    is_system = models.BooleanField(default=False)
    customized = models.BooleanField(default=False, help_text="A built-in role edited in the app; upgrades keep its permissions")

    def __str__(self):
        return self.name


class UserRole(models.Model):
    """Role grant, optionally scoped to a department and time-boxed (delegation)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_grants")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="grants")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    delegated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="delegations_given")
    reason = models.CharField(max_length=200, blank=True)
    granted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="grants_made")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "role", "department")]


# ---------------------------------------------------------------------------
# Zero trust: devices and sessions
# ---------------------------------------------------------------------------
class Device(models.Model):
    STATUS = [("pending", "Pending enrolment"), ("active", "Active"), ("revoked", "Revoked")]
    KINDS = [("pc", "Office PC"), ("tablet", "Shop-floor tablet"), ("phone", "Managed phone"), ("gate", "Gate device"), ("tv", "TV display")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=KINDS, default="pc")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    enrollment_code_hash = models.CharField(max_length=128, blank=True)
    enrollment_expires = models.DateTimeField(null=True, blank=True)
    secret_hash = models.CharField(max_length=128, blank=True)
    # Optional restriction: only these users may sign in on the device.
    restricted_to = models.ManyToManyField(User, blank=True, related_name="restricted_devices")
    enrolled_at = models.DateTimeField(null=True, blank=True)
    enrolled_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AuthSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL)
    family = models.UUIDField(default=uuid.uuid4)
    refresh_hash = models.CharField(max_length=128, unique=True)
    previous_refresh_hash = models.CharField(max_length=128, blank=True, db_index=True)
    mfa_verified = models.BooleanField(default=False)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=120, blank=True)

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()


class LoginAttempt(models.Model):
    username = models.CharField(max_length=60, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    device_id = models.UUIDField(null=True, blank=True)
    success = models.BooleanField()
    reason = models.CharField(max_length=60, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)


# ---------------------------------------------------------------------------
# Base classes for business records
# ---------------------------------------------------------------------------
class Sequence(models.Model):
    key = models.CharField(max_length=40)
    period = models.CharField(max_length=10)
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("key", "period")]

    @classmethod
    def next(cls, key, period):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(key=key, period=period)
            value = seq.next_value
            seq.next_value = value + 1
            seq.save(update_fields=["next_value"])
            return value


class BaseRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # Optimistic concurrency token; clients send it back on update.
    row_version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True


class DocumentRecord(BaseRecord):
    """A business document with a readable, sequential number."""

    DOC_PREFIX = "DOC"
    doc_no = models.CharField(max_length=40, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.doc_no:
            now = timezone.localtime()
            period = f"{now:%y%m}"
            value = Sequence.next(self.DOC_PREFIX, period)
            self.doc_no = f"{self.DOC_PREFIX}-{period}-{value:05d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.doc_no


# ---------------------------------------------------------------------------
# Audit (brief p.19): id, version, field, old, new, reason, user, time, approval
# Entries are hash-chained so tampering with history is detectable.
# ---------------------------------------------------------------------------
class AuditChainHead(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_hash = models.CharField(max_length=64, default="0" * 64)
    count = models.PositiveBigIntegerField(default=0)


class AuditLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    resource = models.CharField(max_length=60, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True, blank=True)
    doc_no = models.CharField(max_length=40, blank=True, db_index=True)
    record_version = models.PositiveIntegerField(null=True, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    approval_ref = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    device_id = models.UUIDField(null=True, blank=True)
    session_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64)
    entry_hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-id"]


# ---------------------------------------------------------------------------
# Evidence attachments (brief p.41)
# ---------------------------------------------------------------------------
class Attachment(models.Model):
    CLASSIFICATIONS = [
        ("internal", "Internal"),
        ("commercial", "Commercial"),
        ("personal", "Personal / identity"),
        ("security", "Security evidence"),
    ]
    SOURCES = [("live", "Live camera capture"), ("upload", "Selected / uploaded file"), ("system", "System generated")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64, db_index=True)
    parent = GenericForeignKey("content_type", "object_id")
    purpose = models.CharField(max_length=40)
    proof_round = models.PositiveIntegerField(default=1)
    original_name = models.CharField(max_length=200)
    mime_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    storage_name = models.CharField(max_length=120, unique=True)
    classification = models.CharField(max_length=12, choices=CLASSIFICATIONS, default="internal")
    capture_source = models.CharField(max_length=8, choices=SOURCES, default="upload")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    device_id = models.UUIDField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    frozen = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]


# ---------------------------------------------------------------------------
# Maker / checker approvals (brief p.19 "Approval settings to agree")
# ---------------------------------------------------------------------------
class ApprovalPolicy(BaseRecord):
    """
    Maker / checker rule: which operation needs independent approval before it
    takes effect. Policies are data, so the owner can tighten controls without code.
    """

    OPERATIONS = [("create", "Create record"), ("update", "Edit record"), ("action", "Workflow action")]
    CONDITIONS = [("always", "Always"), ("fields_changed", "Only when listed fields change"), ("amount_over", "Only when amount exceeds threshold")]
    name = models.CharField(max_length=120)
    resource = models.CharField(max_length=60, db_index=True, help_text="Resource key, e.g. purchase.purchaseorder")
    operation = models.CharField(max_length=10, choices=OPERATIONS)
    action = models.CharField(max_length=40, blank=True, help_text="Action name when operation = action, e.g. approve")
    condition = models.CharField(max_length=16, choices=CONDITIONS, default="always")
    fields = models.JSONField(default=list, blank=True, help_text='For "fields_changed": ["credit_limit", "payment_terms"]')
    amount_threshold = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    checker_permission = models.CharField(max_length=80, help_text="Permission a checker must hold")
    required_approvals = models.PositiveSmallIntegerField(default=1, help_text="Distinct checkers needed (1-3)")
    reason_required = models.BooleanField(default=True)
    expires_hours = models.PositiveIntegerField(default=72)
    active = models.BooleanField(default=True)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=["resource", "operation", "action"])]

    def __str__(self):
        return self.name


class ApprovalRequest(BaseRecord):
    STATUS = [("pending", "Pending"), ("approved", "Approved & executed"), ("rejected", "Rejected"), ("cancelled", "Cancelled"),
              ("withdrawn", "Withdrawn by maker"), ("expired", "Expired"), ("stale", "Record changed - resubmit")]
    KINDS = [
        ("policy", "Maker / checker policy"),
        ("so_amendment", "Post-lock SO amendment"),
        ("quotation_margin", "Quotation margin exception"),
        ("excess_production", "Excess production disposition"),
        ("material_substitution", "Material / label substitution"),
        ("stock_adjustment", "Stock adjustment"),
        ("purchase_limit", "Purchase above limit"),
        ("credit_release", "Dispatch credit release"),
        ("admin_correction", "Closed-record admin correction"),
        ("additional_issue", "Additional material issue"),
    ]
    kind = models.CharField(max_length=30, choices=KINDS)
    status = models.CharField(max_length=10, choices=STATUS, default="pending", db_index=True)
    policy = models.ForeignKey(ApprovalPolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="requests")
    operation = models.CharField(max_length=10, blank=True)
    action = models.CharField(max_length=40, blank=True)
    resource = models.CharField(max_length=60)
    object_id = models.CharField(max_length=64, blank=True)
    object_version = models.PositiveIntegerField(null=True, blank=True, help_text="Record version the maker saw")
    doc_no = models.CharField(max_length=40, blank=True)
    summary = models.CharField(max_length=300)
    payload = models.JSONField(default=dict)
    changes = models.JSONField(default=dict, blank=True, help_text="Proposed field changes: {field: [current, proposed]}")
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    required_permission = models.CharField(max_length=80)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    approvals = models.JSONField(default=list, blank=True, help_text="[{user, name, at, note}]")
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)


# ---------------------------------------------------------------------------
# Alerts, tasks inbox and outbound messaging (brief p.18, p.50)
# ---------------------------------------------------------------------------
class Notification(models.Model):
    SEVERITY = [("info", "Info"), ("warning", "Warning"), ("critical", "Critical")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    severity = models.CharField(max_length=10, choices=SEVERITY, default="info")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    resource = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    next_action = models.CharField(max_length=200, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "dedupe_key"], condition=~models.Q(dedupe_key=""), name="uniq_notification_dedupe"),
        ]


class OutboxMessage(BaseRecord):
    CHANNELS = [("whatsapp", "WhatsApp"), ("erp", "Accounting integration")]
    # Book UI-07: explicit communication states. None of them means the underlying work is done.
    STATUS = [
        ("draft", "Draft - approval needed"), ("approved", "Approved / eligible"), ("queued", "Queued"),
        ("accepted", "Provider accepted"), ("delivered", "Delivered"), ("read", "Read"), ("failed", "Failed"),
        ("suppressed", "Suppressed"), ("cancelled", "Cancelled"),
    ]
    UNSENT = ("draft", "approved")
    FINAL = ("read", "suppressed", "cancelled")
    channel = models.CharField(max_length=10, choices=CHANNELS)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    recipient = models.CharField(max_length=200)
    template = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    source_resource = models.CharField(max_length=60)
    source_id = models.CharField(max_length=64)
    source_version = models.PositiveIntegerField(default=1)
    dedupe_key = models.CharField(max_length=160, unique=True)
    provider_id = models.CharField(max_length=120, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")


class IdempotencyRecord(models.Model):
    """Prevents duplicate postings after repeated taps or network retries (p.4)."""

    key = models.CharField(max_length=80)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("key", "user")]


class ShiftLock(models.Model):
    """Explicit closure of a plant shift (brief p.3/p.10)."""

    SHIFTS = [("day", "Day 09:00-21:00"), ("night", "Night 21:00-09:00")]
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT)
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=SHIFTS)
    closed_at = models.DateTimeField(default=timezone.now)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    automatic = models.BooleanField(default=False)

    class Meta:
        unique_together = [("site", "shift_date", "shift")]


def content_type_for(model):
    return ContentType.objects.get_for_model(model)
