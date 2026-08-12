#!/usr/bin/env python3
"""Capture publique de toutes les annonces Super-Parrain d'un profil."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
from lib.template_builder import build_from_text, write_build_result

DEFAULT_PROFILE = "https://www.super-parrain.com/users/adrien-b-8"
BASE = "https://www.super-parrain.com"

# URL path segments → offers.json lk
SLUG_ALIASES = {
    "boursobank-1": "boursobank",
    "boursobank": "boursobank",
    "trade-republic": "traderepublic",
    "traderepublic": "traderepublic",
    "coinbase-1": "coinbase",
    "binance-1": "binance",
    "airbnb-1": "airbnb",
    "airbnb-hotes": "airbnb",
    "deblock-11": "deblock",
    "deblock": "deblock",
    "lolivier": "lolivier",
    "lolivier-assurance": "lolivier",
    "parionssport": "parionssport",
    "parions-sport": "parionssport",
    "fdj-francaise-des-jeux": None,  # pas dans offers
    "vinted": None,
    "whatnot": None,
    "okx": None,
    "nrj-mobile": None,
    "plum": None,
    "robinhood": "robinhood",
}


def html_message_to_text(fragment: str) -> str:
    parts = re.split(r"<br\s*/?>", fragment, flags=re.I)
    lines: list[str] = []
    for part in parts:
        part = re.sub(r"<[^>]+>", "", part)
        part = unescape(part)
        if re.fullmatch(r"[\r\n\t ]*", part or ""):
            lines.append("")
        else:
            part = re.sub(r"^[\r\n]+", "", part)
            part = re.sub(r"[\r\n]+$", "", part)
            lines.append(part)
    return "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")


def extract_profile_announcement_urls(profile_html: str) -> list[str]:
    # /offres/.../annonces/adrien-b-8
    urls = re.findall(
        r'href="(/offres/[^"]+/annonces/[^"]+)"',
        profile_html,
        flags=re.I,
    )
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(urljoin(BASE, u))
    return out


def extract_message(html: str) -> str | None:
    m = re.search(
        r'<p class="c-parrain__message">(.*?)</p>',
        html,
        flags=re.I | re.S,
    )
    if not m:
        m = re.search(
            r'class="c-parrain__message"[^>]*>(.*?)</p>',
            html,
            flags=re.I | re.S,
        )
    if not m:
        return None
    return html_message_to_text(m.group(1))


def guess_program_slug(url: str, offers: OffersRepository) -> str | None:
    # /offres/kraken/parrainage-kraken/annonces/...
    # /offres/cryptomonnaie/parrainage-kraken/... sometimes
    path = url.split("?")[0].rstrip("/")
    parts = path.split("/")
    # find segment after /offres/
    try:
        i = parts.index("offres")
    except ValueError:
        return None
    candidates = []
    if i + 1 < len(parts):
        candidates.append(parts[i + 1])
    # parrainage-XXX
    for p in parts:
        if p.startswith("parrainage-"):
            candidates.append(p[len("parrainage-") :])
            candidates.append(p)

    offer_slugs = {o.get("lk") for o in offers.load_all() if o.get("lk")}
    offer_by_name = {
        re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower()): o.get("lk")
        for o in offers.load_all()
    }

    for c in candidates:
        c_norm = c.lower().strip()
        if c_norm in SLUG_ALIASES:
            alias = SLUG_ALIASES[c_norm]
            if alias is None:
                return None
            return alias
        if c_norm in offer_slugs:
            return c_norm
        # strip trailing -1 -11
        base = re.sub(r"-\d+$", "", c_norm)
        if base in offer_slugs:
            return base
        if base in SLUG_ALIASES and SLUG_ALIASES[base]:
            return SLUG_ALIASES[base]
        key = re.sub(r"[^a-z0-9]+", "", c_norm)
        if key in offer_by_name:
            return offer_by_name[key]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--language", default="fr")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause entre pages")
    parser.add_argument("--limit", type=int, default=0, help="0 = toutes")
    parser.add_argument("--dry-run-only", action="store_true", help="Liste sans ecrire")
    args = parser.parse_args()

    offers = OffersRepository()
    print(f"Profil: {args.profile}")
    profile_html = fetch_text(args.profile)
    urls = extract_profile_announcement_urls(profile_html)
    print(f"Annonces trouvees: {len(urls)}")

    if args.limit:
        urls = urls[: args.limit]

    stats = {"ok": 0, "skip": 0, "error": 0, "no_offer": 0, "no_message": 0}
    report = []

    for url in urls:
        time.sleep(args.sleep)
        slug = guess_program_slug(url, offers)
        if not slug:
            stats["no_offer"] += 1
            report.append({"url": url, "status": "no_offer_match"})
            print(f"  SKIP no_offer  {url}")
            continue
        try:
            html = fetch_text(url)
        except Exception as exc:  # noqa: BLE001
            stats["error"] += 1
            report.append({"url": url, "program": slug, "status": "fetch_error", "error": str(exc)})
            print(f"  ERR fetch {slug}: {exc}")
            continue
        text = extract_message(html)
        if not text or len(text.strip()) < 10:
            stats["no_message"] += 1
            report.append({"url": url, "program": slug, "status": "no_message"})
            print(f"  SKIP no_message {slug}")
            continue
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
            stats["no_offer"] += 1

        if args.dry_run_only:
            print(f"  WOULD capture {slug} ({len(text)} chars)")
            stats["ok"] += 1
            continue

        try:
            result = build_from_text(
                platform="super-parrain",
                program=slug,
                language=args.language,
                golden_text=text,
                offer=offer,
                announcement_url=url,
            )
            paths = write_build_result(result)
            stats["ok"] += 1
            report.append(
                {
                    "url": url,
                    "program": slug,
                    "status": "ok",
                    "mutable": result.mutable_fields,
                    "sync_mode": result.sync_mode,
                    "notes": result.notes,
                }
            )
            print(
                f"  OK {slug:16} mutable={result.mutable_fields} mode={result.sync_mode}"
            )
        except Exception as exc:  # noqa: BLE001
            stats["error"] += 1
            report.append({"url": url, "program": slug, "status": "build_error", "error": str(exc)})
            print(f"  ERR build {slug}: {exc}")

    out = ROOT / "data" / "captures" / "super-parrain-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"stats": stats, "items": report}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Stats:", stats)
    print("Report:", out)
    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
