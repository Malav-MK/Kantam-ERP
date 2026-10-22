"""Test helpers: a client that behaves like the Node gateway + a browser on an enrolled device."""
import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal

import pyotp
from django.test import Client, TestCase
from django.utils import timezone

from apps.core.auth_views import new_enrollment_code
from apps.core.models import Company, Department, Device, Role, Site, User, UserRole
from apps.core.rbac_sync import sync_permissions
from apps.core.security import gateway_signature
from apps.masters.models import (
    Artwork, Customer, Item, Location, Machine, Mould, PackingRule, Product, ProductComponent, Recipe, RecipeLine, Supplier,
)

SECRET = "test-gateway-secret"
PASSWORD = "Correct-Horse-Battery-77"


class ZTClient:
    """Signs every request exactly as the gateway does and keeps device/refresh cookies."""

    def __init__(self, ip="10.1.2.3", sign=True):
        self.c = Client()
        self.ip = ip
        self.sign = sign
        self.token = None

    def _headers(self, method, path, extra=None):
        h = {"HTTP_X_REQUESTED_WITH": "kantam"}
        if self.sign:
            ts = str(int(time.time()))
            rid = uuid.uuid4().hex
            h.update({
                "HTTP_X_ZT_TIMESTAMP": ts, "HTTP_X_ZT_CLIENT_IP": self.ip, "HTTP_X_ZT_REQUEST_ID": rid,
                "HTTP_X_ZT_SIGNATURE": gateway_signature(SECRET, ts, method, path, self.ip, rid),
            })
        if self.token:
            h["HTTP_AUTHORIZATION"] = f"Bearer {self.token}"
        h.update(extra or {})
        return h

    def get(self, path, **params):
        from urllib.parse import urlencode
        full = path + ("?" + urlencode(params) if params else "")
        return self.c.get(full, **self._headers("GET", full))

    def post(self, path, data=None, idem=None, **extra):
        h = self._headers("POST", path, extra)
        if idem:
            h["HTTP_IDEMPOTENCY_KEY"] = idem
        return self.c.post(path, data=json.dumps(data or {}), content_type="application/json", **h)

    def upload(self, path, file, purpose, **fields):
        h = self._headers("POST", path)
        return self.c.post(path, data={"file": file, "purpose": purpose, **fields}, **h)

    def patch(self, path, data):
        return self.c.patch(path, data=json.dumps(data), content_type="application/json", **self._headers("PATCH", path))

    # --- flows -----------------------------------------------------------
    def enrol(self, name="Test PC"):
        code, h = new_enrollment_code()
        Device.objects.create(name=name, enrollment_code_hash=h, enrollment_expires=timezone.now() + timedelta(hours=1))
        r = self.post("/api/auth/device/enroll", {"code": code})
        assert r.status_code == 200, r.content
        return r.json()["device"]["id"]

    def login(self, username, password=PASSWORD, enrol=True):
        if enrol and "kt_dev" not in self.c.cookies:
            self.enrol()
        r = self.post("/api/auth/login", {"username": username, "password": password})
        body = r.json()
        if r.status_code == 200 and body.get("mfa_required"):
            tok = body["mfa_token"]
            user = User.objects.get(username=username)
            if body["mfa_setup_required"]:
                secret = self.post("/api/auth/mfa/setup", {"mfa_token": tok}).json()["secret"]
            else:
                secret = user.mfa_secret
            r = self.post("/api/auth/mfa/verify", {"mfa_token": tok, "code": pyotp.TOTP(secret).now()})
            body = r.json()
        if r.status_code == 200:
            self.token = body["access_token"]
        return r


def make_user(username, roles, dept_code="MGMT"):
    dept = Department.objects.get(code=dept_code)
    u = User(username=username, full_name=username.title(), department=dept, site=dept.site)
    u.set_password(PASSWORD)
    u.save()
    for r in roles:
        UserRole.objects.create(user=u, role=Role.objects.get(code=r))
    return u


