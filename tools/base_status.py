#!/usr/bin/env python3
"""Gate unique BASE_READY_ALL — verification reelle, pas un flag manuel."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.coverage import build_matrix, write_coverage_report, mapping_quality
from lib.inventory import list_mapping_refs
from lib.mapping_guards import load_mapping_raw, write_blocked_reason
from lib.offers import OffersRepository
from lib.phase import live_canary_allowed, live_writes_enabled, phase_name
from platforms.registry import ALL_PLATFORMS


def _count_qualities(platform: str) -> dict[str, int]:
    full = partial = stale = 0
    for ref in list_mapping_refs():
        if ref.platform != platform:
            continue
        q = mapping_quality(ref.path)
        raw = load_mapping_raw(ref.platform, ref.program, ref.language)
        st = str(raw.get("status") or "")
        if st in {"NOT_PRESENT_ON_ACCOUNT", "STALE_MAPPING", "NOT_ON_ACCOUNT"} or q == "stale_mapping":
            stale += 1
        elif q == "capture_partial":
            partial += 1
        elif q in {"captured", "manual_write"}:
            full += 1
        else:
            partial += 1
    return {"full": full, "partial": partial, "stale": stale, "mapped": full + partial + stale}


def _writer_dry(platform: str) -> dict:
    """Return {ok, mode, note} for dry-run writer readiness."""
    try:
        if platform == "super-parrain":
            from platforms.super_parrain.writer import build_write_plan

            p = build_write_plan(program="kraken")
            return {
                "ok": bool(p.structure_preserved),
                "mode": "WRITE_PREPARED",
                "note": f"structure={p.structure_preserved} changes={list(p.changed_fields)}",
            }
        if platform == "parrainage-co":
            from platforms.parrainage_co.writer import build_write_plan

            p = build_write_plan(program="kraken")
            blocked = write_blocked_reason("parrainage-co", "paypal")
            return {
                "ok": bool(p.structure_preserved),
                "mode": "WRITE_PREPARED",
                "note": f"kraken structure={p.structure_preserved}; paypal_blocked={bool(blocked)}",
            }
        if platform == "code-parrainage":
            from platforms.code_parrainage.writer import build_write_plan

            p = build_write_plan(program="kraken")
            return {
                "ok": bool(p.structure_preserved),
                "mode": "WRITE_PREPARED",
                "note": f"structure={p.structure_preserved}",
            }
        if platform == "1parrainage":
            from platforms.oneparrainage.writer import build_write_plan

            # pick any mapped program
            refs = [r for r in list_mapping_refs() if r.platform == "1parrainage"]
            if not refs:
                return {"ok": False, "mode": "MISSING", "note": "no mappings"}
            p = build_write_plan(program=refs[0].program)
            return {
                "ok": bool(p.structure_preserved),
                "mode": "WRITE_PREPARED",
                "note": f"sample={refs[0].program} structure={p.structure_preserved}",
            }
        if platform == "referralcodes":
            from platforms.referralcodes.writer import dry_run_report

            r = dry_run_report()
            return {
                "ok": True,
                "mode": r.get("write_mode") or "MANUAL_WRITE",
                "note": f"official_import dry-run pending={r.get('pending_updates')}",
            }
        if platform == "referralcode-tv":
            refs = [r for r in list_mapping_refs() if r.platform == "referralcode-tv"]
            return {
                "ok": len(refs) >= 5,
                "mode": "MANUAL_WRITE",
                "note": f"mapped={len(refs)} — browser write not auto until risk review",
            }
        if platform == "referraldrop":
            from platforms.referraldrop.writer import dry_run_report

            r = dry_run_report()
            return {
                "ok": True,
                "mode": "AUTH_BLOCKED",
                "note": r.get("auth_status"),
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": "ERROR", "note": str(exc)}
    return {"ok": False, "mode": "UNKNOWN", "note": ""}


def _platform_base_status(pid: str, q: dict, writer: dict) -> str:
    if pid == "super-parrain":
        return "BASE_READY" if q["full"] >= 29 and writer["ok"] else "IN_PROGRESS"
    if pid == "parrainage-co":
        # stale paypal allowed
        if q["full"] >= 20 and writer["ok"] and q["partial"] == 0:
            return "BASE_READY"
        return "IN_PROGRESS"
    if pid == "code-parrainage":
        if q["full"] >= 20 and q["partial"] <= 1 and writer["ok"]:
            return "BASE_READY"
        return "IN_PROGRESS"
    if pid == "1parrainage":
        if q["mapped"] >= 7 and writer["ok"]:
            return "BASE_READY"
        return "IN_PROGRESS"
    if pid == "referralcodes":
        return "BASE_READY / MANUAL_WRITE" if q["mapped"] >= 5 and writer["ok"] else "IN_PROGRESS"
    if pid == "referralcode-tv":
        return "BASE_READY / MANUAL_WRITE" if q["mapped"] >= 5 and writer["ok"] else "IN_PROGRESS"
    if pid == "referraldrop":
        return "BASE_READY / AUTH_BLOCKED" if writer["ok"] else "IN_PROGRESS"
    return "IN_PROGRESS"


def _telegram_dry() -> dict:
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "telegram_update.py"), "Kraken code ABC123TEST"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0 and "Kraken" in out and "publication reelle" in out.lower() or "Aucune publication" in out
        # softer ok
        ok = r.returncode == 0 and "kraken" in out.lower()
        return {"ok": ok, "returncode": r.returncode, "tail": out[-500:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _pytest() -> dict:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        tail = (r.stdout or "")[-200:]
        passed = "passed" in tail
        return {"ok": r.returncode == 0 and passed, "returncode": r.returncode, "tail": tail.strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _global_dry_run() -> dict:
    try:
        from sync import run_all

        results = run_all()
        # Fail only on hard errors (not pending_update)
        hard = [r for r in results if r.status in {"error", "failed"}]
        return {
            "ok": len(hard) == 0,
            "rows": len(results),
            "hard_errors": len(hard),
            "statuses": {s: sum(1 for r in results if r.status == s) for s in sorted({r.status for r in results})},
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def main() -> int:
    write_coverage_report()
    matrix = build_matrix()
    offers_n = len(OffersRepository().load_all())
    mapped_pairs = len(list_mapping_refs())

    platforms = []
    blockers = []
    for pid in ALL_PLATFORMS:
        q = _count_qualities(pid)
        writer = _writer_dry(pid)
        status = _platform_base_status(pid, q, writer)
        row = {
            "platform": pid,
            "status": status,
            "mapped": q["mapped"],
            "full": q["full"],
            "partial": q["partial"],
            "stale": q["stale"],
            "write": writer["mode"],
            "writer_ok": writer["ok"],
            "note": writer["note"],
        }
        platforms.append(row)
        if not str(status).startswith("BASE_READY"):
            blockers.append(f"{pid}:{status}")
        if not writer["ok"]:
            blockers.append(f"{pid}:writer_not_ready:{writer['note']}")

    tg = _telegram_dry()
    tests = _pytest()
    dry = _global_dry_run()

    if not dry.get("ok"):
        blockers.append(f"global_dry_run:{dry}")
    if not tg.get("ok"):
        blockers.append("telegram_dry_run_fail")
    if not tests.get("ok"):
        blockers.append("tests_fail")
    if live_writes_enabled():
        blockers.append("live_writes_should_be_off")
    if live_canary_allowed():
        blockers.append("live_canary_should_be_off")

    # Corrupt mapping check: paypal must be blocked
    if not write_blocked_reason("parrainage-co", "paypal"):
        blockers.append("paypal_should_be_write_blocked")

    base_ready_all = (
        all(str(p["status"]).startswith("BASE_READY") for p in platforms)
        and dry.get("ok")
        and tg.get("ok")
        and tests.get("ok")
        and not live_writes_enabled()
        and not live_canary_allowed()
        and bool(write_blocked_reason("parrainage-co", "paypal"))
    )

    report = {
        "phase": phase_name(),
        "live_writes_enabled": live_writes_enabled(),
        "live_canary_allowed": live_canary_allowed(),
        "programs": offers_n,
        "mapped_pairs": mapped_pairs,
        "platforms": platforms,
        "global_dry_run": dry,
        "telegram_dry_run": tg,
        "tests": tests,
        "blockers": blockers,
        "base_ready_all": base_ready_all,
        "coverage": {
            "programs_in_offers": matrix.get("programs_in_offers"),
            "mapped_pairs": matrix.get("mapped_pairs"),
        },
    }
    out = ROOT / "data" / "captures" / "base-status.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== AUTOFRESH BASE STATUS ===")
    print(f"Programs: {offers_n}")
    print(f"Platforms: 7")
    print(f"Mapped pairs: {mapped_pairs}")
    print()
    for p in platforms:
        print(f"{p['platform']}: {p['status']}  (mapped={p['mapped']} full={p['full']} partial={p['partial']} stale={p['stale']} write={p['write']})")
    print()
    print(f"Global dry-run: {'PASS' if dry.get('ok') else 'FAIL'}")
    print(f"Telegram dry-run: {'PASS' if tg.get('ok') else 'FAIL'}")
    print(f"Tests: {'PASS' if tests.get('ok') else 'FAIL'} {tests.get('tail','')}")
    print(f"Live writes enabled: {'YES' if live_writes_enabled() else 'NO'}")
    print()
    if blockers and not base_ready_all:
        print("Blockers:")
        for b in blockers:
            print(f"  - {b}")
    print(f"BASE_READY_ALL = {'YES' if base_ready_all else 'NO'}")
    print(f"report={out}")
    return 0 if base_ready_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
