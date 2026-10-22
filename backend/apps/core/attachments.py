"""
Protected evidence storage (brief p.41, p.45).

* Content is validated by magic bytes (not the client's file name/MIME type).
* Images are re-encoded with Pillow which drops EXIF/GPS metadata.
* Files live outside any web root under random names; retrieval re-checks the
  parent record's scope and the attachment's classification on every request.
* Attaching the same file twice to the same record/purpose returns the
  original attachment - it can never re-post stock or other business effects.
"""
import hashlib
import io
import uuid

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework.response import Response

from . import audit
from .api import BusinessRuleError, idempotent
from .models import Attachment
from .rbac import has_perm
from .resource_api import ResourceBase, get_resource
from .registry import REGISTRY, resource_for_model

SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"%PDF-", "application/pdf", "pdf"),
]
CLASSIFICATION_PERMS = {
    "commercial": "commercial.price.view",
    "personal": "hr.personal.view",
    "security": "gate.evidence.view",
}


def sniff(head):
    for sig, mime, ext in SIGNATURES:
        if head.startswith(sig):
            return mime, ext
    return None, None


def sanitise_image(raw, mime):
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
        if img.width * img.height > 40_000_000:
            raise BusinessRuleError("Image dimensions too large.", "invalid_file")
        out = io.BytesIO()
        if mime == "image/jpeg":
            img.convert("RGB").save(out, format="JPEG", quality=88)
        else:
            img.save(out, format="PNG")
        return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise BusinessRuleError("File is not a valid image.", "invalid_file")


def store_attachment(*, parent, purpose, upload, user, classification="internal", capture_source="upload", device_id=None, note="", proof_round=1):
    max_bytes = settings.KANTAM["ATTACHMENT_MAX_BYTES"]
    if upload.size > max_bytes:
        raise BusinessRuleError(f"File exceeds {max_bytes // (1024 * 1024)} MB limit.", "invalid_file")
    raw = upload.read()
    mime, ext = sniff(raw[:16])
    if mime is None:
        raise BusinessRuleError("Only JPEG, PNG or PDF evidence is accepted.", "invalid_file")
    if mime.startswith("image/"):
        raw = sanitise_image(raw, mime)
    digest = hashlib.sha256(raw).hexdigest()
    ct = ContentType.objects.get_for_model(type(parent))
    existing = Attachment.objects.filter(content_type=ct, object_id=str(parent.pk), purpose=purpose, sha256=digest, proof_round=proof_round).first()
    if existing:
        return existing, False
    now = timezone.now()
    storage_name = f"{now:%Y/%m}/{uuid.uuid4().hex}.{ext}"
    path = settings.MEDIA_ROOT / storage_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    att = Attachment.objects.create(
        content_type=ct, object_id=str(parent.pk), purpose=purpose, proof_round=proof_round,
        original_name=(upload.name or "file")[:200], mime_type=mime, size=len(raw), sha256=digest,
        storage_name=storage_name, classification=classification, capture_source=capture_source,
        uploaded_by=user, device_id=device_id, note=note[:300],
    )
    return att, True


def can_view_attachment(request, att):
    perm = CLASSIFICATION_PERMS.get(att.classification)
    if perm and not has_perm(request.user, perm):
        return False
    res = resource_for_model(att.content_type.model_class())
    if res is None or not has_perm(request.user, res.perm("view")):
        return False
    return res.get_queryset(request).filter(pk=att.object_id).exists()


def attachment_json(att):
    return {
        "id": str(att.id), "purpose": att.purpose, "proof_round": att.proof_round, "name": att.original_name,
        "mime_type": att.mime_type, "size": att.size, "sha256": att.sha256, "classification": att.classification,
        "capture_source": att.capture_source, "uploaded_by": att.uploaded_by.full_name, "uploaded_at": att.uploaded_at,
        "frozen": att.frozen, "note": att.note,
    }


class AttachmentListView(ResourceBase):
    def get_required_permission(self, request):
        res = self.res()
        if request.method == "GET":
            return res.perm("view")
        return [res.perm("update"), res.perm("attach")]

    def get(self, request, key, pk):
        res = self.res()
        obj = self.get_object(res, request)
        ct = ContentType.objects.get_for_model(res.model)
        atts = Attachment.objects.filter(content_type=ct, object_id=str(obj.pk)).select_related("uploaded_by").order_by("-uploaded_at")
        return Response([attachment_json(a) for a in atts if can_view_attachment(request, a)])

    @idempotent
    def post(self, request, key, pk):
        res = self.res()
        obj = self.get_object(res, request)
        upload = request.FILES.get("file")
        purpose = (request.data.get("purpose") or "").strip()[:40]
        if not upload or not purpose:
            raise BusinessRuleError("file and purpose are required.", "validation")
        hook = getattr(res, "attachment_policy", None)
        policy = hook(request, obj, purpose) if hook else {}
        if policy.get("blocked"):
            raise BusinessRuleError(policy["blocked"], "attachment_locked")
        classification = policy.get("classification", "internal")
        capture = request.data.get("capture_source", "upload")
        att, created = store_attachment(
            parent=obj, purpose=purpose, upload=upload, user=request.user, classification=classification,
            capture_source=capture if capture in {"live", "upload"} else "upload",
            device_id=getattr(getattr(request, "zt_device", None), "pk", None),
            note=request.data.get("note", ""), proof_round=policy.get("proof_round", 1),
        )
        if created:
            audit.record("attach", res.key, obj, changes={"attachment": str(att.id), "purpose": purpose, "sha256": att.sha256})
        return Response(attachment_json(att), status=201 if created else 200)


class AttachmentContentView(ResourceBase):
    required_permission = "authenticated"

    def get(self, request, att_id):
        att = Attachment.objects.select_related("content_type").filter(pk=att_id).first()
        if att is None or not can_view_attachment(request, att):
            raise Http404("Not found")
        path = settings.MEDIA_ROOT / att.storage_name
        if not path.exists():
            raise Http404("File missing")
        audit.record("view_attachment", "core.attachment", object_id=str(att.id), changes={"purpose": att.purpose})
        resp = FileResponse(open(path, "rb"), content_type=att.mime_type)
        resp["Content-Disposition"] = f'inline; filename="evidence-{att.id}"'
        resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


def attachments_for(obj, purpose=None, proof_round=None):
    ct = ContentType.objects.get_for_model(type(obj))
    qs = Attachment.objects.filter(content_type=ct, object_id=str(obj.pk))
    if purpose:
        qs = qs.filter(purpose=purpose)
    if proof_round is not None:
        qs = qs.filter(proof_round=proof_round)
    return qs
