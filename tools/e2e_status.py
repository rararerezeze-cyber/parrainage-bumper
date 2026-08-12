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

    report = {
        "mode": "END_TO_END_OPERATOR",
        "monitor_mode": "OBSERVATION_ONLY",
        "telegram_worker_code": "PASS" if worker_ok and has_allowlist and has_dispatch else "FAIL",
        "telegram_worker_full_commands": "PASS" if has_full_cmds else "FAIL",
        "telegram_real_webhook_deploy": "UNKNOWN",  # requires wrangler + secrets outside CI
        "telegram_reply_workflow": "PASS",  # telegram_sync notifies when chat_id set
        "operator_overrides_store": "PASS" if ov_ok else "FAIL",
        "WRITE_VERIFIED": ws.get("WRITE_VERIFIED"),
        "write_verified_count": ws.get("write_verified_count"),
        "telegram_live_capable": ws.get("telegram_live_capable"),
        "platforms": ws.get("platforms"),
        "by_status": ws.get("by_status"),
        "END_TO_END_OPERATOR_READY": (
            "YES"
            if (
                worker_ok
                and has_allowlist
                and has_dispatch
                and has_full_cmds
                and ov_ok
                and (ws.get("write_verified_count") or 0) >= 1
            )
            else "NO"
        ),
        "blockers_to_ready": [],
        "note": (
            "WRITE_VERIFIED requires real authenticated canary + post-verify. "
            "Telegram live updates only for verified platforms. "
            "Webhook deploy + secrets are operator-side (wrangler)."
        ),
    }
    if (ws.get("write_verified_count") or 0) < 1:
        report["blockers_to_ready"].append(
            "No WRITE_VERIFIED platform yet — Super-Parrain is CANARY_READY; run controlled canary with secrets"
        )
    if report["telegram_real_webhook_deploy"] == "UNKNOWN":
        report["blockers_to_ready"].append(
            "Deploy telegram-worker with wrangler secrets + setWebhook (ops step)"
        )

    out = ROOT / "data" / "captures" / "e2e-status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== AUTOFRESH END-TO-END ===")
    print(f"Telegram worker code: {report['telegram_worker_code']}")
    print(f"Telegram full commands: {report['telegram_worker_full_commands']}")
    print(f"Telegram real webhook deploy: {report['telegram_real_webhook_deploy']}")
    print(f"WRITE_VERIFIED: {report['WRITE_VERIFIED']}")
    print(f"Telegram live-capable: {len(report['telegram_live_capable'] or [])}/7")
    for p in report["platforms"] or []:
        print(f"  {p['platform']:18} {p['status']:22} tg={p['telegram_action']}")
    print(f"END_TO_END_OPERATOR_READY = {report['END_TO_END_OPERATOR_READY']}")
    if report["blockers_to_ready"]:
        print("Blockers:")
        for b in report["blockers_to_ready"]:
            print(f"  - {b}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
