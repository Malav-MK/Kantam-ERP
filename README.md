# Kantam ERP + CRM

Integrated plant ERP and CRM for **Kantam Kontainers LLP** (IML packaging, Bareja), built against the
*ERP + CRM designer brief v5.0*. It is one connected system for sales, artwork, planning, stores,
purchase, production, quality, packing/dispatch, gate security, tasks/maintenance and owner control.
Each transaction is entered once and its effect carries through the organisation.

```
 plant LAN (approved CIDRs only)
        │  HTTPS
 ┌──────▼────────────────────────────┐
 │ gateway/  Node.js (Express)       │  Zero-trust policy enforcement point
 │  • factory-network allowlist      │  • serves the React UI (frontend/)
 │  • EdDSA token + device binding   │  • rate limits, CSP/HSTS, no CORS
 │  • HMAC-signs every request ──────┼──┐
 └───────────────────────────────────┘  │ internal network only
 ┌──────────────────────────────────────▼──┐     ┌──────────────┐
 │ backend/  Django 5 + DRF                │────▶│ PostgreSQL 16 │
 │  • RBAC deny-by-default, row + field    │     └──────────────┘
 │    scoping on every read/write/export   │     ┌──────────────┐
 │  • atomic stock ledger, workflows       │◀────│ worker       │ PM generation, reminders,
 │  • hash-chained audit, evidence store   │     │ (run_worker) │ shift auto-close
 └─────────────────────────────────────────┘     └──────────────┘
```

| Folder | What it is |
|---|---|
| `backend/` | Django apps: `core` (identity, RBAC, zero trust, audit, registry), `masters`, `crm`, `sales`, `planning`, `production`, `inventory`, `purchase`, `quality`, `fg`, `gate`, `tasks`, `dashboards` |
| `gateway/` | Node zero-trust gateway (also serves the built UI) |
| `frontend/` | React 18 + TypeScript (Vite). Workspaces render from server metadata |
| `deploy/` | Windows one-script installer (`deploy/windows`), Docker Compose for Linux servers, encrypted backup / restore scripts |
| `docs/` | Permission matrix, requirements traceability, [SECURITY.md](docs/SECURITY.md) (OWASP mapping, RBAC, maker/checker) |

## How "dynamic" works

Every record type is declared once in a `resources.py` (`apps/core/registry.py`). From that one declaration the system:

- generates the permission codes (`<module>.<record>.view/create/update/export/<action>`);
- serves `/api/meta`, which describes the fields, filters, workflow actions, dependent selections and help text, filtered to what **this user** may see;
- exposes generic list, detail, create, edit, action, history, attachment and export endpoints, with row scope and field projections applied every time.

The React app builds its menus, tables, forms and action dialogs from that metadata. To add a new register or workflow, you add a model and a resource; you don't hand-write any new screens. Dedicated screens exist only where a board is clearer: the owner overview, machine board, TV view, planning board, stores shift screen, shortages, gate/muster, traceability and audit.

## Zero-trust security model

Nothing is trusted because of where it sits on the network. Each request is verified from scratch.

| Control | Where | Brief |
|---|---|---|
| Factory-network allowlist, fail closed (mobile data / guest Wi-Fi / outside refused) | gateway **and** Django | p.28 |
| Device enrolment: one-time code → httpOnly device credential; sessions and tokens are bound to the device | Django, gateway | p.28 |
| Named accounts, Argon2 hashing, lockout after failed logins, no default passwords | Django | p.27 |
| TOTP MFA required for owner, admin and manager levels | Django | p.27 |
| 10-minute EdDSA access tokens. Only Django holds the signing key; the gateway can verify but never mint tokens | both | p.27 |
| Rotating refresh cookie. If an old refresh token is replayed, the whole session family is revoked | Django | — |
| Every request re-checks the session (revocation, idle/absolute timeout), device, user state and **role version**, so a permission change applies on the next call | Django | p.27 |
| Micro-segmentation: Django rejects any request not HMAC-signed by the gateway (timestamped, client IP bound) and has no published port | both | p.28 |
| RBAC deny-by-default on every endpoint, export, attachment and action. Row scope (e.g. sales execs see own leads; hosts see own visitors). Field projection (prices, supplier rates, personal data) | Django | p.27, p.41 |
| Maker/checker: configurable approval policies hold sensitive creates/edits/actions until 1–3 *different* authorised checkers approve; re-validated at execution; stale changes refused. See [SECURITY.md](docs/SECURITY.md) | Django + UI | p.19, p.62 |
| Hash-chained audit log with `verify_chain()`; no hard deletes of posted records | Django | p.19 |
| Evidence files: magic-byte validation, EXIF/GPS stripped, SHA-256, random names outside the web root, scope re-checked on every view, locked after closure | Django | p.41 |
| CSP, HSTS, no-store caching, no CORS, cross-site requests refused, rate limits, sanitised TV projection | gateway | p.26, p.28 |
| Idempotency keys on every posting, so a repeated tap or retry never posts twice; row locks stop double allocation | Django | p.4 |

> No app or Wi-Fi rule can guarantee zero leaks: an authorised person can still photograph a screen (brief p.28). These controls reduce exposure; they don't replace device management and access reviews.

## Roles

There are 23 system roles, defined in `backend/apps/core/rbac_matrix.py`. The full matrix is generated into [`docs/permission-matrix.md`](docs/permission-matrix.md).

