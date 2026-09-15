#!/usr/bin/env python3
"""Hermes writer dispatch — PC_OFF_READY on real SAFE_DIFF only.

Never auto-dispatches HUMAN_SAVE_REQUIRED / NEVER_AUTO_COMMIT /
AUTH_BLOCKED_MANUAL / Super SKIP / cookie-session platforms.
Empty changed_fields = NO_SAFE_DIFF (no fake write).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.paths import MAPPINGS_DIR, golden_path, mapping_path
from lib.renderer import MappingRepository, TemplateRepository
from lib.template_builder import structure_preserved_via_markers
from lib.write_status import (
    STATUS_WRITE_VERIFIED,
    get_platform_status,
    human_local_command,
    may_auto_execute_on_safe_diff,
    runtime_route,
    summary as write_summary,
)

AUTO_SAFE_DIFF_PLATFORMS = ("1parrainage", "code-parrainage", "parrainage-co")
NEVER_AUTO_DISPATCH = (
    "referralcode-tv",
    "referralcodes",
    "referraldrop",
    "super-parrain",
)


def _mapping_write_status(platform: str, program: str, language: str = "fr") -> str | None:
    p = MAPPINGS_DIR / f"{platform}.{program}.{language}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("write_status")


def _scope_plan_to_field(plan, confirmed_field: str | None):
    """Limit a live write to the exact field explicitly confirmed by the operator.

    A writer renders the whole listing body, so merely filtering changed_fields
    would be unsafe: unrelated pending values would still be present in
    plan.rendered/plan.variables and could be published. For every unrelated
    pending field we restore the value currently represented by the platform
    baseline before rendering the target body.

    Returns (plan, note). note is NO_CONFIRMED_SAFE_DIFF when this platform has
    no pending change for the confirmed field. Any other note is fail-closed.
    """
    confirmed_field = (confirmed_field or "").strip()
    if not confirmed_field:
        return None, "missing_confirmed_field"

    original = dict(getattr(plan, "changed_fields", {}) or {})
    if confirmed_field not in original:
        plan.changed_fields = {}
        return plan, "NO_CONFIRMED_SAFE_DIFF"

    variables = dict(getattr(plan, "variables", {}) or {})

    for field, delta in original.items():
        if field == confirmed_field:
            continue
        old = (delta or {}).get("old")
        if old is None:
            old = (getattr(plan, "platform_values", {}) or {}).get(field)
        if old is None:
            return None, f"unscopable_unconfirmed_field:{field}"
        variables[field] = old

    # 1Parrainage's proven writer does not publish this static render. It
    # rereads each authenticated CKEditor body and replaces only the confirmed
    # field with an exact audited span count. Keep unrelated variables at their
    # published baseline and let that live-body validator remain authoritative.
    if plan.platform == "1parrainage" and getattr(plan, "live_validation_required", False):
        plan.variables = variables
        plan.changed_fields = {confirmed_field: original[confirmed_field]}
        plan.structure_preserved = True
        return plan, None

    mapping = MappingRepository().load(plan.platform, plan.program, plan.language)
    template = TemplateRepository().load_text(plan.platform, plan.program, plan.language)
    rendered = template
    for field in mapping.mutable_fields:
        marker_value = mapping.markers.get(field)
        value = variables.get(field)
        if not marker_value:
            return None, f"missing_marker:{field}"
        if value is None:
            return None, f"missing_scoped_value:{field}"
        rendered = rendered.replace(marker_value, str(value))

    preserved = structure_preserved_via_markers(
        template,
        plan.historical,
        rendered,
        mapping.mutable_fields,
        mapping.markers,
        dict(getattr(plan, "platform_values", {}) or {}),
        variables,
    )
    if not preserved:
        return None, "scoped_structure_not_preserved"

    plan.variables = variables
    plan.rendered = rendered
    plan.changed_fields = {confirmed_field: original[confirmed_field]}
    plan.structure_preserved = True
    return plan, None


def _persist_verified_baseline(plan, result=None) -> dict:
    """Close a post-verified write durably so it never reappears as pending.

    1Parrainage is special: its live CKEditor body is the post-write authority,
    not the static list/template render. Therefore its golden is left intact;
    only the verified published values and authenticated occurrence metadata
    are persisted.
    """
    now = datetime.now(timezone.utc).isoformat()

    gp = golden_path(plan.platform, plan.program, plan.language)
    golden_written = False
    if plan.platform != "1parrainage":
        gp.parent.mkdir(parents=True, exist_ok=True)
        tmp_g = gp.with_suffix(gp.suffix + ".tmp")
        tmp_g.write_text(plan.rendered, encoding="utf-8")
        tmp_g.replace(gp)
        golden_written = True

    mp = mapping_path(plan.platform, plan.program, plan.language)
    data = json.loads(mp.read_text(encoding="utf-8"))
    platform_values = dict(data.get("platform_values") or {})
    for field in getattr(plan, "mutable_fields", []) or []:
        value = (getattr(plan, "variables", {}) or {}).get(field)
        if value is not None:
            platform_values[field] = value
    data["platform_values"] = platform_values
    data["write_status"] = "WRITE_VERIFIED"
    data["last_write_at"] = now

    if result is not None:
        edit_url = getattr(result, "edit_url", None)
        if edit_url:
            data["edit_url"] = edit_url
    edit_urls = [str(x) for x in (getattr(plan, "edit_urls", None) or []) if x]
    if edit_urls:
        data["edit_urls"] = edit_urls
    public_offer_ids = [
        str(x) for x in (getattr(plan, "public_offer_ids", None) or []) if x
    ]
    if public_offer_ids:
        data["public_offer_ids"] = public_offer_ids

    tmp_m = mp.with_suffix(mp.suffix + ".tmp")
    tmp_m.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_m.replace(mp)

    return {
        "golden": str(gp.relative_to(ROOT)) if golden_written else None,
        "golden_written": golden_written,
        "mapping": str(mp.relative_to(ROOT)),
        "persisted_at": now,
        "edit_urls": edit_urls,
        "public_offer_ids": public_offer_ids,
    }


def _skip_route(platform: str) -> dict:
    route = runtime_route(platform)
    out = {
        "platform": platform,
        "skipped": True,
        "reason": route,
        "route": route,
        "status": get_platform_status(platform),
    }
    cmd = human_local_command(platform)
    if cmd:
        out["human_command"] = cmd
    return out


def _try_super_parrain(program: str) -> dict:
    if not may_auto_execute_on_safe_diff("super-parrain"):
        return _skip_route("super-parrain")
    st = get_platform_status("super-parrain")
    if st != STATUS_WRITE_VERIFIED:
        return {
            "platform": "super-parrain",
            "skipped": True,
            "reason": f"status={st} (need WRITE_VERIFIED for telegram live; use canary tool)",
            "status": st,
        }

    from platforms.super_parrain.writer import build_write_plan, execute_write
    from lib.super_parrain_schedule import is_eligible, mark_pending_done, record_super_action_now
    from lib.paths import golden_path, mapping_path

    eligible, nxt, hours = is_eligible()
    if not eligible:
        return {
            "platform": "super-parrain",
            "skipped": True,
            "reason": "cooldown_active",
            "next_eligible_at": nxt.isoformat(),
            "hours_remaining": round(hours, 2),
        }

    plan = build_write_plan("super-parrain", program, "fr")
    if not plan.structure_preserved:
        return {"platform": "super-parrain", "ok": False, "error": "structure_not_preserved"}
    if not plan.changed_fields:
        return {"platform": "super-parrain", "ok": True, "note": "noop_in_sync"}

    result = asyncio.run(execute_write(plan, dry_run=False))
    out = {
        "platform": "super-parrain",
        "ok": result.ok,
        "post_match": result.post_match,
        "error": result.error,
        "changed_fields": plan.changed_fields,
    }
    if result.ok and result.post_match:
        golden_path("super-parrain", program, "fr").write_bytes(plan.rendered.encode("utf-8"))
        mp = mapping_path("super-parrain", program, "fr")
        d = json.loads(mp.read_text(encoding="utf-8"))
        d["platform_values"] = {
            k: plan.variables.get(k) for k in plan.mutable_fields if plan.variables.get(k)
        }
        d["write_status"] = "WRITE_VERIFIED"
        d["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mark_pending_done("super-parrain", program, "fr")
        record_super_action_now()
        out["action"] = "UPDATED_VERIFIED"
    return out


def _try_platform_if_verified(
    platform: str,
    program: str,
    language: str = "fr",
    *,
    confirmed_field: str | None = None,
) -> dict:
    if not may_auto_execute_on_safe_diff(platform):
        return _skip_route(platform)
    st = get_platform_status(platform)
    mapping_file = MAPPINGS_DIR / f"{platform}.{program}.{language}.json"
    if not mapping_file.exists():
        return {
            "platform": platform,
            "skipped": True,
            "reason": "program_not_mapped",
            "route": runtime_route(platform),
            "status": st,
        }
    # Import platform writer dynamically when PC_OFF_READY
    writers = {
        "code-parrainage": "platforms.code_parrainage.writer",
        "1parrainage": "platforms.oneparrainage.writer",
        "parrainage-co": "platforms.parrainage_co.writer",
    }
    mod_name = writers.get(platform)
    if not mod_name:
        return {
            "platform": platform,
            "skipped": True,
            "reason": "no_live_writer_module_or_use_specialized_canary",
            "status": get_platform_status(platform),
        }
    try:
        import importlib

        mod = importlib.import_module(mod_name)
        build = getattr(mod, "build_write_plan")
        execute = getattr(mod, "execute_write")
    except Exception as exc:  # noqa: BLE001
        return {"platform": platform, "ok": False, "error": f"import:{exc}"}

    try:
        plan = build(platform, program, language)
    except Exception as exc:  # noqa: BLE001
        return {
            "platform": platform,
            "ok": False,
            "error": f"plan_build_failed:{type(exc).__name__}:{exc}",
            "route": runtime_route(platform),
            "confirmed_field": confirmed_field,
        }

    scoped, scope_note = _scope_plan_to_field(plan, confirmed_field)
    if scoped is None:
        return {
            "platform": platform,
            "ok": False,
            "error": scope_note or "field_scope_failed",
            "confirmed_field": confirmed_field,
        }
    plan = scoped
    if scope_note == "NO_CONFIRMED_SAFE_DIFF":
        return {
            "platform": platform,
            "ok": True,
            "note": scope_note,
            "route": runtime_route(platform),
            "confirmed_field": confirmed_field,
        }
    if not getattr(plan, "structure_preserved", True):
        return {
            "platform": platform,
            "ok": False,
            "error": "structure_not_preserved",
            "confirmed_field": confirmed_field,
        }
    if not getattr(plan, "changed_fields", None):
        return {
            "platform": platform,
            "ok": True,
            "note": "NO_CONFIRMED_SAFE_DIFF",
            "route": runtime_route(platform),
            "confirmed_field": confirmed_field,
        }

    result = asyncio.run(execute(plan, dry_run=False))
    verified = bool(getattr(result, "ok", False) and getattr(result, "post_match", None) is True)
    out = {
        "platform": platform,
        "ok": verified,
        "post_match": getattr(result, "post_match", None),
        "error": getattr(result, "error", None),
        "route": runtime_route(platform),
        "action": "UPDATED_VERIFIED" if verified else "FAILED",
        "confirmed_field": confirmed_field,
        "changed_fields": dict(getattr(plan, "changed_fields", {}) or {}),
        "occurrence_results": getattr(result, "occurrence_results", None),
    }
    if verified:
        try:
            out["baseline"] = _persist_verified_baseline(plan, result)
            out["baseline_persisted"] = True
        except Exception as exc:  # noqa: BLE001
            # A platform write without a durable local baseline is not closed:
            # report failure so Slack/operator sees that reconciliation is needed.
            out["ok"] = False
            out["action"] = "VERIFY_PERSIST_FAILED"
            out["baseline_persisted"] = False
            out["error"] = f"baseline_persist_failed:{exc}"
    elif getattr(result, "ok", False) and getattr(result, "post_match", None) is not True:
        out["error"] = out.get("error") or "write_not_post_verified"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-telegram", action="store_true")
    ap.add_argument("--program", default="kraken")
    ap.add_argument("--plan-only", action="store_true", help="Never live write")
    ap.add_argument(
        "--field",
        default="",
        help="Exact logical field explicitly confirmed by the operator (required for live writes)",
    )
    ap.add_argument(
        "--platform",
        default="",
        help="Optional exact platform scope from the operator command",
    )
    args = ap.parse_args()

    ws = write_summary()
    print(
        f"WRITE_VERIFIED={ws.get('WRITE_VERIFIED')} "
        f"auto_safe_diff={ws.get('telegram_live_capable')}"
    )

    reports: list[dict] = []
    if args.plan_only:
        reports.append(
            {
                "note": "plan_only",
                "WRITE_VERIFIED": ws.get("WRITE_VERIFIED"),
                "platforms": ws.get("platforms"),
            }
        )
    else:
        # Super-Parrain is a fused deferred route, never part of this immediate
        # PC-off confirmation path. Keep it visible only for global commands.
        if not args.platform:
            reports.append(_try_super_parrain(args.program))

        auto_targets = [
            plat
            for plat in AUTO_SAFE_DIFF_PLATFORMS
            if not args.platform or plat == args.platform
        ]
        for plat in auto_targets:
            reports.append(
                _try_platform_if_verified(
                    plat,
                    args.program,
                    confirmed_field=args.field,
                )
            )

        # Explicitly scoped human/blocked platform commands remain fail-closed.
        if args.platform and args.platform not in AUTO_SAFE_DIFF_PLATFORMS:
            reports.append(_skip_route(args.platform))
        elif not args.platform:
            for plat in NEVER_AUTO_DISPATCH:
                if plat == "super-parrain":
                    continue
                reports.append(_skip_route(plat))

    out = ROOT / "data" / "captures" / "verified-writers-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "from_telegram": args.from_telegram,
        "program": args.program,
        "confirmed_field": args.field or None,
        "platform_filter": args.platform or None,
        "write_status": ws,
        "reports": reports,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
