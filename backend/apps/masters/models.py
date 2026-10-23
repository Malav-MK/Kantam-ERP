"""
Master data (brief p.5).

Used masters are never deleted - they are deactivated so historical links stay
intact. Recipes, packing rules and artwork carry revisions/effective dates and
released jobs keep their own snapshot, so editing a master never rewrites the
past (p.19).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseRecord

COMPONENT_TYPES = [("container", "Container / tub"), ("lid", "Lid"), ("handle", "Handle")]


class Customer(BaseRecord):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    plant_branch = models.CharField("Plant / branch", max_length=120, blank=True)
    industry = models.CharField(max_length=80, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    billing_address = models.TextField(blank=True)
    delivery_address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=120, default="50% advance, balance before dispatch")
    freight_basis = models.CharField(max_length=60, default="Ex-Works Bareja")
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance_required_pct = models.DecimalField(max_digits=5, decimal_places=2, default=50)
    lead_time_days = models.PositiveIntegerField(default=21)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="Account owner")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Supplier(BaseRecord):
    CATEGORIES = [
        ("polymer", "Polymer"), ("labels", "Labels / stickers"), ("masterbatch", "Masterbatch"), ("packing", "Packing material"),
        ("spares", "Spares / maintenance"), ("services", "Services"), ("other", "Other"),
    ]
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORIES, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=120, blank=True)
    lead_time_days = models.PositiveIntegerField(default=7)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Contact(BaseRecord):
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="contacts")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField(max_length=120)
    designation = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=20, blank=True, help_text="Mobile / WhatsApp number with country code, e.g. +91 98xxxxxxxx")
    whatsapp_opt_in = models.BooleanField(default=False, help_text="Recorded consent for WhatsApp business messages (p.50)")
    active = models.BooleanField(default=True)

    def clean(self):
        if bool(self.customer_id) == bool(self.supplier_id):
            raise ValidationError("A contact belongs to exactly one customer or one supplier.")

    def __str__(self):
        return self.name


class Item(BaseRecord):
    """Any stocked item: raw materials, labels, packing, produced components and packed sets."""

    KINDS = [
        ("polymer", "Polymer"), ("masterbatch", "Masterbatch"), ("impact_modifier", "Impact modifier"),
        ("label", "IML label"), ("polybag", "Polybag"), ("carton", "Outer box / carton"), ("tape", "BOPP tape"),
        ("additive", "Other additive"), ("spare", "Spare part"), ("regrind", "Regrind"),
        ("component", "Moulded component (SFG)"), ("set", "Packed set (FG)"), ("other", "Other"),
    ]
    UNITS = [("kg", "kg"), ("pcs", "pieces"), ("roll", "rolls"), ("box", "boxes"), ("bag", "bags"), ("nos", "numbers"), ("ltr", "litres")]
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KINDS)
    stock_uom = models.CharField("Stock unit", max_length=5, choices=UNITS)
    whole_units = models.BooleanField(default=False, help_text="Integer quantities only (pieces, labels, cartons)")
    grade = models.CharField(max_length=80, blank=True)
    colour = models.CharField(max_length=60, blank=True)
    group = models.CharField(max_length=60, blank=True, help_text="Label group / classification (15 label groups to reconcile)")
    min_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    safety_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    moq = models.DecimalField("MOQ", max_digits=14, decimal_places=3, default=0)
    order_multiple = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    lead_time_days = models.PositiveIntegerField(default=7)
    default_supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    standard_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    legacy_code = models.CharField(max_length=60, blank=True, help_text="Existing ID preserved from migration")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class UOMConversion(BaseRecord):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="conversions")
    from_uom = models.CharField(max_length=10)
    factor = models.DecimalField(max_digits=14, decimal_places=6, help_text="Stock units per 1 from-unit, e.g. 1 box = 500 labels")
    approved = models.BooleanField(default=False)

    class Meta:
        unique_together = [("item", "from_uom")]


class Product(BaseRecord):
    SHAPES = [("round", "Round"), ("square", "Square"), ("rectangular", "Rectangular"), ("triangular", "Triangular"), ("hexagonal", "Hexagonal"), ("octagonal", "Octagonal / decagonal"), ("other", "Other")]
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    shape = models.CharField(max_length=12, choices=SHAPES)
    capacity_ml = models.PositiveIntegerField(null=True, blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, help_text="Blank = common design available to all customers")
    customer_code = models.CharField(max_length=60, blank=True)
    iml = models.BooleanField("IML", default=True)
    set_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+", help_text="FG item for the packed complete set")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProductComponent(BaseRecord):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="components")
    component_type = models.CharField(max_length=10, choices=COMPONENT_TYPES)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+", help_text="SFG component item")
    qty_per_set = models.PositiveSmallIntegerField(default=1)
    weight_g = models.DecimalField("Shot contribution (g/pc)", max_digits=8, decimal_places=2)
    iml = models.BooleanField("IML", default=True)
    labels_per_piece = models.PositiveSmallIntegerField(default=1)
    procured = models.BooleanField(default=False, help_text="Bought-in rather than moulded (e.g. some handles)")

    class Meta:
        unique_together = [("product", "component_type")]

    def __str__(self):
        return f"{self.product.code} / {self.get_component_type_display()}"


class Artwork(BaseRecord):
    """One revision of an artwork/label design (brief p.8)."""

    STATUS = [
        ("requirement", "Requirement / KLD"), ("draft", "Design draft"), ("internal_review", "Internal review"),
        ("customer_approval", "Customer approval"), ("print_release", "Print release"), ("approved", "Eligible for production"),
        ("obsolete", "Obsolete"),
    ]
    FINISHES = [
        ("glossy", "Glossy"), ("matte", "Matte"), ("metallic", "Metallic"), ("foil", "Foil"),
        ("orange_peel", "Orange Peel (Normal)"), ("orange_peel_glossy", "Orange Peel (Glossy)"), ("orange_peel_foil", "Orange Peel + Foil"),
        ("transparent", "Transparent"), ("transparent_lam12", "Transparent + (12 Mic. lamination)"), ("other", "Other"),
    ]
    code = models.CharField("MK-ART / design no.", max_length=40)
    revision = models.PositiveSmallIntegerField(default=1)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="artworks")
    component_type = models.CharField(max_length=10, choices=COMPONENT_TYPES)
    customers = models.ManyToManyField(Customer, blank=True, related_name="artworks", help_text="Customers this design is for")
    flavour = models.CharField(max_length=80, blank=True)
    common_design = models.BooleanField(default=False)
    label_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="artworks", limit_choices_to={"kind": "label"})
    kld_reference = models.CharField("KLD reference", max_length=80, blank=True)
    height_mm = models.DecimalField("Height (mm)", max_digits=8, decimal_places=2, null=True, blank=True)
    width_mm = models.DecimalField("Width (mm)", max_digits=8, decimal_places=2, null=True, blank=True)
    colour_reference = models.CharField(max_length=120, blank=True)
    finish = models.CharField(max_length=20, choices=FINISHES)
    finish_other = models.CharField("Other finish", max_length=80, blank=True)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS, default="requirement", db_index=True)
    internal_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    customer_approved_on = models.DateField(null=True, blank=True)
    customer_approval_ref = models.CharField(max_length=120, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by")

    class Meta:
        unique_together = [("code", "revision")]

    @property
    def eligible(self):
        return self.status == "approved"

    @property
    def size(self):
        """'Height: 98.25 MM, Width: 506 MM'"""
        def mm(v):
            return f"{v.normalize():f}" if v is not None else "-"
        if self.height_mm is None and self.width_mm is None:
            return ""
        return f"Height: {mm(self.height_mm)} MM, Width: {mm(self.width_mm)} MM"

    def __str__(self):
        flavour = f" {self.flavour}" if self.flavour else ""
        return f"{self.code} R{self.revision}{flavour} ({self.component_type})"


class Recipe(BaseRecord):
    BASIS = [("total_blend", "Total blend (fractions sum to 100%)"), ("base_resin", "Base resin (additives % of resin)")]
    product_component = models.ForeignKey(ProductComponent, on_delete=models.PROTECT, related_name="recipes")
    revision = models.PositiveSmallIntegerField(default=1)
    basis = models.CharField(max_length=12, choices=BASIS, default="total_blend")
    loss_allowance_pct = models.DecimalField("Runner/startup/purge allowance %", max_digits=5, decimal_places=2, default=0)
    label_allowance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    expected_yield_pct = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    effective_from = models.DateField()
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("product_component", "revision")]

    def __str__(self):
        return f"{self.product_component} recipe R{self.revision}"


class RecipeLine(BaseRecord):
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    percent = models.DecimalField(max_digits=6, decimal_places=3)
    is_masterbatch = models.BooleanField(default=False)
    sequence = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["sequence"]


class PackingRule(BaseRecord):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="packing_rules")
    revision = models.PositiveSmallIntegerField(default=1)
    pcs_per_bag = models.PositiveIntegerField()
    pcs_per_carton = models.PositiveIntegerField()
    bag_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    carton_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tape_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cartons_per_tape_roll = models.PositiveIntegerField(default=0)
    effective_from = models.DateField()
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("product", "revision")]


class Machine(BaseRecord):
    DRIVES = [("hydraulic", "Hydraulic"), ("electric", "All-electric"), ("hybrid", "Hybrid")]
    STATUS = [("available", "Available"), ("maintenance", "Maintenance block"), ("breakdown", "Breakdown"), ("inactive", "Inactive")]
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    make = models.CharField(max_length=60, blank=True)
    model_no = models.CharField("Model", max_length=60, blank=True)
    serial_no = models.CharField(max_length=60, blank=True)
    tonnage = models.PositiveIntegerField(null=True, blank=True)
    drive_type = models.CharField(max_length=10, choices=DRIVES, default="hydraulic")
    has_iml_robot = models.BooleanField("IML robot fitted", default=False)
    robot_ref = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="available")
    sequence = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class Mould(BaseRecord):
    STATUS = [("available", "Available"), ("in_use", "In use"), ("in_repair", "In repair / tool room"), ("out", "Out on gate pass"), ("inactive", "Inactive")]
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    product_component = models.ForeignKey(ProductComponent, on_delete=models.PROTECT, related_name="moulds")
    cavities = models.PositiveSmallIntegerField()
    active_cavities = models.PositiveSmallIntegerField()
    standard_cycle_s = models.DecimalField("Standard cycle (s)", max_digits=6, decimal_places=2)
    setup_minutes = models.PositiveIntegerField(default=60)
    compatible_machines = models.ManyToManyField(Machine, blank=True, related_name="moulds")
    min_tonnage = models.PositiveIntegerField(null=True, blank=True)
    requires_robot = models.BooleanField(default=True)
    ownership = models.CharField(max_length=20, choices=[("kantam", "Kantam"), ("customer", "Customer-owned")], default="kantam")
    shot_count = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="available")
    location = models.CharField(max_length=80, blank=True)

    def clean(self):
        if self.active_cavities and self.cavities and self.active_cavities > self.cavities:
            raise ValidationError("Active cavities cannot exceed total cavities.")

    def __str__(self):
        return f"{self.code} ({self.active_cavities}/{self.cavities} cav)"


class Location(BaseRecord):
    KINDS = [
        ("warehouse", "Warehouse / rack / bin"), ("label_store", "Label store"), ("machine_side", "Machine-side WIP"),
        ("qc_hold", "QC hold area"), ("fg", "FG store"), ("dispatch", "Dispatch staging"), ("scrap", "Scrap / regrind"),
        ("supplier", "Supplier (virtual)"), ("customer", "Customer (virtual)"), ("production", "Production consumption (virtual)"),
        ("adjustment", "Stock adjustment (virtual)"),
    ]
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=15, choices=KINDS)
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.PROTECT)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

    @property
    def is_virtual(self):
        return self.kind in {"supplier", "customer", "production", "adjustment"}

    def __str__(self):
        return f"{self.code} {self.name}"


class Reason(BaseRecord):
    KINDS = [
        ("downtime", "Downtime"), ("rejection", "Rejection / defect"), ("hold", "QC hold"), ("amendment", "Amendment"),
        ("loss", "Lost / hold reason"), ("adjustment", "Stock adjustment"), ("cancellation", "Cancellation"),
    ]
    kind = models.CharField(max_length=12, choices=KINDS)
    code = models.CharField(max_length=20)
    text = models.CharField(max_length=160)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("kind", "code")]

    def __str__(self):
        return f"{self.code} {self.text}"


class Holiday(BaseRecord):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)
