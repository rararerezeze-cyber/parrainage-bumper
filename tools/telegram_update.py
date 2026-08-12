#!/usr/bin/env python3
"""Entree naturelle Telegram-like: met a jour offers.json puis dry-run global.

Exemples:
  python tools/telegram_update.py "Kraken code BBBBBB"
  python tools/telegram_update.py "Le nouveau code Kraken est BBBBBB"
  python tools/telegram_update.py "Kraken lien https://invite.example/x"

Aucune publication plateforme. Commit offers.json optionnel via --write.
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
from lib.paths import OFFERS_PATH
from sync import run_all

# Patterns souples
CODE_PATTERNS = [
    re.compile(
        r"(?i)\b([a-z0-9][a-z0-9\-_]{1,40})\s+(?:code|nouveau code|code parrain)\s+(?:est\s+|:\s*)?([A-Za-z0-9][A-Za-z0-9._\-!]{2,60})\b"
    ),
    re.compile(
        r"(?i)\b(?:code|nouveau code|code parrain)\s+([a-z0-9][a-z0-9\-_]{1,40})\s+(?:est\s+|:\s*)?([A-Za-z0-9][A-Za-z0-9._\-!]{2,60})\b"
    ),
    re.compile(
        r"(?i)\b([a-z0-9][a-z0-9\-_]{1,40})\s+code\s+([A-Za-z0-9][A-Za-z0-9._\-!]{2,60})\b"
    ),
]
LINK_PATTERNS = [
    re.compile(
        r"(?i)\b([a-z0-9][a-z0-9\-_]{1,40})\s+(?:lien|nouveau lien|link)\s+(?:est\s+|:\s*)?(https?://\S+)"
    ),
    re.compile(
        r"(?i)\b(?:lien|nouveau lien|link)\s+([a-z0-9][a-z0-9\-_]{1,40})\s+(?:est\s+|:\s*)?(https?://\S+)"
    ),
]


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


def parse_message(message: str, offers: OffersRepository) -> dict:
    msg = message.strip()
    for pat in CODE_PATTERNS:
        m = pat.search(msg)
        if m:
            prog_tok, code = m.group(1), m.group(2)
            offer = resolve_program(prog_tok, offers)
            if offer:
                return {
                    "program": offer["lk"],
                    "field": "code",
                    "value": code.strip(),
                    "offer_name": offer.get("name"),
                }
    for pat in LINK_PATTERNS:
        m = pat.search(msg)
        if m:
            prog_tok, link = m.group(1), m.group(2)
            offer = resolve_program(prog_tok, offers)
            if offer:
                return {
                    "program": offer["lk"],
                    "field": "link",
                    "value": link.rstrip(").,;"),
                    "offer_name": offer.get("name"),
                }
    raise ValueError(
        "Message non reconnu. Exemples: 'Kraken code ABC123' | "
        "'Le nouveau code Kraken est ABC123' | 'Kraken lien https://...'"
    )


def apply_update(field: str, program: str, value: str, write: bool) -> dict:
    """Applique la maj en memoire; ecrit offers.json si write=True.

    Pour le dry-run, on ecrit toujours un fichier temporaire utilise via
    BONUS_PARRAIN_OFFERS_PATH afin que run_all voie la nouvelle valeur.
    """
    offers = OffersRepository()
    data = offers.load_all()
    old = None
    found = False
    for o in data:
        if o.get("lk") == program:
            old = o.get(field)
            o[field] = value
            found = True
            break
    if not found:
        raise KeyError(program)

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if write:
        OFFERS_PATH.write_text(payload, encoding="utf-8")
        effective = OFFERS_PATH
    else:
        tmp = ROOT / "data" / "captures" / "_offers_preview.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        effective = tmp
    return {
        "old": old,
        "new": value,
        "written": write,
        "effective_offers_path": str(effective),
    }


def format_telegram_report(
    parsed: dict, update: dict, results: list
) -> str:
    lines = [
        f"{parsed['offer_name'] or parsed['program']} mis a jour (dry-run)",
        f"{parsed['field']}: {update['old']!r} -> {update['new']!r}",
        f"offers.json ecrit: {'oui' if update['written'] else 'non (simulation)'}",
        "",
    ]
    by_status: dict[str, list] = {}
    for r in results:
        if r.program == "*" or r.platform == "inventory":
            continue
        if r.program != parsed["program"] and parsed["program"] not in (r.program or ""):
            # show all? user wants multi-platform for that program
            if r.program != parsed["program"]:
                continue
        by_status.setdefault(r.status, []).append(r)

    # Prefer filter by program
    prog_results = [r for r in results if r.program == parsed["program"]]
    if not prog_results:
        prog_results = [r for r in results if r.program not in {"*", "needs_canonical_data"}]

    ok = [r for r in prog_results if r.status in {"in_sync", "pending_update"}]
    manual = [r for r in results if r.status == "manual"]
    pending_cap = [r for r in results if r.status == "capture_pending"]

    lines.append(f"Programme {parsed['program']}:")
    for r in prog_results:
        extra = ""
        if r.changed_fields:
            bits = [f"{k}" for k in r.changed_fields if not k.startswith("_")]
            extra = f" ({', '.join(bits)})" if bits else ""
        lines.append(f"  - {r.platform}: {r.status}{extra}")

    lines.append("")
    lines.append(
        f"{len(ok)} plateforme(s) avec mapping pour ce programme | "
        f"{len(pending_cap)} capture_pending | {len(manual)} manual (global)"
    )
    lines.append("Aucune publication reelle effectuee.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message", help="Message naturel style Telegram")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Ecrit offers.json (sinon simulation parse+rapport)",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Chemin fichier rapport (defaut data/captures/telegram-last-report.txt)",
    )
    args = parser.parse_args()

    offers = OffersRepository()
    try:
        parsed = parse_message(args.message, offers)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    update = apply_update(parsed["field"], parsed["program"], parsed["value"], write=args.write)
    # Faire voir la valeur prevue/ecrite au dry-run
    import os

    os.environ["BONUS_PARRAIN_OFFERS_PATH"] = update["effective_offers_path"]
    results = run_all()
    report = format_telegram_report(parsed, update, results)
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
                "update": update,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[rapport] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
