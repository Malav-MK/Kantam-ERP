# Permission matrix

Generated from `backend/apps/core/rbac_matrix.py` by `manage.py permission_matrix`.
Deny by default: anything not listed is refused on the server (API, exports, attachments, actions).

## Roles

| Role | Level | Description | # permissions |
|---|---|---|---|
| `it_admin` Security / IT administrator | admin | Users, roles, devices and sessions. No commercial or production data. | 15 |
| `gm` General manager / plant head | manager | Site commitments, capacity, quality, maintenance and overdue cross-department actions. | 158 |
| `hr` HR / workforce | manager | Employee/agency events and attendance exceptions. | 23 |
| `maintenance_manager` Maintenance manager | manager | Maintenance division, PM plans, breakdown work orders. | 41 |
| `planner` Planner / production manager | manager | Plans and released PRs; revisions traced; cannot alter historical actuals. | 163 |
| `purchase_manager` Purchase manager | manager | Procurement approval / PO authority under assigned limits. | 130 |
| `qc_manager` Quality manager | manager | Inspection, hold/release, specifications and CAPA authority. | 134 |
| `sales_manager` Sales / marketing manager | manager | Leads, quotations and SO amendments; cannot post production or stock. | 68 |
| `security_supervisor` Security supervisor | manager | Scoped security approval and gate exception policy. | 81 |
| `store_manager` Store manager | manager | Receipt/issue/return/transfer entries; cannot change approved artwork or SO colour. | 124 |
| `owner` Owner / master | owner | All business dashboards and records; approves exceptions. MFA required. | 312 |
| `accounts` Accounts | staff | Receivables, payables, credit release and integration queue. | 25 |
| `artwork` Artwork / label controller | staff | KLD, artwork revisions, approvals and label eligibility. | 25 |
| `dispatch` FG / dispatch | staff | Packing, picking and shipments subject to quality/commercial release. | 42 |
| `operator` Machine operator | staff | Assigned machine/job/shift entries only. | 16 |
| `production_supervisor` Production supervisor / shift engineer | staff | Actuals, downtime and handover within open shift; cannot release QC holds. | 43 |
| `purchase_supervisor` Purchase supervisor | staff | Follow-up and supplier commitments; no PO approval authority. | 36 |
| `qc_inspector` QC inspector | staff | Records inspections; release requires quality.release. | 18 |
| `sales_exec` Sales executive | staff | Own leads, follow-ups, quotations and orders. | 44 |
| `security_guard` Security guard | staff | Visitor/driver verification and movement evidence; cannot self-approve entry. | 45 |
| `store_staff` Store staff | staff | Store movements against released shift PRs. | 27 |
| `technician` Maintenance technician | staff | Assigned work orders with evidence. | 11 |
| `tv_viewer` TV / wall display | viewer | Read-only sanitised production projection. No customer, price, people or evidence data. | 1 |

## Dashboards

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `dashboard.department.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  | ✔ |  |  |  |  |
| `dashboard.gate.view` |  | ✔ | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `dashboard.owner.view` |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `dashboard.tv.view` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  | ✔ |

## Sales & CRM

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `crm.activity.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.activity.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.activity.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.activity.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.lead.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.lead.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.lead.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.lead.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotation.approve` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.quotation.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotation.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.quotation.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotation.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotationline.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotationline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.quotationline.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.quotationline.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.sample.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.sample.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `crm.sample.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `crm.sample.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `masters.customer.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |

