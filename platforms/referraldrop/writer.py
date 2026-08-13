"""ReferralDrop — public read + AUTH_BLOCKED_MANUAL. No official OAuth."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.renderer import MappingRepository

ROOT = Path(__file__).resolve().parents[2]


def dry_run_report() -> dict[str, Any]:
    mapped = [r for r in list_mapping_refs() if r.platform == "referraldrop"]
    items = []
    repo = MappingRepository()
    for ref in mapped:
        try:
            m = repo.load(ref.platform, ref.program, ref.language)
            items.append(
                {
                    "program": ref.program,
                    "announcement_url": m.announcement_url,
                    "quality": getattr(m, "notes", None),
                    "action": "MANUAL_WRITE_ONLY",
                }
            )
        except Exception as exc:  # noqa: BLE001
            items.append({"program": ref.program, "error": str(exc)})

    out = {
        "platform": "referraldrop",
        "auth_status": "AUTH_BLOCKED_MANUAL",
        "read": "READ_PUBLIC_OK",
        "write_mode": "MANUAL_WRITE",
        "live": False,
        "notes": [
            "No official partner OAuth and no public write API.",
            "llms.txt: account/admin/API routes are not documented and not for public indexing.",
            "Login is email/password + optional 'continue with' social — not automatable OAuth.",
            "Durable classification: AUTH_BLOCKED_MANUAL. No bypass.",
        ],
        "programs": items,
    }
    path = ROOT / "data" / "captures" / "referraldrop-status.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