class PlantTestCase(TestCase):
    """Base fixture: organisation, roles, masters for one IML product with 3 flavours."""

    @classmethod
    def setUpTestData(cls):
        sync_permissions()
        company = Company.objects.create(code="K", name="Kantam Kontainers LLP")
        site = Site.objects.create(company=company, code="BAREJA", name="Bareja")
        for code in ["MGMT", "SALES", "PLAN", "PROD", "STORE", "PUR", "QC", "DISP", "SEC", "MAINT", "ACC", "IT", "HR", "ART"]:
            Department.objects.create(site=site, code=code, name=code)
        for code, kind in [("RM", "warehouse"), ("LBL", "label_store"), ("FG", "fg"), ("QCH", "qc_hold"), ("SCR", "scrap"), ("DSP", "dispatch"),
                           ("VS", "supplier"), ("VC", "customer"), ("VP", "production"), ("VA", "adjustment")]:
            Location.objects.create(code=code, name=code, kind=kind)
        cls.owner = make_user("owner", ["owner"])
        cls.sales = make_user("sales", ["sales_exec"], "SALES")
        cls.sales2 = make_user("sales2", ["sales_exec"], "SALES")
        cls.sales_mgr = make_user("salesmgr", ["sales_manager"], "SALES")
        cls.planner = make_user("planner", ["planner"], "PLAN")
        cls.store = make_user("store", ["store_staff"], "STORE")
        cls.store_mgr = make_user("storemgr", ["store_manager"], "STORE")
        cls.supervisor = make_user("supervisor", ["production_supervisor"], "PROD")
        cls.qc = make_user("qcmgr", ["qc_manager"], "QC")
        cls.dispatcher = make_user("dispatch", ["dispatch"], "DISP")
        cls.guard = make_user("guard", ["security_guard"], "SEC")
        cls.pur_sup = make_user("pursup", ["purchase_supervisor"], "PUR")
        cls.pur_mgr = make_user("purmgr", ["purchase_manager"], "PUR")
        cls.tech = make_user("tech", ["technician"], "MAINT")
        cls.maint = make_user("maint", ["maintenance_manager"], "MAINT")
        cls.accounts = make_user("accounts", ["accounts"], "ACC")
        cls.tv = make_user("tv", ["tv_viewer"], "PROD")

        cls.customer = Customer.objects.create(code="C1", name="Dairy Co", advance_required_pct=0)
        cls.supplier = Supplier.objects.create(code="S1", name="Poly Supplier")
        cls.pp = Item.objects.create(code="PP", name="PP", kind="polymer", stock_uom="kg")
        cls.mb = Item.objects.create(code="MBW", name="MB white", kind="masterbatch", stock_uom="kg")
        cls.tub = Item.objects.create(code="TUB", name="Tub", kind="component", stock_uom="pcs", whole_units=True)
        cls.lid = Item.objects.create(code="LID", name="Lid", kind="component", stock_uom="pcs", whole_units=True)
        cls.set_item = Item.objects.create(code="SET", name="Set", kind="set", stock_uom="pcs", whole_units=True)
        cls.product = Product.objects.create(code="R500", name="Round 500", shape="round", iml=True, set_item=cls.set_item)
        cls.pc_tub = ProductComponent.objects.create(product=cls.product, component_type="container", item=cls.tub, weight_g=Decimal("38.5"))
        cls.pc_lid = ProductComponent.objects.create(product=cls.product, component_type="lid", item=cls.lid, weight_g=Decimal("12"))
        today = timezone.localdate()
        for pc in (cls.pc_tub, cls.pc_lid):
            r = Recipe.objects.create(product_component=pc, basis="total_blend", effective_from=today - timedelta(days=1), approved=True)
            RecipeLine.objects.create(recipe=r, item=cls.pp, percent=95, sequence=1)
            RecipeLine.objects.create(recipe=r, item=cls.mb, percent=5, is_masterbatch=True, sequence=2)
        cls.arts = {}
        cls.labels = {}
        for fl in ("Mango", "Kesar", "Vanilla"):
            for ctype in ("container", "lid"):
                lab = Item.objects.create(code=f"L-{fl}-{ctype}", name=f"Label {fl} {ctype}", kind="label", stock_uom="pcs", whole_units=True)
                cls.labels[(fl, ctype)] = lab
                cls.arts[(fl, ctype)] = Artwork.objects.create(code=f"MK-{fl}-{ctype}", product=cls.product, component_type=ctype, customer=cls.customer,
                                                               flavour=fl, label_item=lab, status="approved")
        cls.m1 = Machine.objects.create(code="M01", name="JSW1", tonnage=250, has_iml_robot=True)
        cls.m2 = Machine.objects.create(code="M02", name="JSW2", tonnage=250, has_iml_robot=True)
        cls.mould_tub = Mould.objects.create(code="MT", name="Tub mould", product_component=cls.pc_tub, cavities=2, active_cavities=2, standard_cycle_s=8)
        cls.mould_lid = Mould.objects.create(code="ML", name="Lid mould", product_component=cls.pc_lid, cavities=2, active_cavities=2, standard_cycle_s=8)
        PackingRule.objects.create(product=cls.product, pcs_per_bag=25, pcs_per_carton=500, effective_from=today)

    def client_for(self, user, ip="10.1.2.3"):
        c = ZTClient(ip=ip)
        r = c.login(user.username)
        assert r.status_code == 200, r.content
        return c

    @staticmethod
    def loc(code):
        return Location.objects.get(code=code)
