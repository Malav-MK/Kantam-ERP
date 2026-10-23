"""
Plain-language permission grid for the role editor.

Every permission code is shown either as a tick box in a record-type row
(View / Add / Edit / Delete / Approve / Export) or, when it is not tied to one
record type (price visibility, dashboards, security administration ...), in the
"Special permissions" list with its description. The grid is a presentation of
the same permission codes the server enforces; nothing here grants access.
"""
from .registry import MODULES, REGISTRY

COLUMNS = [
    ("view", "View"), ("add", "Add"), ("edit", "Edit"), ("delete", "Delete"), ("approve", "Approve"), ("export", "Export"),
]
# Workflow actions that cancel, reverse or retire a record. The ERP never hard-deletes posted records,
# so "Delete" means these controlled undo / retire steps.
DELETE_ACTIONS = {"cancel", "cancel_balance", "reverse", "obsolete", "short_close", "dispose", "revoke", "deactivate", "unpack", "withdraw"}
# Decisions made by a person with authority over someone else's work.
APPROVE_ACTIONS = {"approve", "reject", "verify", "authorise", "commercial_approve", "credit_release", "record_advance", "decide",
                   "qc_decide", "reopen", "correct"}
EXTRA_MODULE_LABELS = {
    "commercial": "Prices & costs", "finance": "Finance", "hr": "People / HR", "security": "Security administration",
    "dashboard": "Dashboards", "approvals": "Approvals",
}


def _category(action_name, code):
    if action_name in APPROVE_ACTIONS or code.endswith(".approve") or ".approve_" in code:
        return "approve"
    if action_name in DELETE_ACTIONS:
        return "delete"
    return "edit"


def build_matrix():
    from .models import Permission

    descriptions = dict(Permission.objects.values_list("code", "description"))
    module_of = dict(Permission.objects.values_list("code", "module"))
    # Codes that are the plain view/add/edit/export permission of some record type.
    base = {}
    for res in REGISTRY.values():
        verbs = ["view", "export"] + (["create"] if res.allow_create else []) + (["update"] if res.allow_update else [])
        for verb in verbs:
            base.setdefault(res.perm(verb), (res.key, verb))
    # Action-only codes get one column for every row they appear in: approve beats edit beats delete.
    rank = {"approve": 3, "edit": 2, "delete": 1}
    action_cat = {}
    for res in REGISTRY.values():
        for a in res.actions:
            code = a.perm(res.key)
            if code in base:
                continue
            cat = _category(a.name, code)
            if rank[cat] > rank.get(action_cat.get(code), 0):
                action_cat[code] = cat

    labels = dict(MODULES)
    modules, covered = [], set()
    for mod_code, mod_label in MODULES:
        rows = []
        for res in REGISTRY.values():
            if res.module != mod_code:
                continue
            cells = {c: [] for c, _ in COLUMNS}
            cells["view"].append(res.perm("view"))
            cells["export"].append(res.perm("export"))
            if res.allow_create:
                cells["add"].append(res.perm("create"))
            if res.allow_update:
                cells["edit"].append(res.perm("update"))
            for a in res.actions:
                code = a.perm(res.key)
                if code in action_cat and code not in cells[action_cat[code]]:
                    cells[action_cat[code]].append(code)
            cells = {k: [c for c in v if c in descriptions] for k, v in cells.items()}
            for v in cells.values():
                covered.update(v)
            rows.append({
                "key": res.key, "label": res.label,
                "cells": {k: [{"code": c, "description": descriptions[c]} for c in v] for k, v in cells.items()},
            })
        if rows:
            modules.append({"key": mod_code, "label": mod_label, "rows": rows})

    special = {}
    for code, desc in sorted(descriptions.items()):
        if code in covered:
            continue
        module = module_of.get(code) or "other"
        special.setdefault(module, []).append({"code": code, "description": desc})
    special_groups = [
        {"key": m, "label": labels.get(m) or EXTRA_MODULE_LABELS.get(m) or m.title(), "permissions": perms}
        for m, perms in sorted(special.items(), key=lambda kv: (labels.get(kv[0]) or EXTRA_MODULE_LABELS.get(kv[0]) or kv[0]))
    ]
    return {
        "columns": [{"key": k, "label": label} for k, label in COLUMNS],
        "modules": modules,
        "special": special_groups,
        "help": {
            "view": "See the list and open records.",
            "add": "Create new records.",
            "edit": "Change records and carry out their normal workflow steps (submit, release, post ...).",
            "delete": "Cancel, reverse or retire records. Posted records are never erased; they are cancelled with a reason.",
            "approve": "Approve, reject or verify other people's work.",
            "export": "Download the list as a CSV file.",
        },
    }
