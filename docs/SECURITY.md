# Security design: OWASP alignment, RBAC and maker / checker

This document is for the developers and the IT/security reviewers of the Kantam ERP. It maps each control to the **OWASP Top 10 (2021)** and the relevant **OWASP ASVS** areas, and points to the code and the automated test that proves it.

## 1. OWASP Top 10 mapping

| OWASP | Control in this application | Code | Test evidence |
|---|---|---|---|
| **A01 Broken access control** | Deny by default: a view with no declared permission is refused. Every list, detail, action, export, attachment and history call checks permission **and** row scope on the server | `core/rbac.py` `DenyByDefault`, `core/registry.py`, `core/resource_api.py` | `RbacTests` (guessed IDs, exports, search, direct photo URLs) |
| | Field-level projection: prices, supplier rates, costs and personal data are removed from responses, metadata, history and exports | `Resource.field_permissions` | `test_price_hidden_from_planner_projection`, `test_supplier_rates_hidden_from_store` |
| | References are re-scoped: you can't link a record you can't see | `check_fk_access`, `scoped_get` | `test_cannot_reference_hidden_records_in_writes` |
| | No privilege escalation: no self-grant; you can't grant permissions you don't hold; only an owner can act on owner accounts | `core/resources.py` `_guard_target` | `test_self_role_grant_blocked`, `test_it_admin_cannot_take_over_owner_account` |
| | Maker / checker on sensitive operations (section 3) | `core/maker_checker.py` | `MakerCheckerTests` |
| **A02 Cryptographic failures** | Argon2id passwords; Ed25519-signed access tokens (gateway verifies, can't mint); HMAC-SHA256 gateway→backend signing; refresh and device secrets stored only as SHA-256; TLS ≥ 1.2 at the gateway; encrypted backups | `core/security.py`, `gateway/src/policy.js`, `deploy/backup.sh` | `ZeroTrustTests`, gateway tests |
| **A03 Injection** | ORM only (no raw SQL); filter, sort and search fields are allow-listed per resource; HTML documents escape every value; CSV export neutralises spreadsheet formulas; uploads validated by magic bytes and re-encoded | `resource_api.py` `csv_safe`, `documents.py`, `attachments.py` | `test_csv_export_neutralises_formulas`, `test_rejects_non_image_upload…` |
| **A04 Insecure design** | Workflow states enforced on the server; immutable posted records; idempotency keys; row locks against double allocation; approved details frozen | services in each app | `test_two_users_cannot_allocate_same_stock`, `test_approved_visit_details_locked` |
| **A05 Security misconfiguration** | `DEBUG` off by default; secrets are required; strict CSP, HSTS, `nosniff`, `frame-ancestors 'none'`, `no-store`; no CORS; cross-site requests refused; Django admin not installed; containers read-only, non-root, no capabilities | `settings.py`, `zero_trust.py`, `gateway/src/app.js`, `deploy/docker-compose.yml` | `test_no_store_cache_headers`, gateway header tests |
| **A06 Vulnerable components** | All dependencies pinned. Audited with `pip-audit` and `npm audit`: **0 known vulnerabilities** at the time of writing. The gateway proxy uses only `node:http` (this removed a vulnerable glob dependency chain) | `requirements.txt`, `package.json` | see "Re-running the audits" |
| **A07 Identification & authentication failures** | Named accounts only; 12+ character passwords with common/numeric/similarity checks; lockout after 5 failures; **generic response for locked, unknown or wrong-password logins** (no username enumeration); TOTP MFA for owner/admin/manager; **temporary passwords must be changed before use**; password reuse refused; device-bound sessions; idle and absolute timeouts; refresh-token rotation with reuse detection; permission changes invalidate tokens | `core/auth_views.py`, `core/authentication.py` | `ZeroTrustTests`, `test_temporary_password_must_be_changed_first` |
| **A08 Software & data integrity** | Hash-chained audit log with `verify_chain()`; maker/checker execution re-checks the record version | `core/audit.py`, `maker_checker.py` | `test_audit_chain_detects_tampering`, `test_field_conditional_policy_and_stale_detection` |
| **A09 Logging & monitoring** | Audit trail for every change, export, login and evidence view. `kantam.security` log for **every access denial**. Gateway JSON access log (never logs tokens). Login attempt table | `rbac.py`, `audit.py`, `gateway/src/app.js` | `test_access_denials_are_logged` |
| **A10 SSRF** | The server never fetches user-supplied URLs. Outbound messaging (WhatsApp only; no email addresses are stored) is approval-gated drafts only | `core/services.py` `draft_outbox` | — |

ASVS areas covered: V2 authentication, V3 session management, V4 access control, V5 validation and encoding, V7 logging, V8 data protection (`no-store`, field projection), V12 files, V13 API, V14 configuration.

## 2. RBAC on backend **and** frontend

**Backend (the actual control).** Each role is a set of permission codes, defined in `rbac_matrix.py` and generated into `docs/permission-matrix.md`. Grants can be scoped to a department and given an expiry (for delegation). The effective permission set is recomputed on every request, and a role-version stamp in each token makes changes apply on the next call.

**Frontend (usability only, never trusted).**
- The menu and workspaces come from `/api/meta`, which the server has already filtered for the current user.
- Create, Edit and action buttons appear only when the server reports them as allowed for that record (`_meta.actions`, `can_create`, `can_update`).
- Board routes are wrapped in `RequirePerm` and show "Not available for your role".
- Hidden fields never reach the browser: the server removes them.
- Controlled actions show an **approval** badge and the rule that applies, read from `approval_rules` in the metadata.
- Accounts with a temporary password see only the password-change screen.

If someone bypasses the UI, for example with browser developer tools or by calling the API directly, the server still refuses the request.

## 3. Maker / checker (four-eyes) control

### How it works

1. The **maker** submits a create, edit or workflow action as usual.
2. If an **approval policy** matches, the server first runs the operation as a **dry run** inside a database savepoint. Every business rule and permission is validated immediately, and the exact proposed change is captured. The dry run is then rolled back.
3. The request is stored and the API answers `202 Pending approval`. **Nothing changes yet.** Authorised checkers are notified.
4. A **checker** reviews the request in **Approvals**: the maker, the reason, a current-vs-proposed table and the policy. They approve or reject (a rejection needs a reason).
5. When the required number of approvals is reached, the operation runs **as the maker**, after re-checking that:
   - the maker still holds the permission;
   - the maker can still see the record;
   - the record has not changed since submission. If it has, the request becomes **stale** and must be resubmitted, so nothing is ever applied over newer data.
6. The audit trail records the change under the maker's name with the approval reference. The approval keeps every checker's name, time and note.

### The owner is the super administrator

The owner account is exempt from every maker/checker rule. This covers policies, built-in two-step workflows and "someone else must approve" checks. An owner's change applies immediately and is still recorded in the audit trail under the owner's name. Two protections remain:
- only an owner can change an owner account;
- the owner role can't be limited.

Kantam decided this deliberately (23 Oct 2026). Keep the number of owner accounts small and protect them with MFA.

### Rules that are always enforced (for everyone except the owner)

- The maker can never approve or reject their own request.
- Each checker counts only once, and policies can require 1–3 distinct checkers.
- A checker must hold the policy's checker permission at the moment they decide.
- Requests expire (72 hours by default; the background worker marks them expired and notifies the maker).
- The maker can withdraw a pending request; nobody else can.
- **Policy changes are themselves maker/checker controlled**, so one person cannot silently switch a control off.

### Default policies (editable under Administration → Maker / checker policies)

| Policy | Trigger | Checker permission |
|---|---|---|
| Grant role / Revoke role | always | `security.roles.manage` |
| Change role permissions / Reset role to default | always | `security.roles.manage` |
| Reset user password / Reset MFA | always | `security.users.manage` |
| Create / change approval policy | always | `approvals.policy.manage` |
| Customer credit / terms change | `credit_limit`, `payment_terms`, `advance_required_pct` or `freight_basis` edited | `sales.so.approve_amendment` |
| Supplier terms / GSTIN change | `payment_terms` or `gstin` edited | `purchase.po.approve` |
| Item standard cost change | `standard_cost` edited | `commercial.cost.view` |
| Recipe approval | always | `masters.recipe.approve` |
| Stock movement reversal | always | `inventory.adjust.approve` |
| Closed-shift production correction | always | `production.correct_closed` |
| Cancel sales order / Cancel purchase order | always | `sales.so.approve_amendment` / `purchase.po.approve` |
| High-value PO approval (owner co-sign) | PO value > ₹5,00,000 | `dashboard.owner.view` |
| Gate pass disposition | always | `gate.gatepass.approve` |
| Task due-date revision (original due date always kept) | always | `tasks.verify` |

Built-in two-step workflows (post-lock SO amendment, quotation margin exception, additional material issue, stock count variance) use the same queue and the same rules. So do the in-workflow separations: a PO creator can't approve their own PO, a guard can't approve their own visitor, and a task's assignee can't verify their own work.

> **Plan the approver roster.** Every policy needs at least two people who can act: one maker and a different checker. For example, with a single owner and no IT admin, nobody can approve policy changes. Assign at least one `it_admin` alongside the owner.

## 4. Re-running the audits

```bash
pip install pip-audit && pip-audit -r backend/requirements.txt
cd gateway  && npm audit --omit=dev
cd frontend && npm audit
```

Run these in CI and before each release. A finding in a dependency counts as a release blocker under gate G5 (brief p.33).

## 5. Not covered by the code (needs operational controls)

- Penetration test and DAST scan against the installed factory server.
- Managed endpoints: USB/screen-capture policy and patching (brief p.28).
- Periodic access reviews: role grants and delegations should be reviewed monthly from the Users screen.
- Central log shipping and alerting on `kantam.security` warnings and `refresh_reuse_detected` audit events.