## Sales orders

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `sales.componentdemand.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.componentdemand.view` |  | ✔ |  |  | ✔ |  |  | ✔ |  |  | ✔ |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |
| `sales.salesorder.cancel` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.salesorder.confirm` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.salesorder.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.salesorder.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.salesorder.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.salesorder.view` |  | ✔ |  |  | ✔ |  | ✔ | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |
| `sales.so.amend_locked` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.so.approve_amendment` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.soline.cancel_balance` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.soline.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.soline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.soline.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `sales.soline.view` |  | ✔ |  |  | ✔ |  | ✔ | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |
| `sales.soversion.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `sales.soversion.view` |  | ✔ |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |

## Masters

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `masters.artwork.advance` |  |  |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.new_revision` |  |  |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.obsolete` |  |  |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |
| `masters.artwork.view` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  |  |
| `masters.contact.create` |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |
| `masters.contact.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.contact.update` |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |
| `masters.contact.view` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |
| `masters.customer.create` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  | ✔ |  |  |  |  |
| `masters.customer.export` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.customer.update` |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.customer.view` |  | ✔ |  |  | ✔ |  |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |
| `masters.holiday.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.holiday.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.holiday.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.holiday.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.item.create` |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.item.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.item.update` |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.item.view` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  |  |
| `masters.labelitem.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.labelitem.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.labelitem.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.labelitem.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.location.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.location.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.location.update` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.location.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  | ✔ |  |  |
| `masters.machine.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.machine.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.machine.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.machine.view` |  | ✔ |  | ✔ | ✔ |  | ✔ |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |
| `masters.mould.create` |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.mould.export` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.mould.update` |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.mould.view` |  | ✔ |  | ✔ | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `masters.packingrule.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.packingrule.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.packingrule.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.packingrule.view` |  | ✔ |  |  | ✔ |  | ✔ | ✔ |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `masters.product.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.product.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.product.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.product.view` |  | ✔ |  |  | ✔ |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ |  | ✔ | ✔ |  |  |  |  |
| `masters.productcomponent.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.productcomponent.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.productcomponent.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.productcomponent.view` |  | ✔ |  |  | ✔ |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ |  | ✔ | ✔ |  |  |  |  |
| `masters.reason.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.reason.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.reason.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.reason.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |
| `masters.recipe.approve` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipe.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipe.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipe.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipe.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipeline.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipeline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipeline.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.recipeline.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.supplier.create` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.update` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.view` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  |  | ✔ |  |  | ✔ |  |  |  |
| `masters.uomconversion.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.uomconversion.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.uomconversion.update` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `masters.uomconversion.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |

## Planning

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `planning.monthlyplan.approve` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.monthlyplan.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.monthlyplan.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.monthlyplan.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.monthlyplan.view` |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.planline.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.planline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.planline.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.planline.view` |  | ✔ |  |  | ✔ |  |  | ✔ |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `planning.planrevision.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.planrevision.view` |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.prmaterial.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.prmaterial.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  | ✔ | ✔ |  |  |  | ✔ |  |  |
| `planning.productionrequest.cancel` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.productionrequest.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.productionrequest.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.productionrequest.release` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.productionrequest.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `planning.productionrequest.view` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  | ✔ |  | ✔ | ✔ |  | ✔ |  |  | ✔ |  |  |

## Production

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `production.correct_closed` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `production.downtime.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |
| `production.downtime.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.downtime.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.downtime.view` |  | ✔ |  | ✔ | ✔ |  | ✔ |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |
| `production.labourallocation.create` |  |  | ✔ |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.labourallocation.export` |  | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.labourallocation.update` |  |  | ✔ |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.labourallocation.view` |  | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.productionreport.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |
| `production.productionreport.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.productionreport.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.productionreport.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  | ✔ | ✔ |  | ✔ |  |  |  |  |  |
| `production.shift.close` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.shifthandover.create` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.shifthandover.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.shifthandover.update` |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `production.shifthandover.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |

## Stores & labels

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `inventory.additional_issue.approve` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.adjust.approve` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.issue.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `inventory.label_substitute.approve` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.lot.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.lot.update` |  |  |  |  |  |  | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.lot.view` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  | ✔ |  |  |
| `inventory.reservation.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.reservation.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `inventory.return.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `inventory.stockbalance.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockbalance.opening_stock` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockbalance.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  | ✔ | ✔ |  |  | ✔ |  |  |  | ✔ |  |  |
| `inventory.stockcount.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockcount.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockcount.update` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockcount.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `inventory.stockmove.create` |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `inventory.stockmove.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `inventory.stockmove.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |

## Purchase

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `masters.supplier.create` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.update` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `masters.supplier.view` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ | ✔ | ✔ |  |  |  | ✔ |  |  | ✔ |  |  |  |
| `purchase.grn.create` |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `purchase.grn.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.grn.update` |  |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.grn.view` |  | ✔ |  |  |  | ✔ | ✔ |  |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  | ✔ |  |  |
| `purchase.grnline.create` |  |  |  |  |  | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |
| `purchase.grnline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.grnline.update` |  |  |  |  |  | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.grnline.view` |  | ✔ |  |  |  | ✔ | ✔ |  |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ | ✔ |  |  | ✔ |  |  |
| `purchase.po.approve` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.poline.create` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.poline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.poline.followup` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.poline.update` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.poline.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  | ✔ |  |  |
| `purchase.purchaseorder.create` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.purchaseorder.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.purchaseorder.update` |  |  |  |  |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.purchaseorder.view` |  | ✔ |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.rate.view` |  | ✔ |  |  |  | ✔ |  |  |  |  | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.requisition.approve` |  |  |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.requisition.create` |  |  |  | ✔ | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.requisition.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `purchase.requisition.update` |  |  |  |  | ✔ | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |  |  |  |  |  |
| `purchase.requisition.view` |  | ✔ |  | ✔ | ✔ | ✔ |  |  |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |

## Quality

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `quality.complaint.create` |  |  |  |  |  |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `quality.complaint.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `quality.complaint.update` |  |  |  |  |  |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `quality.complaint.view` |  | ✔ |  |  |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  | ✔ |  |  |  |  |  |
| `quality.inspection.create` |  |  |  |  |  |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  | ✔ |  |  |  |  |  |
| `quality.inspection.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `quality.inspection.update` |  |  |  |  |  |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `quality.inspection.view` |  | ✔ |  |  | ✔ |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  | ✔ |  |  |  |  |  |
| `quality.release` |  |  |  |  |  |  | ✔ |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |

