# Design-book validation

This document checks the current application against two documents dated 16 Oct 2026: the *Kantam App Visual Design Book* (43 pages) and the *Workflow and Section Specification* (51 pages). Both use the same screen IDs, UI-01 to UI-37.

The six photographs on visual-book page 2 are screenshots of **this** application (`localhost:8443/r/...`), so the books are a redesign proposal for it.

Status key:
- **Built**: the screen's workflow and server rules exist and are covered by tests. The layout may still differ from the book.
- **Partial**: the core records exist, but named parts of the spec are missing.
- **Missing**: there is no model or screen for it.

## 1. Summary

| Result | Screens |
|---|---|
| Built (15) | 01, 04, 09, 10, 12, 13, 14, 15, 20, 21, 22, 24, 27, 28, 37 |
| Partial (14) | 02, 03, 05, 06, 07, 08, 11, 16, 19, 23, 25, 26, 34, 35 |
| Missing (8) | 17, 18, 29, 30, 31, 32, 33, 36 |

The core order chain is in place, along with its server rules: SO → PR → issue/return → output → QC → packing → dispatch → gate. Every row of the books' "connected source chain" works at **lot** level.

The books add three things:
1. A different navigation and visual system.
2. A **unique physical box** identity, with labels, scan/weigh and box-level loading.
3. New modules: tool room, people/skills, costing, AI MIS and POWER marketing.

The books themselves call items 2 and 3 "pending scope".

## 2. Conflicts and issues to resolve before building

