"""Zero-trust controls (brief p.27-28, gate G5)."""
from datetime import timedelta

from django.utils import timezone

from apps.core import audit
from apps.core.models import AuditLog, AuthSession, Device, Role, UserRole

from .helpers import PlantTestCase, ZTClient


class ZeroTrustTests(PlantTestCase):
    def test_unsigned_request_is_rejected(self):
        c = ZTClient(sign=False)
        r = c.get("/api/auth/device")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "zero_trust_denied")

    def test_tampered_signature_rejected(self):
        c = ZTClient()
        h = c._headers("GET", "/api/auth/device")
        h["HTTP_X_ZT_CLIENT_IP"] = "10.9.9.9"  # client IP altered after signing
        r = c.c.get("/api/auth/device", **h)
        self.assertEqual(r.status_code, 403)

    def test_outside_network_fails_closed(self):
        c = ZTClient(ip="203.0.113.7")  # public / mobile data address
        r = c.get("/api/auth/device")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["reason"], "network not approved")

    def test_login_requires_enrolled_device(self):
        c = ZTClient()
        r = c.login("sales", enrol=False)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "device_not_enrolled")

    def test_owner_requires_mfa(self):
        c = ZTClient()
        c.enrol()
        r = c.post("/api/auth/login", {"username": "owner", "password": "Correct-Horse-Battery-77"})
        self.assertTrue(r.json()["mfa_required"])
        self.assertNotIn("access_token", r.json())
        self.assertEqual(self.client_for(self.owner).get("/api/auth/me").status_code, 200)

    def test_lockout_after_failed_attempts(self):
        c = ZTClient()
        c.enrol()
        for _ in range(5):
            c.post("/api/auth/login", {"username": "sales", "password": "wrong"})
        r = c.post("/api/auth/login", {"username": "sales", "password": "Correct-Horse-Battery-77"})
        unknown = c.post("/api/auth/login", {"username": "no-such-user", "password": "x"})
        # Locked, wrong password and unknown user are indistinguishable (no username enumeration).
        self.assertEqual((r.status_code, r.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(r.status_code, 401)
        from apps.core.models import User
        self.assertIsNotNone(User.objects.get(username="sales").locked_until)

    def test_token_bound_to_device(self):
        a = self.client_for(self.sales)
        b = ZTClient()
        b.enrol("Other PC")
        b.token = a.token  # stolen token replayed from another device
        r = b.get("/api/auth/me")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["reason"], "device_not_trusted")

    def test_revoked_device_blocks_immediately(self):
        c = self.client_for(self.sales)
        Device.objects.update(status="revoked", secret_hash="")
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_role_change_invalidates_token(self):
        c = self.client_for(self.sales)
        self.assertEqual(c.get("/api/r/crm.lead").status_code, 200)
        UserRole.objects.filter(user=self.sales).delete()
        self.sales.bump_role_version()
        self.assertEqual(c.get("/api/r/crm.lead").status_code, 401)

    def test_refresh_rotation_and_reuse_detection(self):
        c = self.client_for(self.sales)
        old_cookie = c.c.cookies["kt_rt"].value
        r = c.post("/api/auth/refresh")
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(c.c.cookies["kt_rt"].value, old_cookie)
        c.c.cookies["kt_rt"] = old_cookie  # attacker replays the rotated token
        self.assertEqual(c.post("/api/auth/refresh").status_code, 401)
        self.assertTrue(AuthSession.objects.filter(user=self.sales, revoke_reason="refresh_reuse_detected").exists())

    def test_idle_timeout(self):
        c = self.client_for(self.sales)
        AuthSession.objects.filter(user=self.sales).update(last_seen=timezone.now() - timedelta(hours=2))
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_disabled_user_blocked(self):
        c = self.client_for(self.sales)
        self.sales.is_active = False
        self.sales.save()
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_audit_chain_detects_tampering(self):
        audit.record("test", "x", object_id="1", changes={"a": 1})
        audit.record("test", "x", object_id="2", changes={"a": 2})
        self.assertTrue(audit.verify_chain()["ok"])
        AuditLog.objects.filter(object_id="1").update(changes={"a": 999})
        self.assertFalse(audit.verify_chain()["ok"])

    def test_no_store_cache_headers(self):
        c = self.client_for(self.sales)
        r = c.get("/api/auth/me")
        self.assertIn("no-store", r["Cache-Control"])
