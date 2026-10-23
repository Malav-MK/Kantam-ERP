"""Artwork & labels: creation with required files, size in mm, finish, several customers; Label items; customer codes; dropdown metadata."""
import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.core.attachments import attachments_for
from apps.masters.models import Artwork, Customer, Item

from .helpers import PlantTestCase


def files():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    return {
        "design_pdf": SimpleUploadedFile("design.pdf", b"%PDF-1.4\n%test\n", content_type="application/pdf"),
        "customer_approval": SimpleUploadedFile("ok.png", buf.getvalue(), content_type="image/png"),
    }


class ArtworkAndLabelTests(PlantTestCase):
    def new_artwork(self, client, **extra):
        payload = {"code": "MK-NEW", "product": str(self.product.pk), "component_type": "lid", "finish": "glossy",
                   "height_mm": "98.25", "width_mm": "506", **extra}
        return client.post_form("/api/r/masters.artwork", payload, files())

    def test_new_artwork_with_files_size_and_several_customers(self):
        owner = self.client_for(self.owner)
        other = Customer.objects.create(code="C2", name="Ice Cream Co")
        r = self.new_artwork(owner, customers=[str(self.customer.pk), str(other.pk)])
        self.assertEqual(r.status_code, 201, r.content[:400])
        body = r.json()
        self.assertEqual(body["size"], "Height: 98.25 MM, Width: 506 MM")
        self.assertEqual({c["id"] for c in body["customers_items"]}, {str(self.customer.pk), str(other.pk)})
        art = Artwork.objects.get(pk=body["id"])
        self.assertEqual(attachments_for(art).count(), 2)
        listed = owner.get("/api/r/masters.artwork", customers=str(other.pk)).json()["results"]
        self.assertEqual([x["id"] for x in listed], [body["id"]])

    def test_files_and_finish_are_mandatory(self):
        owner = self.client_for(self.owner)
        r = owner.post("/api/r/masters.artwork", {"code": "MK-X", "product": str(self.product.pk), "component_type": "lid", "finish": "glossy"})
        self.assertEqual(r.json()["error"], "upload_required", r.content[:300])
        r = self.new_artwork(owner, finish="")
        self.assertEqual(r.status_code, 400, r.content[:300])
        r = self.new_artwork(owner, finish="other")
        self.assertEqual(r.json()["error"], "validation", r.content[:300])
        r = self.new_artwork(owner, finish="other", finish_other="Pearl lacquer")
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(Artwork.objects.get(pk=r.json()["id"]).finish_other, "Pearl lacquer")
        meta = owner.get("/api/r/masters.artwork/meta").json()
        self.assertIn("Orange Peel + Foil", [c["label"] for c in meta["fields"]["finish"]["choices"]])
        self.assertEqual(meta["fields"]["finish_other"]["show_if"], {"finish": "other"})
        self.assertEqual(meta["fields"]["supplier"]["filter_default"], {"category": "labels"})
        self.assertEqual(meta["fields"]["customers"]["type"], "fk_multi")
        self.assertEqual([u["purpose"] for u in meta["required_uploads"]], ["design_pdf", "customer_approval"])

    def test_artwork_cannot_advance_without_its_files(self):
        art = self.arts[("Mango", "lid")]
        Artwork.objects.filter(pk=art.pk).update(status="draft")
        r = self.client_for(self.owner).post(f"/api/r/masters.artwork/{art.pk}/actions/advance")
        self.assertEqual(r.json()["error"], "upload_required", r.content[:300])

    def test_new_revision_keeps_size_customers_and_finish(self):
        art = self.arts[("Mango", "lid")]
        Artwork.objects.filter(pk=art.pk).update(height_mm=Decimal("98.25"), width_mm=Decimal("506"), finish="orange_peel_foil")
        r = self.client_for(self.owner).post(f"/api/r/masters.artwork/{art.pk}/actions/new_revision", {"reason": "New colour"})
        self.assertEqual(r.status_code, 200, r.content[:400])
        new = Artwork.objects.get(pk=r.json()["id"])
        self.assertEqual((r.json()["size"], new.finish), ("Height: 98.25 MM, Width: 506 MM", "orange_peel_foil"))
        self.assertEqual(list(new.customers.all()), [self.customer])

    def test_label_items_screen(self):
        owner = self.client_for(self.owner)
        r = owner.post("/api/r/masters.labelitem", {"code": "L-NEW", "name": "Label new lid", "group": "Round 500"})
        self.assertEqual(r.status_code, 201, r.content[:400])
        item = Item.objects.get(code="L-NEW")
        self.assertEqual((item.kind, item.stock_uom, item.whole_units), ("label", "pcs", True))
        codes = {x["code"] for x in owner.get("/api/r/masters.labelitem", page_size=200).json()["results"]}
        self.assertIn("L-NEW", codes)
        self.assertNotIn("PP", codes)  # only labels
        modules = {m["key"]: [x["key"] for x in m["resources"] if x.get("nav") is not False] for m in owner.get("/api/meta").json()["modules"]}
        self.assertIn("masters.labelitem", modules["masters"])
        self.assertIn("masters.productcomponent", modules["masters"])
        self.assertNotIn("masters.supplier", modules["masters"])
        self.assertIn("masters.supplier", modules["purchase"])
        found = owner.get("/api/r/masters.productcomponent", search="R500").json()["results"]
        self.assertEqual(len(found), 2)

    def test_customer_code_is_filled_in(self):
        owner = self.client_for(self.owner)
        Customer.objects.create(code="07", name="Seven")
        self.assertEqual(owner.get("/api/r/masters.customer/meta").json()["defaults"], {"code": "08"})
        r = owner.post("/api/r/masters.customer", {"name": "No code given"})
        self.assertEqual((r.status_code, r.json().get("code")), (201, "08"), r.content[:300])
        self.assertEqual(owner.get("/api/r/masters.customer/meta").json()["defaults"], {"code": "09"})
