"""
Illustrative demo data for review / training. All values are invented (brief
p.24/p.67: screenshot and example figures are not Kantam business facts).
Run only on a test database.
"""
import getpass
import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import password_validation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Department, Role, User, UserRole
from apps.inventory.services import post_opening_balance
from apps.masters.models import (
    Artwork, Contact, Customer, Item, Location, Machine, Mould, PackingRule, Product, ProductComponent, Recipe, RecipeLine, Supplier,
)

DEMO_USERS = [
    ("gm", "Plant Head (demo)", ["gm"], "MGMT"), ("itadmin", "IT Admin (demo)", ["it_admin"], "IT"),
    ("sales.mgr", "Sales Manager (demo)", ["sales_manager"], "SALES"), ("sales.exec", "Sales Executive (demo)", ["sales_exec"], "SALES"),
    ("artwork", "Artwork Controller (demo)", ["artwork"], "ART"), ("planner", "Planner (demo)", ["planner"], "PLAN"),
    ("supervisor", "Shift Supervisor (demo)", ["production_supervisor"], "PROD"), ("operator", "Operator (demo)", ["operator"], "PROD"),
    ("store.mgr", "Store Manager (demo)", ["store_manager"], "STORE"), ("store", "Store Staff (demo)", ["store_staff"], "STORE"),
    ("pur.sup", "Purchase Supervisor (demo)", ["purchase_supervisor"], "PUR"), ("pur.mgr", "Purchase Manager (demo)", ["purchase_manager"], "PUR"),
    ("qc.mgr", "QC Manager (demo)", ["qc_manager"], "QC"), ("qc", "QC Inspector (demo)", ["qc_inspector"], "QC"),
    ("dispatch", "Dispatch (demo)", ["dispatch"], "DISP"), ("guard", "Security Guard (demo)", ["security_guard"], "SEC"),
    ("sec.sup", "Security Supervisor (demo)", ["security_supervisor"], "SEC"), ("hr", "HR (demo)", ["hr"], "HR"),
    ("maint.mgr", "Maintenance Manager (demo)", ["maintenance_manager"], "MAINT"), ("tech", "Technician (demo)", ["technician"], "MAINT"),
    ("accounts", "Accounts (demo)", ["accounts"], "ACC"), ("tv", "Shop-floor TV (demo)", ["tv_viewer"], "PROD"),
]


