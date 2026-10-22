# Requirements traceability: brief v5.0 → implementation → evidence

Status key: **Built** = implemented with server-side enforcement · **Partial** = core built, scope noted · **Open** = needs a Kantam decision or data.

| Brief | Requirement | Status | Implementation | Test evidence |
|---|---|---|---|---|
| p.3 | SO auto-lock after 24 h; amendments need reason, old/new values and approval | Built | `sales/models.py` `locked_at`, `sales/services.py` `request_line_amendment` / `apply_line_amendment`, `SOVersion` | `test_24h_lock_and_amendment_with_approval_keeps_history`, `test_maker_cannot_approve_own_amendment` |
| p.3, p.10 | Day 09–21 / night 21–09; night keeps its shift date; closed shifts immutable | Built | `core/shifts.py`, `ShiftLock`, `production/services.py` `assert_shift_open`, worker `auto_close_shifts` | `ShiftTests`, `test_closed_shift_blocks_normal_entries` |
| p.3, p.9 | Monthly plan editable via revisions; baseline kept; actual cycle reforecasts | Built | `planning/services.py` `approve_plan`, `revise_line`, `reforecast_machine`; `production/services.py` `_actual_cycle_reforecast` | `test_production_report_reconciles_and_cycle_reforecasts_keeping_baseline`, `test_cycle_8_to_10…` |
| p.3, p.10 | Issue per 12-h shift; cancelled PR returns against old PR; reissue under a new linked PR | Built | `inventory/services.py` `issue_to_pr` / `return_from_pr`, `planning/services.py` `cancel_pr` | `test_cancelled_pr_returns_against_old_pr_and_reissues_new` |
| p.3 | Next month prepared around day 20 without closing the current month | Built | `planning/resources.py` `MonthlyPlanResource.before_create`; balances derive from the ledger only | — |
| p.3, p.8 | Preserve label-store v14 controls | **Open** | v14 file not supplied; label controls (artwork-revision issue matching, approved-only) are built | `test_pr_release_issue_controls_and_wrong_label_blocked` |
| p.4 | Permanent IDs, document numbers, status, owner, history; event, shift and posting times kept separately | Built | `core/models.py` `BaseRecord` / `DocumentRecord`, `StockMove.event_time` / `shift_date` / `created_at` | — |
| p.4 | Atomic postings, no duplicate on retry, no double allocation | Built | `transaction.atomic`, `select_for_update`, `@idempotent`, item-row lock in `reserve()` | `test_two_users_cannot_allocate_same_stock`, idempotent replay assertions |
| p.5 | Dependent selections; masterbatch follows the SO; recipe snapshot; deactivate, never delete | Built | `sales/services.py` `validate_line`, `pr_materials` MB substitution, PR `snapshot`, `on_delete=PROTECT` | `test_incompatible_artwork_prevented` |
| p.6 | CRM pipeline, activities, samples, quotation versions, convert once to SO | Built | `crm/resources.py` | — |
| p.7 | Line-level component demand; ordered/allocated/planned/good/held/packed/dispatched kept separate | Built | `regenerate_demand`, `sales/services.py` `quantities`, `readiness` | `test_multi_flavour_order_creates_distinct_component_demand` |
| p.8 | Artwork workflow; obsolete revision impact list; wrong label blocked at issue | Built | `masters/resources.py` `ArtworkResource`, `_artwork_impact`; `issue_to_pr` | wrong-label test |
| p.9 | Machine/mould/robot compatibility; next free time; Day → Date → Machine → Shift → Select Plan | Built | `planning/services.py` `compatibility_issues`, `next_free_time`; UI `PlanningBoard` / `ScheduleDialog` | — |
| p.10 | Job segments without overlap; output reconciliation; admin correction with controlled postings | Built | `create_pr` overlap check, `validate_report`, `admin_correct` | reconciliation test |
| p.11 | Central calculation dictionary with inputs shown | Built | `core/calc.py`; `basis` stored on `PRMaterial`, `line_metrics` | `CalcTests` |
| p.12 | Ledger movement types; on-hand/held/reserved/available/incoming/machine-side; no negative stock; counts with approval | Built | `inventory/services.py`, `StockBalance` check constraint, `StockCount` + approval handler | `test_negative_stock_blocked`, `test_fractional_pieces_rejected` |
| p.13 | Requisition → PO (maker/checker) → staged follow-up → GRN → accepted-only balance; time-phased shortages | Built | `purchase/` | `test_500_declared_490…`, `test_creator_cannot_approve_own_po`, `test_late_arrival…` |
| p.14 | Inspection dispositions; COA container/lid separate from recorded data; complaint CAPA; traceability | Built | `quality/`, `inventory/trace.py`, `dashboards/documents.py` `render_coa` | `test_complaint_traces_back_to_component_lots` |
| p.15 | Matching by compatible variant; packing without double counting; unpack | Built | `fg/services.py` | `test_10000_containers_8000_lids…`, `test_excess_lids…` |
| p.16 | Dispatch workflow; QC and commercial gates; single posting; delivery exceptions | Built | `fg/services.py` `validate_dispatch`, `post_dispatch` | `test_qc_hold_prevents_shipment`, `test_dispatch_posts_once…`, `test_commercial_gate…` |
| p.16, p.64 | Accounting integration and margin | **Open** | Financial cards show "Not connected"; outbox has an `erp` channel ready for a connector | — |
| p.17 | Maintenance, labour allocation, inventory locations | Built | `tasks/`, `production` `LabourAllocation`, `masters` `Location` | — |
| p.17 | Sales-visit / vehicle GPS location | **Open** | Not built pending purpose and consent decision | — |
| p.18 | Event synchronisation and alerts with owner, severity, dedupe | Built | `core/services.py` `notify` (dedupe keys), notifications at each event | — |
| p.19, p.27 | Role boundaries, server-side locks, audit, no hard delete | Built | `core/rbac*.py`, `registry.py`, `audit.py` | `RbacTests`, `test_audit_chain_detects_tampering` |
| p.20, p.26 | Owner one-click dashboard with drill-down and freshness; machine board; TV view | Built | `dashboards/services.py`; UI `Boards.tsx` | `test_tv_projection_is_sanitised` |
| p.20 | Printable documents (SO, PR, PO, GRN, COA, pick list) | Partial | HTML documents printed to PDF by the browser; quotation/CAPA response not templated | — |
| p.27–28 | Separate named accounts, MFA, device trust, factory-network-only, fail closed | Built | `core/auth_views.py`, `authentication.py`, `zero_trust.py`, `gateway/` | `ZeroTrustTests`, gateway tests |
| p.29–30 | IML process/setup recipes, telemetry, OEE, energy | **Open** | Machine telemetry (EUROMAP 77) not connected; setup readings recorded via inspections/tasks | — |
| p.37–45 | Visitor book, staff movement, vehicles, material in/out, returnable passes, couriers, evidence, muster | Built | `gate/` | `GateTests` |
| p.48–57 | Tasks with proof rounds; PM plans (8 intervals); breakdown workflow; reminders; incidents | Built | `tasks/` | `TaskTests` |
| p.50 | WhatsApp / email | Partial | Approval-gated outbox drafts with dedupe and supersede; no provider connected | — |
| p.62–63 | Org hierarchy, delegation, PostgreSQL, durable worker, backups | Built / unverified | `UserRole` scope + expiry, compose, `run_worker`, `backup.sh` | Load test and restore drill not yet run |
| p.65 | Family moulds, changeover matrix, regrind | **Open** | Single component per mould today; regrind is an item kind and lot state only | — |
