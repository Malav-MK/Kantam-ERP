"""
Role -> permission matrix (brief p.19, p.27, p.41, p.62).

Permissions are generated from the resource registry (``<resource>.view`` etc.)
plus the explicit capability permissions below. Roles are expressed as
include/exclude glob patterns so new resources inherit sensible defaults while
sensitive capabilities must always be granted explicitly.

This file is the reviewable "permission matrix" deliverable; `manage.py
sync_rbac` applies it to the database and `docs/permission-matrix.md` is
generated from it.
"""

# Capability permissions that are not tied to one resource's CRUD.
CAPABILITIES = {
    # Field-level / projection permissions
    "commercial.price.view": ("commercial", "See selling prices, order values and quotation commercials"),
    "commercial.cost.view": ("commercial", "See cost, margin and valuation"),
    "purchase.rate.view": ("purchase", "See supplier rates and purchase values"),
    "hr.personal.view": ("hr", "See personal contact/identity details of people"),
    "gate.evidence.view": ("gate", "View gate photo / document evidence"),
    "finance.dashboard.view": ("finance", "Protected financial dashboard cards"),
    # Dashboards
    "dashboard.owner.view": ("dashboard", "Owner one-click overview"),
    "dashboard.department.view": ("dashboard", "Department dashboard"),
    "dashboard.tv.view": ("dashboard", "Sanitised TV / wall projection"),
    "dashboard.gate.view": ("dashboard", "Gate / onsite overview and muster list"),
    # Controls
    "sales.so.amend_locked": ("sales", "Request amendment of a locked SO"),
    "sales.so.approve_amendment": ("sales", "Approve post-lock SO amendments"),
    "production.shift.close": ("production", "Close a shift"),
    "production.correct_closed": ("production", "Admin correction of closed shift records"),
    "inventory.adjust.approve": ("inventory", "Approve stock adjustments / count variances"),
    "inventory.additional_issue.approve": ("inventory", "Approve issue beyond released shift demand"),
    "inventory.label_substitute.approve": ("inventory", "Approve label / masterbatch substitution"),
    "quality.release": ("quality", "Release / reject held stock"),
    "dispatch.credit_release": ("fg", "Release dispatch against commercial/credit gate"),
    "purchase.po.approve": ("purchase", "Approve purchase orders within limit"),
    "tasks.verify": ("tasks", "Verify submitted task proof"),
    "tasks.assign": ("tasks", "Assign tasks to others"),
    "maintenance.pm.approve": ("tasks", "Approve / activate PM plans"),
    "outbox.approve": ("core", "Approve outbound WhatsApp messages"),
    "approvals.view": ("core", "See approval queue"),
    "approvals.policy.manage": ("core", "Propose / approve maker-checker policies"),
    "audit.view": ("core", "View audit history"),
    "audit.verify": ("core", "Verify audit hash-chain integrity"),
    # Security administration
    "security.users.manage": ("security", "Create / disable users"),
    "security.roles.manage": ("security", "Assign roles and permissions"),
    "security.devices.manage": ("security", "Enrol / revoke devices"),
    "security.sessions.manage": ("security", "View / revoke sessions"),
}

OPERATIONS_VIEW = [
    "masters.*.view", "sales.*.view", "planning.*.view", "production.*.view", "inventory.*.view",
    "purchase.*.view", "quality.*.view", "fg.*.view", "tasks.*.view",
]