## Packing & dispatch

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `dispatch.credit_release` |  |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |
| `fg.deliveryexception.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.deliveryexception.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.deliveryexception.view` |  | ✔ |  |  |  |  | ✔ |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatch.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatch.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatch.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatch.view` |  | ✔ |  |  |  |  | ✔ | ✔ |  |  | ✔ | ✔ |  | ✔ |  |  |  |  | ✔ |  |  |  |  |
| `fg.dispatchline.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatchline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatchline.update` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.dispatchline.view` |  | ✔ |  |  |  |  | ✔ |  |  |  | ✔ | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.packingrecord.create` |  |  |  |  |  |  |  |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.packingrecord.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `fg.packingrecord.view` |  | ✔ |  |  |  |  | ✔ |  |  |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |

## Gate & security

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gate.attendanceexception.export` |  | ✔ | ✔ |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.attendanceexception.update` |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.attendanceexception.view` |  | ✔ | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.courier.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.courier.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.courier.release` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.courier.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.courier.view` |  | ✔ |  |  |  |  |  |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.evidence.view` |  | ✔ |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gateevent.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gateevent.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gateevent.view` |  | ✔ | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.arrive` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.authorise` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `gate.gatematerial.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.depart` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.gatematerial.handover` |  |  |  |  |  |  |  |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.gatematerial.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.verify` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerial.view` |  | ✔ |  |  |  |  |  |  | ✔ | ✔ | ✔ |  |  | ✔ |  |  |  |  |  | ✔ | ✔ |  |  |
| `gate.gatematerialline.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerialline.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  | ✔ |  |  |  |  |  |  |  |  |  |
| `gate.gatematerialline.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  | ✔ |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatematerialline.view` |  | ✔ |  |  |  |  |  |  | ✔ | ✔ | ✔ |  |  | ✔ |  |  |  |  |  | ✔ | ✔ |  |  |
| `gate.gatepass.approve` |  |  |  | ✔ |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.gatepass.create` |  |  |  | ✔ |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatepass.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.gatepass.out` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatepass.return` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatepass.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.gatepass.view` |  | ✔ |  | ✔ |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.mycourier.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.mycourier.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `gate.myvisit.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.myvisit.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `gate.person.create` |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.person.export` |  | ✔ | ✔ |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.person.update` |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.person.view` |  | ✔ | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.vehicletrip.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.vehicletrip.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.vehicletrip.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.vehicletrip.view` |  | ✔ |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.approve` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.visit.create` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.enter` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.exit` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.export` |  | ✔ |  |  | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.visit.reject` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `gate.visit.request_approval` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.update` |  |  |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |
| `gate.visit.view` |  | ✔ |  |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |

## Tasks & maintenance

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `maintenance.pm.approve` |  |  |  | ✔ |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.asset.create` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.asset.export` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.asset.update` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.asset.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |
| `tasks.assign` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.breakdown.create` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |
| `tasks.breakdown.export` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  | ✔ |  |  |  |  |  |  |  |
| `tasks.breakdown.update` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  | ✔ |  |  |  |  |  | ✔ |  |
| `tasks.breakdown.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  | ✔ | ✔ |  |  |  |  |  | ✔ |  |
| `tasks.incident.create` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.incident.export` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.incident.update` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.incident.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.pmplan.create` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.pmplan.export` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.pmplan.update` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.pmplan.view` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `tasks.task.create` |  | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |
| `tasks.task.export` |  | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  | ✔ |  | ✔ | ✔ |  |  |  |  |  |  |
| `tasks.task.update` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `tasks.task.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `tasks.verify` |  | ✔ |  | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |

## Administration

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `approvals.view` |  | ✔ |  |  | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `audit.verify` | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `audit.view` | ✔ | ✔ |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `core.approvalrequest.export` | ✔ | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `core.approvalrequest.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `core.department.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `core.userlookup.export` | ✔ | ✔ |  |  | ✔ | ✔ | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `core.userlookup.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `outbox.approve` |  |  |  |  |  | ✔ |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |

## Commercial projections

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `commercial.cost.view` |  |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |
| `commercial.price.view` |  |  |  |  |  |  |  | ✔ |  |  | ✔ | ✔ |  |  |  |  |  |  | ✔ |  |  |  |  |

## Security

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `security.devices.manage` | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `security.roles.manage` | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `security.sessions.manage` | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |
| `security.users.manage` | ✔ |  |  |  |  |  |  |  |  |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |

## HR

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `hr.personal.view` |  |  | ✔ |  |  |  |  |  | ✔ |  | ✔ |  |  |  |  |  |  |  |  |  |  |  |  |

## Finance

| Permission | it_admin | gm | hr | maintenance_manager | planner | purchase_manager | qc_manager | sales_manager | security_supervisor | store_manager | owner | accounts | artwork | dispatch | operator | production_supervisor | purchase_supervisor | qc_inspector | sales_exec | security_guard | store_staff | technician | tv_viewer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `finance.dashboard.view` |  |  |  |  |  |  |  |  |  |  | ✔ | ✔ |  |  |  |  |  |  |  |  |  |  |  |
