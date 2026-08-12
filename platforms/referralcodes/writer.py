"""ReferralCodes.com — official Agent Import (preferred over browser).

WRITE_PREPARED / CANARY_READY: validated JSON/CSV payload ready for
  https://referralcodes.com/profile/import/agent
WRITE_VERIFIED: after authenticated Validate+Commit + public post_match.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.phase import content_write_allowed, phase_name
from platforms.referralcodes.agent_import import (
    DOCS_URL,
    IMPORT_UI,
    SCHEMA_VERSION,
    build_import_payload,
    validate_payload,
    write_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]


def dry_run_report(program: str | None = "kraken") -> dict[str, Any]:
    programs = [program] if program else None
    payload, meta = build_import_payload(programs)
    validation = validate_payload(payload)
    stem = f"referralcodes-agent-import-{program or 'all'}"
    paths = write_artifacts(payload, meta, stem=stem)
    out = {
        "platform": "referralcodes",
        "method": "official_agent_import",
        "prefer": ["Agent Import JSON/CSV", "future API", "no browser CAPTCHA bypass"],
        "docs": DOCS_URL,
        "import_ui": IMPORT_UI,
        "schema_version": SCHEMA_VERSION,
        "write_mode": "CANARY_READY" if validation.ok else "WRITE_PREPARED",
        "live": False,
        "content_write_allowed": content_write_allowed("referralcodes"),
        "phase": phase_name(),
        "validation_ok": validation.ok,
        "validation_errors": validation.errors,
        "payload": payload,
        "programs": meta,
        "pending_updates": sum(1 for m in meta if m.get("status") == "ok"),
        "artifacts": paths,
        "blocker_to_write_verified": (
            None
            if validation.ok
            else "Payload failed schema validation — fix offers/mappings before canary import"
        )
        or (
            "Live step remaining: login REFERRALCODES_* → open import UI → Validate → "
            "Commit one canary item → reread profile post_match"
        ),
        "canary_steps": [
            "python tools/prepare_referralcodes_agent_import.py --program kraken",
            f"Open {IMPORT_UI} (authenticated)",
            "Paste JSON → Validate → read #agent-import-result",
            "Commit if ok → reread public profile",
            "mark_write_verified only with post_match evidence",
        ],
    }
    path = ROOT / "data" / "captures" / "referralcodes-official-dry-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def build_write_plan(program: str = "kraken", language: str = "en") -> dict[str, Any]:
    """Compatibility shim for activation_canary generic path."""
    report = dry_run_report(program)
    return type(
        "Plan",
        (),
        {
            "platform": "referralcodes",
            "program": program,
            "language": language,
            "structure_preserved": report.get("validation_ok"),
            "changed_fields": {
                m["program"]: m.get("item")
                for m in report.get("programs") or []
                if m.get("status") == "ok"
            },
            "announcement_url": None,
            "edit_url": IMPORT_UI,
        },
    )()


def execute_write(*_a, **_k) -> dict[str, Any]:
    """Live import not auto-committed here — prepare payload only."""
    plan = dry_run_report()
    return {
        "ok": False,
        "write_mode": plan.get("write_mode"),
        "error": "referralcodes_agent_import_prepare_only_use_import_ui_for_commit",
        "import_ui": IMPORT_UI,
        "plan": plan,
        "note": (
            "Autofresh prepares + validates the official Agent Import payload. "
            "Commit stays operator-gated on /profile/import/agent until browser "
            "automation of Validate/Commit is proven with secrets."
        ),
    }