| # | Issue | Recommendation |
|---|---|---|
| C1 | The workflow book's baseline is the **96-page brief v6**. The app was built from the **76-page brief v5.0** we were given. | Supply v6. Any requirement added between v5 and v6 is not checked here. |
| C2 | **Colour.** The book uses navy navigation (#142A3A), teal primary actions (#006D77), and red (#B42336) only for brand and critical hold. The app currently uses Kantam logo red (#E31F26) as the primary colour, as requested on 16 Oct. | Add the book palette as a selectable theme preset (the theme system already supports presets). Make it the default only if Kantam approves. Using red for every primary button conflicts with red meaning "critical hold". |
| C3 | **Navigation.** The book groups the menu as Personal work / Factory flow / Role-scoped support, with rare admin pages under More. The app lists child records in the sidebar (SO lines, recipe lines, PO lines, GRN lines, plan lines, PR materials, quotation lines, dispatch lines, contacts, unit conversions). | These children already show as tabs inside their parent records. Hide them from the sidebar and regroup the menu. |
| C4 | Owner overview: the spec says "six decision-relevant measures", but the visual shows four cards. | Confirm the six measures. |
| C5 | No hardware is identified: Zepto printer model, scanner, scale protocol. UI-17, UI-18 and UI-25 can't be accepted without the actual devices. | Send model numbers and a sample of the 4×4-inch label stock. |
| C6 | UI-07 and UI-32 depend on a WhatsApp provider and an AI endpoint, and both are data-egress decisions. | These are owner decisions. They are not covered by the current zero-trust setup, which has no outbound internet. |

## 3. Screen-by-screen

| ID | Screen | Status | What exists | Gaps against the books |
|---|---|---|---|---|
| UI-01 | Sign in | Built | Named accounts, device enrolment, generic failure message, MFA, no shared password, paste allowed | Language picker on the sign-in screen (the user has `preferred_language`, but there are no UI translations) |
| UI-02 | My work today | Partial | Home shows my tasks and "waiting for me" | Tabs Today / Overdue / Waiting review / Blocked / Verified with counters; "Open next action"; filters kept on Back |
| UI-03 | Owner overview | Partial | Cards, freshness stamp, financial cards marked "not connected", pending approvals | Tabs Delivery / Quality / Cost-cash / People; each card opening its contributing records; plan-vs-accepted chart; exception list with "Open source" |
| UI-04 | Plant / machine board | Built | Machine, PR, good/held/reject, freshness, operator-entered values labelled | Shift readiness, downtime and handover as tabs on the board (they currently live in separate menus) |
| UI-05 | Follow-up Centre | Partial | Tasks follow Assigned→Accepted→In progress/Blocked→Submitted→Verified/Reopened/Cancelled, with source link, reviewer and blocker; CRM activities | One combined centre; **separate original and current due dates, with an approved due-date revision** (the task has one `due_at`); a WhatsApp state column; escalation chain and quiet hours |
| UI-06 | Follow-up detail | Partial | Proof rounds, no self-verification, reopening needs a reason, audit history | Activity timeline that merges events, evidence and messages; "Request help" |
| UI-07 | Notifications / outbox | Partial | Outbox with Draft / Approved / Queued / Provider accepted / Delivered / Failed / Cancelled; approval-gated; in-app notifications deduplicated | Missing states: **Approval needed, Read, Suppressed**; a unique key of source + version + event + recipient + channel; **invalidating drafts when the source is amended**; signed provider callbacks; retry/dead-letter. No provider is connected (C6) |
| UI-08 | CRM pipeline / account | Partial | Leads with stages, conversion, one customer record, duplicate customer code blocked, own-lead scope | Pipeline board view; **POWER source (P/O/W/E/R) as a fixed choice** (it is currently free text); duplicate *suggestions* on contact and company name |
| UI-09 | Samples / quotations | Built | Samples, quotation lines and slabs, submit/approve/send/revise, margin exception | An explicit **tax-inclusive / tax-exclusive** flag on prices |
| UI-10 | SO and component demand | Built | 24-hour lock, amendments with old/new/reason/approval, versions, component demand, sets not double-counted, order PDF | Message tab (needs the outbox work in UI-07) |
| UI-11 | Product / master workspace | Partial | All masters exist; deactivation instead of deletion; dependent selections checked on the server | Grouped master tiles with Products / Partners / Manufacturing / Locations / Configuration tabs; child masters taken out of the sidebar (C3) |
| UI-12 | Artwork / label readiness | Built | Revisions, approval steps, obsolete revisions blocked, label lots | Final v14 label-store layout (data from Kantam is still pending) |
| UI-13 | Monthly capacity / reforecast | Built | Baseline kept, revisions, cycle reforecast (8 s→10 s test), planning board | "Prepare next month" around day 20 as an explicit action; overbooking check for robots (machine and mould are checked) |
| UI-14 | Shift PR / readiness | Built | 12-hour shift PR, materials, release gates, cancel→return→linked reissue (tested) | — |
| UI-15 | Shift production | Built | Good/held/reject, downtime, labour, closed shift locked, controlled correction (maker-checker) | — |
| UI-16 | Stores / SFG / FG | Partial | Stock on hand, holds, reservations with row locks, counts, set readiness | Availability **by box** (needs UI-17) |
| UI-17 | Box birth / QR / 4×4 label | **Missing** | — | Box entity with an immutable ID, serial series reserved atomically (voided numbers never reused), snapshot at print time, print queue, reprint only with a reason, QR/barcode for the same key |
| UI-18 | Scan and weigh | **Missing** | — | Scale capture with stable flag and calibration, approved tare, net/expected/variance per product tolerance, quarantine. Formulas are on workflow page 47 |
| UI-19 | Box trace / movement | Partial | Lot genealogy and traceability, append-only stock ledger, reversals link back | Box-level timeline, repack parent/child, custodian acknowledgement |
| UI-20 | Purchase desk | Built | PO lines and follow-up, shortages, short-close, PO approval (₹5 lakh owner co-sign) | Keeping the **original and revised supplier promise** (the PO line has one `promised_date`) |
| UI-21 | GRN / incoming QC | Built | Gate arrival, GRN, QC decision, 500 declared / 490 received / 20 held → 470 (tested) | **Flagging a duplicate supplier invoice number** |
| UI-22 | QC inspection | Built | Inspection decision, hold blocks shipment, release only by authorised role | Product-specific test-limit sheet (currently a free-form checklist) |
| UI-23 | Complaints / CAPA | Partial | Complaint stages, link to dispatch and lots, containment / root cause / CA / PA / effectiveness text | CAPA actions as owned tasks with an independent effectiveness reviewer; recall-scope query by box; old evidence kept when a CAPA is reopened |
| UI-24 | Packing / matching | Built | Consumes components and creates sets in one transaction; 400+400 → 400 sets; unpack | Box labels tab (UI-17) |
| UI-25 | Dispatch scan / loading | Partial | Pick, verify, approve, post once, QC hold blocks | **Per-box scan**; separate staged / loaded / unloaded / gate-exited states; duplicate scan ignored |
| UI-26 | Manifest / invoice / POD | Partial | Dispatch post / deliver / exception, gate clearance | Manifest by box ID; invoice link (the accounting system is undecided); POD reminder |
| UI-27 | Gate / visitors | Built | Visitors, host approval, photos, server timestamps, vehicles, material, couriers, passes | — |
| UI-28 | Maintenance / PM | Built | Assets, PM plans (marked pending OEM approval), breakdowns, task proof, verified restart | Spares request linked to stores |
| UI-29 | Tool room | **Missing** | Mould master only | Tool jobs, trials, engineering changes, shot counter |
| UI-30 | People / skills | **Missing** | People directory, labour allocation | Skills, training, practical assessments, private HR reviews |
| UI-31 | Cost / accounts | **Missing** | Cost fields protected by field-level permissions; standard-cost changes need maker-checker | Costing screen (standard vs actual), receivables/payables (needs the accounting system) |
| UI-32 | AI MIS | **Missing** | — | Pending scope in the books; needs a data-egress decision (C6) |
| UI-33 | POWER marketing | **Missing** | Lead source as free text | Campaigns, partners, outreach, attribution |
| UI-34 | Approvals / delegation | Partial | Maker-checker inbox, current-vs-proposed diff, stale detection, distinct checkers, expiry, time-boxed role delegation | **Delegated** and **Authority limits** tabs; "Review next approval" button |
| UI-35 | Admin / devices / audit | Partial | Users, roles, devices, sessions, hash-chained audit, policies | Integrations, backup and restore health panels; access-review queue |
| UI-36 | Mobile operator / guard | **Missing** | Responsive layout only | Dedicated stacked screens with 48 px targets, bottom tab bar, explicit offline "pending" state |
| UI-37 | TV display | Built | Sanitised aggregate projection, freshness stamp, other APIs denied (tested) | Rotating panels |

## 4. Cross-cutting rules (workflow book pages 41–50)

| Rule | Status |
|---|---|
| One primary action per screen (business verb + object) | Partial: actions come from metadata but are shown with equal weight. Needs a `primary` flag per resource and state |
| Destructive actions name the document, units and consequence, and need a reason | Built: reason and confirmation on cancel, reverse, close shift, dispose |
| Words on status badges, not colour alone | Built |
| Tables: search, sort, filter, pagination | Built. Column chooser and frozen identifier column are missing |
| Keep typed values on validation errors | Built. Focus moving to the first invalid field is missing |
| Empty, stale, conflict, offline and no-permission states | Partial: conflict (row version), no-permission and stale timestamps exist. Offline "pending" state is missing |
| Shifts 09:00–21:00 / 21:00–09:00 IST; machine count from the register, never hard-coded | Built |
| Server owns IDs, timestamps, totals and audit | Built |
| Scope enforced on every route, export and photo | Built (tested) |
| 50-user load test, restore drill, device QA | Not done (needs factory environment) |

## 5. Quick fixes applied (16 Oct 2026)

| Gap | Change | Test |
|---|---|---|
| C2 colour | New theme preset **Design book (navy & teal)**: navy sidebar #142A3A, teal actions #006D77, #F3F6F8 workspace, #B42336 for critical/hold. Kantam red stays the default until Kantam chooses | build |
| C3 / UI-11 menu | Sidebar regrouped into Personal work / Boards / Factory flow / Role-scoped support. Only the current department is expanded. Child records (SO lines and versions, recipe lines, contacts, unit conversions, product components, quotation lines, GRN lines, plan revisions, PR materials, dispatch lines, gate material lines) are hidden from the menu but still open as parent tabs and by direct link | `NavigationMetaTests` |
| UI-02 My work | Today / Overdue / Waiting review / Blocked / Verified tabs with counts. "Open next action" and "New follow-up". The tab is kept in the URL, so Back returns to it | build |
| UI-05 / p.43 due dates | Tasks keep the **original due date**. The due date can't be edited directly; "Revise due date" needs a reason and a checker (new maker-checker policy). Revisions are logged, and lateness is measured against the original | `FollowUpDueDateTests` |
| UI-20 supplier promise | PO lines keep the **original supplier promise** (frozen when the PO leaves draft), plus a revision history with reasons and the slip in days | `PurchasePromiseTests` |
| UI-07 outbox | New states: Draft – approval needed, **Read**, **Suppressed**. Editing a source document (or its lines) suppresses unsent drafts. Provider callbacks can only move a message forward (duplicates and out-of-order callbacks are ignored, and Delivered never drops back to Failed) | `OutboxStateTests` |
| UI-08 POWER source | Leads need an **original POWER source** (Paid / Organic / Word of mouth / External partner / Reach out), fixed after creation. The old free-text source becomes "Source detail", and existing leads were mapped where the text was clear | `CommercialDataTests` |
| UI-09 tax basis | Quotations state **prices exclude / include GST** and the GST rate. Both are copied to revisions and the sent snapshot | `CommercialDataTests` |
| UI-21 duplicate invoice | A supplier invoice number already used on another GRN (matched ignoring case, punctuation and leading zeros) is refused unless a reason is given. Accepted reuse is linked to the first GRN and accounts are notified | `CommercialDataTests` |

Screens UI-02, UI-07 and UI-11 now meet the parts of the spec listed above. The box, hardware and new-module gaps remain.

## 6. Suggested build order (follows the books' P0–P5)

1. **P0 navigation:** book theme preset (C2), regrouped menu with child records hidden (C3), primary-action flag, My work tabs (UI-02), Delegated / Authority limits tabs (UI-34).
2. **P1–P2 data rules:** original vs current due dates (tasks, PO lines), POWER source choice, tax-inclusive flag, duplicate supplier invoice flag, full outbox state model with source-version invalidation (UI-07).
3. **P3 boxes:** box entity, serials, label printing, scan/weigh, box loading and manifest (UI-16 to UI-19, UI-25, UI-26). This needs the hardware details first (C5).
4. **P4–P5 modules:** mobile layouts, tool room, people/skills, costing, then AI MIS and POWER after the owner's decisions.
