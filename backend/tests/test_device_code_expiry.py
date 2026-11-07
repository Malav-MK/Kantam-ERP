"""Enrolment codes have no time limit: they end when used, or when the owner expires them."""
from datetime import timedelta

from django.utils import timezone

from apps.core.auth_views import new_enrollment_code
from apps.core.models import AuditLog, Device

from .helpers import PlantTestCase, ZTClient, make_user


class DeviceCodeExpiryTests(PlantTestCase):
    def setUp(self):
        self.code, code_hash = new_enrollment_code()
        self.device = Device.objects.create(name="Gate tablet", enrollment_code_hash=code_hash)

    def _enrol(self, code):
        return ZTClient().post("/api/auth/device/enroll", {"code": code})

    def _act(self, client, action):
        return client.post(f"/api/r/core.device/{self.device.pk}/actions/{action}", {"reason": "Demo finished"})

    def test_new_device_from_the_api_has_no_time_limit(self):
        r = self.client_for(self.owner).post("/api/r/core.device", {"name": "Reception PC", "kind": "pc"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(Device.objects.get(name="Reception PC").enrollment_expires)
        self.assertEqual(self._enrol(r.json()["_extra"]["enrollment_code"]).status_code, 200)

    def test_time_does_not_end_a_code(self):
        Device.objects.filter(pk=self.device.pk).update(enrollment_expires=timezone.now() - timedelta(days=400))
        self.assertEqual(self._enrol(self.code).status_code, 200)

    def test_code_still_works_only_once(self):
        self.assertEqual(self._enrol(self.code).status_code, 200)
        self.assertEqual(self._enrol(self.code).status_code, 400)

    def test_owner_expires_a_code_and_it_is_audited(self):
        r = self._act(self.client_for(self.owner), "expire_code")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._enrol(self.code).status_code, 400)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, "pending")
        self.assertTrue(AuditLog.objects.filter(action="device_code_expired", object_id=str(self.device.pk)).exists())

    def test_it_admin_cannot_end_an_unused_code(self):
        it = self.client_for(make_user("itadmin", ["it_admin"]))
        for action in ("expire_code", "new_code", "revoke"):
            self.assertEqual(self._act(it, action).json()["error"], "invalid_state", action)
        r = it.delete(f"/api/r/core.device/{self.device.pk}", {"reason": "Demo finished"})
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(self._enrol(self.code).status_code, 200)

    def test_it_admin_can_still_revoke_an_enrolled_device(self):
        self.assertEqual(self._enrol(self.code).status_code, 200)
        it = self.client_for(make_user("itadmin", ["it_admin"]))
        self.assertEqual(self._act(it, "revoke").status_code, 200)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, "revoked")
