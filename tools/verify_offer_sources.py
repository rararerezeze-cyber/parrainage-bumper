#!/usr/bin/env python3
"""Probe registry URLs and reclassify source_class from live HTTP (no bypass)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_result
from lib.monitor.models import OfferKind, SourceClass
from lib.monitor.registry import load_registry, save_registry
from lib.monitor.structured import extract_json_ld, extract_next_data, structured_reward_hints

REPORT = ROOT / "data" / "captures" / "offer-sources-verify.json"


def classify_response(status: int, body: str, cfg_kind: str) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    low = (body or "")[:4000].lower()
    if status == 404:
        return SourceClass.WRONG_OR_DEAD_URL.value, "DEAD_URL", notes + ["http_404"]
    if status == 403 or any(x in low for x in ("captcha", "just a moment", "cf-browser", "access denied")):
        return SourceClass.ANTI_BOT_BLOCKED.value, "403_ANTIBOT", notes + [f"http_{status}"]
    if status == 0 or status >= 500:
        return SourceClass.UNVERIFIED.value, "TEMPORARY_ERROR", notes + [f"http_{status}"]
    if status >= 400:
        return SourceClass.WRONG_OR_DEAD_URL.value, "WRONG_OR_DEAD_URL", notes + [f"http_{status}"]

    # 2xx
    notes.append(f"http_{status}")
    has_struct = bool(extract_json_ld(body) or extract_next_data(body))
    hints = structured_reward_hints(body)
    if has_struct:
        notes.append("has_json_ld_or_next")
    if hints.get("referee_reward"):
        notes.append("structured_reward_hint")
    if cfg_kind == OfferKind.APP_PERSONALIZED.value:
        return SourceClass.AUTH_APP_ONLY.value, "APP_ONLY", notes
    if cfg_kind == OfferKind.OPERATOR_ONLY.value:
        return SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value, "NO_PUBLIC_OFFER", notes

    # thin page / challenge residual
    if len(body) < 200:
        return SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value, "EMPTY_PAGE", notes

    if any(x in low for x in ("referral", "parrainage", "filleul", "invite", "parrain")):
        notes.append("referral_keywords")
        return SourceClass.VERIFIED_OFFICIAL.value, "NONE", notes

    return SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value, "NO_PUBLIC_OFFER", notes + ["no_referral_keywords"]


def main() -> int:
    reg = load_registry()
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for program, cfg in sorted(reg.items(), key=lambda kv: (-kv[1].impact_count, kv[0])):
        if not cfg.source_url or not cfg.enabled:
            rows.append(
                {
                    "program": program,
                    "impact_count": cfg.impact_count,
                    "url": cfg.source_url,
                    "status": None,
                    "source_class": cfg.source_class,
                    "failure": "NO_PUBLIC_OFFER",
                    "notes": ["skipped_no_url"],
                }
            )
            continue
        res = fetch_result(cfg.source_url, timeout=20)
        sc, fail, notes = classify_response(res.status, res.body, cfg.offer_kind)
        # preserve intentional app/operator classifications
        if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value:
            sc = SourceClass.AUTH_APP_ONLY.value
        if cfg.offer_kind == OfferKind.OPERATOR_ONLY.value:
            sc = SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value
        cfg.source_class = sc
        cfg.last_verify_http = res.status
        cfg.last_verify_at = now
        if sc == SourceClass.WRONG_OR_DEAD_URL.value:
            notes.append("needs_official_url_update")
        if sc == SourceClass.ANTI_BOT_BLOCKED.value:
            notes.append("public_fetch_blocked")
        rows.append(
            {
                "program": program,
                "impact_count": cfg.impact_count,
                "url": cfg.source_url,
                "status": res.status,
                "source_class": sc,
                "failure": fail,
                "notes": notes,
                "body_len": len(res.body or ""),
                "parser": cfg.parser,
                "offer_kind": cfg.offer_kind,
            }
        )
        print(f"{program:22} http={res.status:3} {sc:35} impact={cfg.impact_count}")

    save_registry(reg)
    payload = {
        "generated_at": now,
        "count": len(rows),
        "by_source_class": {},
        "programs": rows,
    }
    for r in rows:
        payload["by_source_class"][r["source_class"]] = (
            payload["by_source_class"].get(r["source_class"], 0) + 1
        )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["by_source_class"], indent=2))
    print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