ROLES = {
    "owner": {
        "name": "Owner / master",
        "level": "owner",
        "include": ["*"],
        "exclude": [],
        "description": "All business dashboards and records; approves exceptions. MFA required.",
    },
    "it_admin": {
        "name": "Security / IT administrator",
        "level": "admin",
        "include": ["security.*", "audit.view", "audit.verify", "core.*", "approvals.policy.manage"],
        "exclude": [],
        "description": "Users, roles, devices and sessions. No commercial or production data.",
    },
    "gm": {
        "name": "General manager / plant head",
        "level": "manager",
        "include": OPERATIONS_VIEW + [
            "crm.*.view", "gate.*.view", "dashboard.owner.view", "dashboard.department.view", "dashboard.gate.view",
            "approvals.view", "tasks.*", "tasks.assign", "tasks.verify", "audit.view", "*.export",
        ],
        "exclude": ["commercial.cost.view", "hr.personal.view", "finance.dashboard.view"],
        "description": "Site commitments, capacity, quality, maintenance and overdue cross-department actions.",
    },
    "sales_manager": {
        "name": "Sales / marketing manager",
        "level": "manager",
        "include": [
            "crm.*", "sales.*", "masters.customer.*", "masters.contact.*", "masters.product.view",
            "masters.productcomponent.view", "masters.artwork.view", "masters.item.view", "masters.packingrule.view",
            "commercial.price.view", "sales.so.approve_amendment", "sales.so.amend_locked", "fg.dispatch.view",
            "planning.planline.view", "dashboard.department.view", "tasks.task.*", "tasks.assign", "tasks.verify",
            "approvals.view", "quality.complaint.view",
        ],
        "exclude": ["*.delete"],
        "description": "Leads, quotations and SO amendments; cannot post production or stock.",
    },
    "sales_exec": {
        "name": "Sales executive",
        "level": "staff",
        "include": [
            "crm.*.view", "crm.*.create", "crm.*.update", "crm.lead.*", "crm.activity.*", "crm.sample.*",
            "sales.*.view", "sales.salesorder.create", "sales.salesorder.update", "sales.soline.create",
            "sales.soline.update", "sales.salesorder.confirm", "sales.so.amend_locked",
            "masters.customer.view", "masters.customer.create", "masters.contact.*", "masters.product.view",
            "masters.productcomponent.view", "masters.artwork.view", "masters.item.view", "commercial.price.view",
            "fg.dispatch.view", "tasks.task.view", "tasks.task.update", "dashboard.department.view",
        ],
        "exclude": ["crm.quotation.approve", "*.delete", "*.export"],
        "description": "Own leads, follow-ups, quotations and orders.",
    },
    "artwork": {
        "name": "Artwork / label controller",
        "level": "staff",
        "include": [
            "masters.artwork.*", "masters.product.view", "masters.productcomponent.view", "masters.customer.view",
            "masters.item.view", "masters.supplier.view", "inventory.lot.view", "inventory.stockbalance.view",
            "sales.soline.view", "sales.salesorder.view", "planning.productionrequest.view", "tasks.task.view",
            "tasks.task.update", "dashboard.department.view",
        ],
        "exclude": [],
        "description": "KLD, artwork revisions, approvals and label eligibility.",
    },
    "planner": {
        "name": "Planner / production manager",
        "level": "manager",
        "include": [
            "planning.*", "production.*", "masters.*.view", "masters.machine.*", "masters.mould.*",
            "masters.recipe.*", "masters.recipeline.*", "masters.packingrule.*", "sales.salesorder.view",
            "sales.soline.view", "sales.componentdemand.view", "inventory.*.view", "purchase.requisition.*",
            "purchase.purchaseorder.view", "purchase.poline.view", "quality.inspection.view", "tasks.*", "tasks.assign",
            "tasks.verify", "production.shift.close", "dashboard.department.view", "approvals.view", "*.export",
        ],
        "exclude": ["production.correct_closed"],
        "description": "Plans and released PRs; revisions traced; cannot alter historical actuals.",
    },
    "production_supervisor": {
        "name": "Production supervisor / shift engineer",
        "level": "staff",
        "include": [
            "production.*", "planning.productionrequest.view", "planning.prmaterial.view", "planning.planline.view",
            "masters.machine.view", "masters.mould.view", "masters.reason.view", "masters.product.view",
            "masters.productcomponent.view", "masters.item.view", "masters.artwork.view", "inventory.lot.view",
            "tasks.task.*", "production.shift.close", "dashboard.department.view", "tasks.breakdown.*", "tasks.asset.view",
        ],
        "exclude": ["production.correct_closed", "*.delete"],
        "description": "Actuals, downtime and handover within open shift; cannot release QC holds.",
    },
    "operator": {
        "name": "Machine operator",
        "level": "staff",
        "include": [
            "production.productionreport.view", "production.productionreport.create", "production.downtime.view",
            "production.downtime.create", "planning.productionrequest.view", "masters.machine.view", "masters.reason.view",
            "tasks.task.view", "tasks.task.update", "tasks.breakdown.create", "tasks.breakdown.view",
        ],
        "exclude": [],
        "description": "Assigned machine/job/shift entries only.",
    },
    "store_manager": {
        "name": "Store manager",
        "level": "manager",
        "include": [
            "inventory.*", "masters.item.*", "masters.location.*", "masters.uomconversion.*", "masters.supplier.view",
            "masters.artwork.view", "masters.product.view", "masters.productcomponent.view", "planning.productionrequest.view",
            "planning.prmaterial.view", "purchase.grn.*", "purchase.grnline.*", "purchase.purchaseorder.view", "purchase.poline.view",
            "purchase.requisition.*", "gate.gatematerial.view", "gate.gatematerialline.view", "gate.gatematerial.handover",
            "gate.courier.view", "gate.courier.acknowledge", "tasks.task.*", "tasks.assign", "dashboard.department.view", "approvals.view", "*.export",
        ],
        "exclude": ["inventory.adjust.approve", "inventory.label_substitute.approve"],
        "description": "Receipt/issue/return/transfer entries; cannot change approved artwork or SO colour.",
    },
    "store_staff": {
        "name": "Store staff",
        "level": "staff",
        "include": [
            "inventory.*.view", "inventory.stockmove.create", "inventory.issue.create", "inventory.return.create",
            "masters.item.view", "masters.location.view", "masters.artwork.view", "planning.productionrequest.view",
            "planning.prmaterial.view", "purchase.grn.view", "purchase.grn.create", "purchase.grnline.view",
            "purchase.grnline.create", "purchase.poline.view", "gate.gatematerial.view", "gate.gatematerialline.view",
            "tasks.task.view", "tasks.task.update",
        ],
        "exclude": [],
        "description": "Store movements against released shift PRs.",
    },
    "purchase_supervisor": {
        "name": "Purchase supervisor",
        "level": "staff",
        "include": [
            "purchase.*.view", "purchase.purchaseorder.create", "purchase.purchaseorder.update", "purchase.poline.create",
            "purchase.poline.update", "purchase.poline.followup", "purchase.requisition.create", "purchase.requisition.update",
            "masters.supplier.*", "masters.contact.*", "masters.item.view", "masters.artwork.view", "inventory.stockbalance.view",
            "inventory.lot.view", "purchase.rate.view", "planning.prmaterial.view", "tasks.task.*", "dashboard.department.view",
        ],
        "exclude": ["purchase.po.approve", "purchase.requisition.approve"],
        "description": "Follow-up and supplier commitments; no PO approval authority.",
    },
    "purchase_manager": {
        "name": "Purchase manager",
        "level": "manager",
        "include": [
            "purchase.*", "masters.supplier.*", "masters.contact.*", "masters.item.*", "masters.artwork.view",
            "inventory.*.view", "purchase.rate.view", "purchase.po.approve", "planning.prmaterial.view",
            "planning.productionrequest.view", "tasks.*", "tasks.assign", "tasks.verify", "dashboard.department.view",
            "approvals.view", "outbox.approve", "*.export",
        ],
        "exclude": [],
        "description": "Procurement approval / PO authority under assigned limits.",
    },
    "qc_manager": {
        "name": "Quality manager",
        "level": "manager",
        "include": [
            "quality.*", "quality.release", "inventory.lot.*", "inventory.stockbalance.view", "masters.*.view",
            "production.*.view", "planning.productionrequest.view", "purchase.grn.view", "purchase.grnline.*",
            "fg.*.view", "sales.salesorder.view", "sales.soline.view", "tasks.*", "tasks.assign", "tasks.verify",
            "dashboard.department.view", "approvals.view", "*.export",
        ],
        "exclude": ["masters.customer.view"],
        "description": "Inspection, hold/release, specifications and CAPA authority.",
    },
    "qc_inspector": {
        "name": "QC inspector",
        "level": "staff",
        "include": [
            "quality.inspection.view", "quality.inspection.create", "quality.complaint.view", "inventory.lot.view",
            "production.productionreport.view", "planning.productionrequest.view", "masters.product.view",
            "masters.productcomponent.view", "masters.artwork.view", "masters.item.view", "purchase.grnline.view",
            "tasks.task.view", "tasks.task.update",
        ],
        "exclude": [],
        "description": "Records inspections; release requires quality.release.",
    },
    "dispatch": {
        "name": "FG / dispatch",
        "level": "staff",
        "include": [
            "fg.*", "inventory.lot.view", "inventory.stockbalance.view", "sales.salesorder.view", "sales.soline.view",
            "sales.componentdemand.view", "masters.customer.view", "masters.product.view", "masters.productcomponent.view",
            "masters.packingrule.view", "masters.item.view", "masters.location.view", "gate.gatematerial.view", "gate.gatematerial.create",
            "gate.gatematerial.authorise", "gate.gatematerialline.*", "tasks.task.*", "dashboard.department.view",
        ],
        "exclude": ["dispatch.credit_release"],
        "description": "Packing, picking and shipments subject to quality/commercial release.",
    },
    "accounts": {
        "name": "Accounts",
        "level": "staff",
        "include": [
            "sales.salesorder.view", "sales.soline.view", "fg.dispatch.view", "fg.dispatchline.view", "purchase.*.view",
            "masters.customer.view", "masters.supplier.view", "commercial.price.view", "purchase.rate.view",
            "commercial.cost.view", "finance.dashboard.view", "dispatch.credit_release", "outbox.*", "dashboard.department.view",
        ],
        "exclude": [],
        "description": "Receivables, payables, credit release and integration queue.",
    },
    "security_guard": {
        "name": "Security guard",
        "level": "staff",
        "include": [
            "gate.*.view", "gate.*.create", "gate.*.update", "gate.visit.request_approval", "gate.visit.enter", "gate.visit.exit",
            "gate.gateevent.*", "gate.vehicletrip.*", "gate.gatematerial.arrive", "gate.gatematerial.verify", "gate.gatematerial.depart", "gate.courier.receive",
            "gate.courier.release", "gate.gatepass.out", "gate.gatepass.return", "gate.evidence.view", "dashboard.gate.view",
            "masters.supplier.view",  # supplier name for inward arrivals only
            "tasks.task.view", "tasks.task.update",
        ],
        "exclude": ["gate.visit.approve", "gate.visit.reject", "gate.gatematerial.authorise", "gate.gatepass.approve", "gate.person.update"],
        "description": "Visitor/driver verification and movement evidence; cannot self-approve entry.",
    },
    "security_supervisor": {
        "name": "Security supervisor",
        "level": "manager",
        "include": ["gate.*", "gate.evidence.view", "hr.personal.view", "dashboard.gate.view", "tasks.*", "tasks.assign", "tasks.verify", "approvals.view"],
        "exclude": [],
        "description": "Scoped security approval and gate exception policy.",
    },
    "hr": {
        "name": "HR / workforce",
        "level": "manager",
        "include": [
            "gate.person.*", "gate.gateevent.view", "gate.attendanceexception.*", "hr.personal.view", "tasks.task.*",
            "dashboard.gate.view", "production.labourallocation.*",
        ],
        "exclude": [],
        "description": "Employee/agency events and attendance exceptions.",
    },
    "maintenance_manager": {
        "name": "Maintenance manager",
        "level": "manager",
        "include": [
            "tasks.*", "tasks.assign", "tasks.verify", "maintenance.pm.approve", "masters.machine.view", "masters.mould.*",
            "production.downtime.view", "inventory.stockbalance.view", "purchase.requisition.create", "purchase.requisition.view",
            "dashboard.department.view", "gate.gatepass.view", "gate.gatepass.create", "gate.gatepass.approve",
        ],
        "exclude": [],
        "description": "Maintenance division, PM plans, breakdown work orders.",
    },
    "technician": {
        "name": "Maintenance technician",
        "level": "staff",
        "include": ["tasks.task.view", "tasks.task.update", "tasks.breakdown.view", "tasks.breakdown.update", "tasks.asset.view", "masters.machine.view"],
        "exclude": [],
        "description": "Assigned work orders with evidence.",
    },
    "tv_viewer": {
        "name": "TV / wall display",
        "level": "viewer",
        "include": ["dashboard.tv.view"],
        "exclude": [],
        "description": "Read-only sanitised production projection. No customer, price, people or evidence data.",
    },
}

# Brief p.27: these levels must use MFA in production.
MFA_LEVELS = {"owner", "admin", "manager"}
