#!/usr/bin/env python3
"""FULL OPERATOR CONTROL — Telegram natural language entry.

Examples:
  Kraken code ABC123
  Kraken gain filleul 20 €
  Kraken Super-Parrain gain filleul 25 €
  Kraken conditions Déposer 100 € sous 15 jours
  Kraken status
  Kraken overrides
  Kraken supprimer override gain filleul
  Kraken Super-Parrain supprimer override gain filleul

Precedence:
  PLATFORM_OPERATOR > GLOBAL_OPERATOR > ACCEPTED_MONITOR > CANONICAL

Does not auto-accept monitor candidates. Live writes only WRITE_VERIFIED/canary.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.offers import OffersRepository
from lib.operator_overrides import (
    FIELD_ALIASES,
    LOGICAL_TO_OFFER,
    OperatorOverrideStore,
    normalize_field_name,
    normalize_platform,
    resolve_effective_value,
    status_report,
)
from lib.operator_plan import format_plan_report, plan_program_impact
from lib.paths import OPERATOR_OVERRIDES_PATH

# --- parsing ---

_PROG = r"([a-z0-9][a-z0-9\-_.]{1,40})"
# non-capturing alternation — capture is added once at use site
_PLAT_NC = (
    r"(?:super[\s\-]?parrain|parrainage\.?co|code[\s\-]?parrainage|1\s?parrainage|"
    r"referralcodes?(?:\.com)?|referralcode\.?tv|referraldrop)"
)

STATUS_RE = re.compile(
    rf"(?i)^\s*{_PROG}\s+(status|overrides)\s*$"
)
REMOVE_RE = re.compile(
    rf"(?i)^\s*{_PROG}"
    rf"(?:\s+({_PLAT_NC}))?"
    r"\s+supprimer\s+override\s+(.+?)\s*$"
)

# Program [Platform] field value
SET_RE = re.compile(
    rf"(?i)^\s*{_PROG}"
    rf"(?:\s+({_PLAT_NC}))?"
    r"\s+(.+)$"
)

# Natural: "Le nouveau code Kraken est XYZ"
NATURAL_CODE = re.compile(
    r"(?i)\b(?:code|nouveau code|code parrain)\s+([a-z0-9][a-z0-9\-_]{1,40})\s+(?:est\s+|:\s*)?([A-Za-z0-9][A-Za-z0-9._\-!]{2,60})\b"
)
NATURAL_CODE2 = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9\-_]{1,40})\s+(?:code|nouveau code)\s+(?:est\s+|:\s*)?([A-Za-z0-9][A-Za-z0-9._\-!]{2,60})\b"
)
NATURAL_LINK = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9\-_]{1,40})\s+(?:lien|link)\s+(?:est\s+|:\s*)?(https?://\S+)"
)

# Field names ordered longest-first for matching
_FIELD_KEYS = sorted(FIELD_ALIASES.keys(), key=len, reverse=True)


def resolve_program(token: str, offers: OffersRepository) -> dict | None:
    t = token.strip().lower()
    t_key = re.sub(r"[^a-z0-9]+", "", t)
    for o in offers.load_all():
        lk = (o.get("lk") or "").lower()
        name = (o.get("name") or "").lower()
        if t == lk or t_key == re.sub(r"[^a-z0-9]+", "", lk):
            return o
        if t == name or t_key == re.sub(r"[^a-z0-9]+", "", name):
            return o
    return None


def _split_field_value(rest: str) -> tuple[str, str] | None:
    rest = rest.strip()
    low = rest.lower()
    for key in _FIELD_KEYS:
        if low.startswith(key + " ") or low == key:
            # value after field name
            val = rest[len(key) :].strip()
            if low == key:
                return None  # missing value
            if val.startswith(":") or val.startswith("="):
                val = val[1:].strip()
            if not val:
                return None
            return key, val
        # "field: value"
        if low.startswith(key + ":") or low.startswith(key + "="):
            val = rest[len(key) + 1 :].strip()
            if val:
                return key, val
    # fallback: first word = field, rest = value
    parts = rest.split(None, 1)
    if len(parts) == 2 and normalize_field_name(parts[0]):
        return parts[0], parts[1]
    return None


def parse_message(message: str, offers: OffersRepository) -> dict:
    msg = message.strip()
    if not msg:
        raise ValueError("empty_message")
    # injection
    if "\x00" in msg or len(msg) > 4000:
        raise ValueError("invalid_message")

    m = STATUS_RE.match(msg)
    if m:
        offer = resolve_program(m.group(1), offers)
        if not offer:
            raise ValueError(f"unknown_program:{m.group(1)}")
        return {
            "action": "status" if m.group(2).lower() == "status" else "list",
            "program": offer["lk"],
            "offer_name": offer.get("name"),
            "platform": None,
            "field": None,
            "value": None,
        }

    m = REMOVE_RE.match(msg)
    if m:
        offer = resolve_program(m.group(1), offers)
        if not offer:
            raise ValueError(f"unknown_program:{m.group(1)}")
        plat_tok = m.group(2)
        field_tok = m.group(3).strip()
        field = normalize_field_name(field_tok)
        if not field:
            raise ValueError(f"unknown_field:{field_tok}")
        plat = normalize_platform(plat_tok) if plat_tok else None
        if plat_tok and not plat:
            raise ValueError(f"unknown_platform:{plat_tok}")
        return {
            "action": "remove",
            "program": offer["lk"],
            "offer_name": offer.get("name"),
            "platform": plat,
            "field": field,
            "value": None,
        }

    # natural code/link first
    for pat, field in ((NATURAL_CODE, "personal_code"), (NATURAL_CODE2, "personal_code"), (NATURAL_LINK, "personal_link")):
        m = pat.search(msg)
        if m:
            offer = resolve_program(m.group(1), offers)
            if offer:
                val = m.group(2).rstrip(").,;")
                return {
                    "action": "set",
                    "program": offer["lk"],
                    "offer_name": offer.get("name"),
                    "platform": None,
                    "field": field,
                    "value": val,
                }

    m = SET_RE.match(msg)
    if m:
        offer = resolve_program(m.group(1), offers)
        if not offer:
            raise ValueError(f"unknown_program:{m.group(1)}")
        plat_tok = m.group(2)
        rest = m.group(3).strip()
        # if rest starts with platform-like already consumed
        plat = normalize_platform(plat_tok) if plat_tok else None
        if plat_tok and not plat:
            # maybe second token is field not platform — re-parse
            rest = f"{plat_tok} {rest}".strip()
            plat = None
        fv = _split_field_value(rest)
        if not fv:
            raise ValueError(
                "Message non reconnu. Ex: 'Kraken gain filleul 20 €' | "
                "'Kraken Super-Parrain code ABC' | 'Kraken status'"
            )
        field_raw, value = fv
        field = normalize_field_name(field_raw)
        if not field:
            raise ValueError(f"unknown_field:{field_raw}")
        return {
            "action": "set",
            "program": offer["lk"],
            "offer_name": offer.get("name"),
            "platform": plat,
            "field": field,
            "value": value.strip(),
        }

    raise ValueError(
        "Message non reconnu. Exemples: 'Kraken code ABC123' | "
        "'Kraken gain filleul 20 €' | 'Kraken Super-Parrain gain filleul 25 €' | "
        "'Kraken status' | 'Kraken supprimer override gain filleul'"
    )


def apply_operator_command(parsed: dict, *, message: str) -> dict:
    store = OperatorOverrideStore()
    program = parsed["program"]
    action = parsed["action"]

    if action == "status":
        return {"action": "status", "status": status_report(program, store)}
    if action == "list":
        ovs = store.list_for_program(program)
        return {"action": "list", "overrides": [o.to_dict() for o in ovs]}

    if action == "remove":
        ok = store.remove(program, parsed["field"], platform=parsed.get("platform"))
        return {
            "action": "remove",
            "removed": ok,
            "field": parsed["field"],
            "platform": parsed.get("platform"),
        }

    if action == "set":
        # old effective before
        offers = OffersRepository()
        offer = offers.get_by_slug(program)
        offer_key = LOGICAL_TO_OFFER.get(parsed["field"])
        canon = str(offer.get(offer_key)) if offer_key and offer.get(offer_key) is not None else None
        before = resolve_effective_value(
            program,
            parsed["field"],
            platform=parsed.get("platform"),
            canonical=canon,
            store=store,
        )
        ov = store.upsert(
            program,
            parsed["field"],
            parsed["value"],
            platform=parsed.get("platform"),
            message=message,
        )
        after = resolve_effective_value(
            program,
            parsed["field"],
            platform=parsed.get("platform"),
            canonical=canon,
            store=store,
        )
        return {
            "action": "set",
            "override": ov.to_dict(),
            "old_effective": before.value,
            "old_source": before.source,
            "new_effective": after.value,
            "new_source": after.source,
            "written_overrides_path": str(OPERATOR_OVERRIDES_PATH),
        }

    raise ValueError(f"unknown_action:{action}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram FULL OPERATOR CONTROL")
    parser.add_argument("message", help="Message naturel style Telegram")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist overrides (default). Kept for compat; overrides always persist.",
    )
    parser.add_argument(
        "--dry-run-parse",
        action="store_true",
        help="Parse only, do not persist overrides",
    )
    parser.add_argument("--report-file", default="")
    parser.add_argument(
        "--no-plan",
        action="store_true",
        help="Skip cross-platform impact plan",
    )
    args = parser.parse_args()

    offers = OffersRepository()
    try:
        parsed = parse_message(args.message, offers)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run_parse:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return 0

    try:
        result = apply_operator_command(parsed, message=args.message)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"unknown_program:{exc}", file=sys.stderr)
        return 2

    plan = {}
    if not args.no_plan:
        plan = plan_program_impact(
            parsed["program"],
            platform_filter=parsed.get("platform"),
        )

    # report
    if parsed["action"] == "status":
        report = format_plan_report(
            parsed["program"], None, None, None, plan, action="status"
        )
        report += "\n\n" + json.dumps(result.get("status"), ensure_ascii=False, indent=2)
    elif parsed["action"] == "list":
        report = format_plan_report(
            parsed["program"], None, None, None, plan, action="list"
        )
        report += "\n\n" + json.dumps(result.get("overrides"), ensure_ascii=False, indent=2)
    elif parsed["action"] == "remove":
        report = format_plan_report(
            parsed["program"],
            parsed.get("field"),
            None,
            None,
            plan,
            action="remove",
            platform=parsed.get("platform"),
        )
        report += f"\nremoved={result.get('removed')}"
    else:
        report = format_plan_report(
            parsed["program"],
            parsed.get("field"),
            result.get("old_effective"),
            result.get("new_effective"),
            plan,
            action="set",
            platform=parsed.get("platform"),
            source=result.get("new_source"),
        )

    # Queue Super-Parrain content update when global or super-parrain
    try:
        from lib.super_parrain_schedule import enqueue_pending

        if parsed["program"] and parsed["action"] == "set":
            if parsed.get("platform") in (None, "super-parrain"):
                enqueue_pending(
                    "super-parrain",
                    parsed["program"],
                    "fr",
                    reason=f"operator_{parsed.get('field')}",
                )
    except Exception:
        pass

    print(report)

    out = Path(args.report_file) if args.report_file else (
        ROOT / "data" / "captures" / "telegram-last-report.txt"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        report
        + "\n\n"
        + json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "message": args.message,
                "parsed": parsed,
                "result": result,
                "plan_summary": plan.get("summary"),
                "mode": "FULL_OPERATOR_CONTROL",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[rapport] {out}")
    print(f"[overrides] {OPERATOR_OVERRIDES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