class Command(BaseCommand):
    help = "Load illustrative DEMO masters, users and stock (test databases only)."

    def add_arguments(self, parser):
        parser.add_argument("--password-env", help="Environment variable holding the shared demo-user password")

    @transaction.atomic
    def handle(self, *args, **o):
        owner = User.objects.filter(role_grants__role__code="owner").first()
        if owner is None:
            raise CommandError("Run `manage.py bootstrap` first.")
        if Customer.objects.filter(code="DEMO-C1").exists():
            raise CommandError("Demo data already loaded.")
        pwd = os.environ.get(o["password_env"], "") if o.get("password_env") else getpass.getpass("Password for all demo users: ")
        password_validation.validate_password(pwd)

        managers = {}
        for username, name, roles, dept in DEMO_USERS:
            d = Department.objects.get(code=dept)
            u = User(username=username, full_name=name, department=d, site=d.site, job_title=name.replace(" (demo)", ""))
            u.set_password(pwd)
            u.save()
            for r in roles:
                UserRole.objects.create(user=u, role=Role.objects.get(code=r), reason="demo seed")
            managers[username] = u
        for staff, mgr in [("sales.exec", "sales.mgr"), ("store", "store.mgr"), ("operator", "supervisor"), ("qc", "qc.mgr"), ("tech", "maint.mgr"),
                           ("pur.sup", "pur.mgr"), ("guard", "sec.sup")]:
            managers[staff].reporting_manager = managers[mgr]
            managers[staff].save(update_fields=["reporting_manager"])

        by = owner
        c1 = Customer.objects.create(code="DEMO-C1", name="Demo Dairy Foods", industry="Dairy / ice cream", owner=managers["sales.exec"],
                                     delivery_address="Demo address, Gujarat", created_by=by)
        Customer.objects.create(code="DEMO-C2", name="Demo Sweets Co.", industry="Sweets", owner=managers["sales.exec"], created_by=by)
        Contact.objects.create(customer=c1, name="Demo Buyer", phone="+910000000000", whatsapp_opt_in=True, created_by=by)
        s_poly = Supplier.objects.create(code="DEMO-S1", name="Demo Polymers", category="Polymer", lead_time_days=5, created_by=by)
        s_lbl = Supplier.objects.create(code="DEMO-S2", name="Demo Label Printers", category="Labels", lead_time_days=10, created_by=by)
        Contact.objects.create(supplier=s_poly, name="Polymer desk", phone="+910000000001", whatsapp_opt_in=True, created_by=by)

        def item(code, name, kind, uom, whole=False, **kw):
            return Item.objects.create(code=code, name=name, kind=kind, stock_uom=uom, whole_units=whole, created_by=by, **kw)

        pp = item("PP-HOMO-01", "PP homopolymer (demo grade)", "polymer", "kg", default_supplier=s_poly, safety_stock=500, moq=1000, order_multiple=25)
        mb_white = item("MB-WHITE", "White masterbatch", "masterbatch", "kg", colour="White")
        item("MB-RED", "Red masterbatch", "masterbatch", "kg", colour="Red")
        bag = item("PB-500", "Polybag for 500ml", "polybag", "pcs", True)
        ctn = item("CTN-500", "Carton 500ml", "carton", "pcs", True)
        tape = item("TAPE-48", "BOPP tape 48mm", "tape", "roll", True)
        tub = item("SFG-R500-TUB", "Round 500ml tub", "component", "pcs", True)
        lid = item("SFG-R500-LID", "Round 500ml lid", "component", "pcs", True)
        setit = item("FG-R500-SET", "Round 500ml set (tub+lid)", "set", "pcs", True)

        prod = Product.objects.create(code="R500", name="Round 500 ml IML tub", shape="round", capacity_ml=500, iml=True, set_item=setit, created_by=by)
        pc_tub = ProductComponent.objects.create(product=prod, component_type="container", item=tub, weight_g=Decimal("22.0"), created_by=by)
        pc_lid = ProductComponent.objects.create(product=prod, component_type="lid", item=lid, weight_g=Decimal("12.0"), created_by=by)
        PackingRule.objects.create(product=prod, pcs_per_bag=25, pcs_per_carton=500, bag_item=bag, carton_item=ctn, tape_item=tape,
                                   cartons_per_tape_roll=40, effective_from=timezone.localdate(), created_by=by)
        today = timezone.localdate()
        for pc in (pc_tub, pc_lid):
            r = Recipe.objects.create(product_component=pc, basis="total_blend", loss_allowance_pct=2, label_allowance_pct=1, expected_yield_pct=98,
                                      effective_from=today - timedelta(days=30), approved=True, approved_by=by, created_by=by)
            RecipeLine.objects.create(recipe=r, item=pp, percent=98, sequence=1, created_by=by)
            RecipeLine.objects.create(recipe=r, item=mb_white, percent=2, is_masterbatch=True, sequence=2, created_by=by)

        rm = Location.objects.get(code="RM-01")
        lbl_loc = Location.objects.get(code="LBL-01")
        pkg = Location.objects.get(code="PKG-01")
        for flavour in ("Mango", "Kesar", "Vanilla"):
            for pc, ctype in ((pc_tub, "container"), (pc_lid, "lid")):
                lab = item(f"LBL-R500-{flavour[:3].upper()}-{ctype[:3].upper()}", f"IML label R500 {flavour} {ctype}", "label", "pcs", True,
                           group="R500 dairy", default_supplier=s_lbl)
                art = Artwork.objects.create(code=f"MK-ART-R500-{flavour[:3].upper()}-{ctype[:1].upper()}", product=prod, component_type=ctype,
                                             customer=c1, flavour=flavour, label_item=lab, kld_reference="KLD-DEMO", status="approved",
                                             customer_approval_ref="Demo approval mail", customer_approved_on=today, released_at=timezone.now(),
                                             supplier=s_lbl, created_by=by)
                post_opening_balance(item=lab, qty=40000, location=lbl_loc, user=by, artwork=art, supplier_lot=f"DEMO-{flavour[:3].upper()}")
        post_opening_balance(item=pp, qty=3000, location=rm, user=by, supplier_lot="PP-DEMO-1")
        post_opening_balance(item=mb_white, qty=150, location=rm, user=by, supplier_lot="MBW-DEMO-1")
        for it, q in ((bag, 5000), (ctn, 400), (tape, 30)):
            post_opening_balance(item=it, qty=q, location=pkg, user=by)

        for i in range(1, 5):
            m = Machine.objects.create(code=f"M{i:02d}", name=f"JSW {i} (demo)", make="JSW", tonnage=250, has_iml_robot=True, sequence=i, created_by=by)
            from apps.tasks.models import Asset, PMPlan
            asset = Asset.objects.create(code=f"AST-M{i:02d}", name=f"Injection machine M{i:02d}", kind="machine", machine=m, make="JSW",
                                         manual_reference="Confirm from machine plate (demo)", created_by=by)
            PMPlan.objects.create(asset=asset, title="Shift inspection - alarms, leaks, guards", interval="12h", oem_reference="Demo checklist - OEM approval pending",
                                  checklist=[{"id": 1, "text": "Alarm history reviewed", "critical": False}, {"id": 2, "text": "Guards/doors functional", "critical": True}],
                                  assignee=managers["tech"], reviewer=managers["maint.mgr"], next_due=timezone.now() + timedelta(hours=1),
                                  status="approved", approved_by=managers["maint.mgr"], created_by=by)
        mt = Mould.objects.create(code="MLD-R500-TUB", name="R500 tub 4-cav", product_component=pc_tub, cavities=4, active_cavities=4, standard_cycle_s=8, created_by=by)
        ml = Mould.objects.create(code="MLD-R500-LID", name="R500 lid 4-cav", product_component=pc_lid, cavities=4, active_cavities=4, standard_cycle_s=7, created_by=by)
        machines = list(Machine.objects.all())
        mt.compatible_machines.set(machines[:2])
        ml.compatible_machines.set(machines[2:])
        self.stdout.write(self.style.SUCCESS(f"Demo data loaded: {len(DEMO_USERS)} users (password as entered), masters, opening stock, PM plans."))
        self.stdout.write("Demo users: " + ", ".join(u for u, *_ in DEMO_USERS))