`owner`, `it_admin`, `gm`, `sales_manager`, `sales_exec`, `artwork`, `planner`, `production_supervisor`, `operator`,
`store_manager`, `store_staff`, `purchase_supervisor`, `purchase_manager`, `qc_manager`, `qc_inspector`, `dispatch`,
`accounts`, `security_guard`, `security_supervisor`, `hr`, `maintenance_manager`, `technician`, `tv_viewer`.

Roles can be scoped to a department and time-boxed (delegation with expiry). You can add custom roles in the admin UI, but you can't grant permissions you don't hold yourself.

## Run it locally (development)

Requirements: Python 3.11+, Node 20+.

```bash
# 1. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export DJANGO_DEBUG=1 ZT_GATEWAY_SECRET=$(openssl rand -hex 32) ZT_COOKIE_SECURE=false
.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap            # prompts for the owner password; prints a device enrolment code
.venv/bin/python manage.py seed_demo            # optional illustrative data + 22 demo users (prompts for their password)
.venv/bin/gunicorn kantam.wsgi -b 127.0.0.1:8000 &
.venv/bin/python manage.py run_worker &         # PM scheduler, reminders, shift auto-close

# 2. Frontend build + gateway (same ZT_GATEWAY_SECRET as the backend)
cd ../frontend && npm ci && npm run build
cd ../gateway && npm ci
BACKEND_URL=http://127.0.0.1:8000 STATIC_DIR=../frontend/dist ZT_GATEWAY_SECRET=$ZT_GATEWAY_SECRET node src/server.js
```

Open <http://localhost:8443>, enter the enrolment code, then sign in. Owner, manager and admin accounts are asked to set up an authenticator app on first sign-in.
For UI work, `npm run dev` in `frontend/` serves on :5173 and proxies `/api` through the gateway.

Useful commands: `create_user <username> --name … --role planner --department PLAN`, `create_device "Gate tablet" --kind gate`,
`sync_rbac` (after editing the matrix), `permission_matrix` (regenerates the doc), `run_worker --once`.

## Deploy on a Windows server (one script)

```powershell
# On the Windows server, as administrator, from the unzipped package:
deploy\windows\Install-Kantam.cmd          # or: powershell -ExecutionPolicy Bypass -File deploy\windows\Install-Kantam.ps1
```

The script:
- installs Python 3.12, Node.js LTS and PostgreSQL 16 with winget if they are missing;
- builds the app and creates the database, secrets and an HTTPS certificate;
- creates the owner account;
- registers auto-start services under the low-privilege `LOCAL SERVICE` account;
- adds a firewall rule for the approved plant networks only.

Re-running it upgrades the app and keeps the data. Day-to-day administration (status, logs, new device, backup, restore, reset) uses `KantamAdmin.ps1`. See [deploy/windows/README-Windows.md](deploy/windows/README-Windows.md).

To build the package zip from a checkout: `python3 deploy/windows/make_package.py`.

## Deploy on the factory server (Docker)

```bash
cd deploy
cp .env.example .env        # fill secrets (openssl rand -hex 32) and the approved plant CIDRs
mkdir tls && cp server.crt server.key tls/   # certificate for the internal hostname
docker compose up -d --build
docker compose exec backend python manage.py bootstrap
```

- Only the gateway publishes a port, and it binds to the plant-LAN interface. The database and Django sit on an internal-only Docker network.
- Keep public port-forwarding and tunnels disabled. Remote access is off by default and would need a separate VPN decision (brief p.28).
- Back up with `deploy/backup.sh` (AES-256 encrypted, database plus evidence and keys). Run a restore drill with `deploy/restore.sh` on a test stack: a backup only counts once a restore has been proven (p.63).

## Tests

```bash
cd backend  && DJANGO_SETTINGS_MODULE=kantam.test_settings .venv/bin/python manage.py test tests   # 93 tests
cd gateway  && npm test                                                                            # 13 tests
cd frontend && npm run build                                                                       # strict TypeScript
```

The backend suite runs the brief's acceptance scenarios through the API as the relevant roles:

- multi-flavour order demand
- 24-hour lock with an approved amendment that keeps version 1
- maker/checker
- wrong label blocked at issue
- issue retried with the same idempotency key
- cancelled PR return plus linked reissue
- output reconciliation
- 8 s → 10 s cycle reforecast that keeps the baseline
- closed shift
- 10,000 containers / 8,000 lids → 5,000 sets packed
- excess lids not covering other flavours
- QC hold blocks shipment
- dispatch posts once
- commercial gate
- competing reservations
- 500 kg declared / 490 received / 20 held → 470 usable
- visitor host approval and evidence
- guard cannot self-approve
- unauthorised outward load
- courier receiver acknowledgement
- partial returnable pass
- task proof rounds
- PM idempotency
- department isolation, guessed IDs, exports, field projection, TV sanitisation, protected photo URLs
- device binding, token theft, refresh-token reuse, idle timeout, role change, network denial and audit tampering

## Status and decisions still open (brief p.21)

**Built:** every module above, end to end, on one ledger.

**Needs Kantam decisions or data before go-live:**

- the authoritative label-store v14 file and the 15 label groups
- the real machine/mould register
- recipe basis and valuation policy
- designated approvers and limits
- the accounting system of record (no Tally/BUSY connector is built; financial cards say "not connected")
- WhatsApp/email provider (outbound messages are approval-gated drafts in the outbox only)
- OEM-approved PM checklists (demo plans are marked as pending OEM approval)
- language labels
- machine telemetry (manual postings only)

**Not yet verified:**

- browser/mobile camera QA
- a 50-user load test on PostgreSQL (tests ran on SQLite)
- factory network/TLS installation
- a restore drill

Treat this as a pilot until gates G1–G7 (brief p.33) are signed off.
