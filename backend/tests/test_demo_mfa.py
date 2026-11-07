"""The fixed demo two-step code works only when configured and never completes authenticator setup."""
from unittest.mock import patch

from django.conf import settings

from .helpers import PASSWORD, PlantTestCase, ZTClient


class DemoMfaCodeTests(PlantTestCase):
    def _password_step(self):
        c = ZTClient()
        c.enrol()
        body = c.post("/api/auth/login", {"username": self.owner.username, "password": PASSWORD}).json()
        self.assertTrue(body["mfa_required"])
        return c, body["mfa_token"]

    def test_code_is_refused_when_not_configured(self):
        c, tok = self._password_step()
        r = c.post("/api/auth/mfa/verify", {"mfa_token": tok, "code": "000000"})
        self.assertEqual(r.status_code, 401)

    def test_configured_code_signs_in_without_finishing_setup(self):
        with patch.dict(settings.ZERO_TRUST, {"DEMO_MFA_CODE": "000000"}):
            c, tok = self._password_step()
            r = c.post("/api/auth/mfa/verify", {"mfa_token": tok, "code": "000000"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("access_token", r.json())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.mfa_enabled)

    def test_other_codes_are_still_refused_while_configured(self):
        with patch.dict(settings.ZERO_TRUST, {"DEMO_MFA_CODE": "000000"}):
            c, tok = self._password_step()
            r = c.post("/api/auth/mfa/verify", {"mfa_token": tok, "code": "123456"})
        self.assertEqual(r.status_code, 401)
