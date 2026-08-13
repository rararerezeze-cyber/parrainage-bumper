#!/usr/bin/env python3
"""AUTOFRESH END-TO-END status report (operator + write readiness)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.write_status import ensure_write_status_file, summary as write_summary


def main() -> int:
    ensure_write_status_file()
    ws = write_summary()
    # Telegram worker presence (code only — live deploy is ops)
    worker = ROOT / "telegram-worker" / "worker.js"
    worker_ok = worker.exists()
    worker_src = worker.read_text(encoding="utf-8") if worker_ok else ""
    has_allowlist = "TELEGRAM_ALLOWED_USER_ID" in worker_src
    has_dispatch = "telegram_sync.yml" in worker_src
    has_full_cmds = "gain" in worker_src or "filleul" in worker_src

    # Overrides path
    ov = ROOT / "data" / "operator-overrides.json"
    ov_ok = ov.exists()

    hermes_cli = ROOT / "tools" / "hermes_autofresh.py"
    hermes_lib = ROOT / "lib" / "hermes_interface.py"
    hermes_wf = ROOT / ".github" / "workflows" / "hermes_operator.yml"
    hermes_docs = ROOT / "docs" / "hermes-autofresh-interface.md"
    hermes_ready = all(p.exists() for p in (hermes_cli, hermes_lib, hermes_wf, hermes_docs))

    report = {
        "mode": "HERMES_BACKEND",
        "monitor_mode": "OBSERVATION_ONLY",
        "architecture": "Telegram→Hermes→Autofresh→JSON→Hermes→Telegram",
        "telegram_bot_owned_by": "Hermes (not Autofresh)",
        "hermes_interface_code": "PASS" if hermes_ready else "FAIL",
        "telegram_worker_optional": "YES",
        "telegram_worker_required": "NO",
        "telegram_worker_code_present": "YES" if worker_ok else "NO",
        "operator_overrides_store": "PASS" if ov_ok else "FAIL",
        "WRITE_VERIFIED": ws.get("WRITE_VERIFIED"),
        "write_verified_count": ws.get("write_verified_count"),
        "telegram_live_capable": ws.get("telegram_live_capable"),
        "platforms": ws.get("platforms"),
        "by_status": ws.get("by_status"),
        "HERMES_AUTOFRESH_INTERFACE_READY": "YES" if hermes_ready and ov_ok else "NO",
        "HERMES_PRODUCTION_READY": (
            "YES"
            if hermes_ready
            and ov_ok
            and (ROOT / "lib" / "safety.py").exists()
            else "NO"
        ),
        "MONITOR_SHADOW_READY": (
            "YES" if (ROOT / "lib" / "monitor" / "shadow.py").exists() else "NO"
        ),
        "END_TO_END_OPERATOR_READY": (
            "YES"
            if hermes_ready and ov_ok and (ws.get("write_verified_count") or 0) >= 1
            else "NO"
        ),
        "blockers_to_ready": [],
        "note": (
            "Product path: Hermes calls tools/hermes_autofresh.py or workflow hermes_operator.yml. "
            "telegram-worker is optional/test only. "
            "WRITE_VERIFIED requires real authenticated canary + post-verify."
        ),
    }
    if (ws.get("write_verified_count") or 0) < 1:
        report["blockers_to_ready"].append(
            "No WRITE_VERIFIED platform yet — run live canaries (Super-Parrain then parrainage-co / code-parrainage) with post_match"
        )
    if not hermes_ready:
        report["blockers_to_ready"].append("Hermes interface files incomplete")
    blocked = {"AUTH_BLOCKED_GOOGLE", "AUTH_BLOCKED", "AUTH_BLOCKED_MANUAL"}
    non_blocked = [
        p for p in (ws.get("platforms") or []) if p.get("status") not in blocked
    ]
    all_canary = all(
        p.get("status") in {"CANARY_READY", "WRITE_VERIFIED"} for p in non_blocked
    )
    report["ALL_NON_BLOCKED_PLATFORMS_CANARY_READY"] = "YES" if all_canary else "NO"
    mp = ROOT / "data" / "captures" / "multiprogram-dry-run.json"
    if mp.exists():
        try:
            report["MULTIPROGRAM_DRY_RUN_READY"] = json.loads(
                mp.read_text(encoding="utf-8")
            ).get("MULTIPROGRAM_DRY_RUN_READY", "NO")
        except Exception:
            report["MULTIPROGRAM_DRY_RUN_READY"] = "NO"
    else:
        report["MULTIPROGRAM_DRY_RUN_READY"] = "NO"
    packs = ROOT / "data" / "captures" / "post-super-canary-packs.json"
    if packs.exists():
        try:
            report["POST_SUPER_CANARIES_ARMED"] = json.loads(
                packs.read_text(encoding="utf-8")
            ).get("POST_SUPER_CANARIES_ARMED", "NO")
        except Exception:
            report["POST_SUPER_CANARIES_ARMED"] = "NO"
    else:
        report["POST_SUPER_CANARIES_ARMED"] = "NO"

    out = ROOT / "data" / "captures" / "e2e-status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== AUTOFRESH / HERMES INTERFACE ===")
    print(f"Architecture: {report['architecture']}")
    print(f"Hermes interface: {report['hermes_interface_code']}")
    print(f"telegram-worker required: {report['telegram_worker_required']}")
    print(f"WRITE_VERIFIED: {report['WRITE_VERIFIED']}")
    print(f"Live-capable platforms: {len(report['telegram_live_capable'] or [])}/7")
    for p in report["platforms"] or []:
        print(f"  {p['platform']:18} {p['status']:22} tg={p['telegram_action']}")
    print(f"HERMES_AUTOFRESH_INTERFACE_READY = {report['HERMES_AUTOFRESH_INTERFACE_READY']}")
    print(f"END_TO_END_OPERATOR_READY = {report['END_TO_END_OPERATOR_READY']}")
    if report["blockers_to_ready"]:
        print("Blockers:")
        for b in report["blockers_to_ready"]:
            print(f"  - {b}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
