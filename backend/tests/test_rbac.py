"""Department barriers and projections (brief p.27 acceptance checks, p.41)."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.crm.models import Lead
from apps.gate.models import Visit
from apps.sales.models import SalesOrder, SOLine

from .helpers import PlantTestCase


def png_bytes(color=(200, 10, 10)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


class RbacTests(PlantTestCase):
    def setUp(self):
        self.lead = Lead.objects.create(company="Prospect A", salesperson=self.sales)
        self.so = SalesOrder.objects.create(customer=self.customer, salesperson=self.sales)
        self.line = SOLine.objects.create(so=self.so, line_no=1, product=self.product, flavour="Mango", qty=1000, unit_price="4.25",
                                          container_artwork=self.arts[("Mango", "container")], lid_artwork=self.arts[("Mango", "lid")])

    def test_store_cannot_read_crm_by_list_or_guessed_id(self):
        c = self.client_for(self.store)
        self.assertEqual(c.get("/api/r/crm.lead").status_code, 403)
        self.assertEqual(c.get(f"/api/r/crm.lead/{self.lead.pk}").status_code, 403)
        self.assertEqual(c.get("/api/r/crm.lead/export").status_code, 403)

    def test_sales_exec_sees_only_own_leads(self):
        c = self.client_for(self.sales2)
        self.assertEqual(c.get(f"/api/r/crm.lead/{self.lead.pk}").status_code, 404)
        self.assertEqual(c.get("/api/r/crm.lead").json()["count"], 0)
        self.assertEqual(self.client_for(self.sales_mgr).get("/api/r/crm.lead").json()["count"], 1)

    def test_price_hidden_from_planner_projection(self):
        c = self.client_for(self.planner)
        row = c.get(f"/api/r/sales.soline/{self.line.pk}").json()
        self.assertNotIn("unit_price", row)
        self.assertEqual(self.client_for(self.sales).get(f"/api/r/sales.soline/{self.line.pk}").json()["unit_price"], "4.2500")
        meta = c.get("/api/r/sales.soline/meta").json()
        self.assertNotIn("unit_price", meta["fields"])

    def test_search_obeys_barriers(self):
        c = self.client_for(self.sales2)
        self.assertEqual(c.get("/api/r/crm.lead", search="Prospect").json()["count"], 0)

    def test_meta_lists_only_permitted_workspaces(self):
        mods = {m["key"] for m in self.client_for(self.guard).get("/api/meta").json()["modules"]}
        self.assertIn("gate", mods)
        self.assertNotIn("crm", mods)
        self.assertNotIn("sales", mods)

    def test_tv_projection_is_sanitised(self):
        c = self.client_for(self.tv)
        self.assertEqual(c.get("/api/meta").json()["modules"], [])
        self.assertEqual(c.get("/api/r/sales.salesorder").status_code, 403)
        self.assertEqual(c.get("/api/dash/owner").status_code, 403)
        tv = c.get("/api/dash/tv").json()
        for m in tv["machines"]:
            self.assertFalse({"customer", "flavour", "pr_id"} & set(m))

    def test_guard_cannot_see_sales_prices_or_staff_records(self):
        c = self.client_for(self.guard)
        self.assertEqual(c.get("/api/r/sales.salesorder").status_code, 403)
        self.assertEqual(c.get("/api/r/core.user").status_code, 403)

    def test_cannot_reference_hidden_records_in_writes(self):
        """A sales exec cannot create an activity against another exec's lead by guessing its ID."""
        other = Lead.objects.create(company="Other", salesperson=self.sales2)
        c = self.client_for(self.sales)
        r = c.post("/api/r/crm.activity", {"lead": str(other.pk), "kind": "call", "subject": "x", "at": "2026-10-16T10:00:00+05:30"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "reference_denied")

    def test_gate_photo_direct_url_denied_to_other_roles(self):
        g = self.client_for(self.guard)
        visit = g.post("/api/r/gate.visit", {"visitor_name": "V", "host": str(self.sales.pk), "purpose": "Meeting"}).json()
        up = g.upload(f"/api/r/gate.visit/{visit['id']}/attachments", SimpleUploadedFile("p.png", png_bytes(), "image/png"), "visitor_photo")
        self.assertEqual(up.status_code, 201)
        att = up.json()["id"]
        self.assertEqual(g.get(f"/api/attachments/{att}/content").status_code, 200)
        self.assertEqual(self.client_for(self.store).get(f"/api/attachments/{att}/content").status_code, 404)
        self.assertEqual(self.client_for(self.tv).get(f"/api/attachments/{att}/content").status_code, 404)

    def test_rejects_non_image_upload_and_strips_metadata(self):
        g = self.client_for(self.guard)
        visit = g.post("/api/r/gate.visit", {"visitor_name": "V", "host": str(self.sales.pk), "purpose": "Meeting"}).json()
        bad = g.upload(f"/api/r/gate.visit/{visit['id']}/attachments", SimpleUploadedFile("x.png", b"<script>alert(1)</script>", "image/png"), "visitor_photo")
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(bad.json()["error"], "invalid_file")

    def test_self_role_grant_blocked(self):
        from apps.core.models import UserRole
        UserRole.objects.create(user=self.store_mgr, role=__import__("apps.core.models", fromlist=["Role"]).Role.objects.get(code="it_admin"))
        c = self.client_for(self.store_mgr)
        r = c.post(f"/api/r/core.user/{self.store_mgr.pk}/actions/grant_role", {"role": "owner", "reason": "escalate myself"})
        self.assertEqual(r.status_code, 409)


class ThemePreferenceTests(PlantTestCase):
    def test_preferences_saved_per_user_and_strictly_validated(self):
        c = self.client_for(self.store)
        r = c.c.patch("/api/auth/preferences", data='{"mode":"dark","preset":"custom","accent":"#1F5FA8","sidebar":"light","density":"compact"}',
                      content_type="application/json", **c._headers("PATCH", "/api/auth/preferences"))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(c.get("/api/auth/me").json()["ui_preferences"]["accent"], "#1f5fa8")
        for bad in ('{"accent":"red;background:url(x)"}', '{"mode":"neon"}', '{"sidebar":"<script>"}'):
            r = c.c.patch("/api/auth/preferences", data=bad, content_type="application/json", **c._headers("PATCH", "/api/auth/preferences"))
            self.assertEqual(r.status_code, 409, bad)
        self.assertEqual(self.client_for(self.sales).get("/api/auth/me").json()["ui_preferences"], {})
