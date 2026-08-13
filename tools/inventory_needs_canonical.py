#!/usr/bin/env python3
"""Inventorie les annonces Super-Parrain absentes de offers.json (needs_canonical_data)."""
from __future__ import annotations

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
from lib.template_builder import detect_platform_values

# helpers depuis le capture public Super-Parrain
sys.path.insert(0, str(ROOT / "tools"))
import capture_super_parrain as csp  # noqa: E402

OUT = ROOT / "data" / "needs_canonical_data.json"
GOLDEN_DIR = ROOT / "data" / "orphans" / "super-parrain"


def main() -> int:
    offers = OffersRepository()
    profile = csp.DEFAULT_PROFILE
    html = fetch_text(profile)
    urls = csp.extract_profile_announcement_urls(html)
    items = []
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    for url in urls:
        time.sleep(0.3)
        slug = csp.guess_program_slug(url, offers)
        if slug is not None:
            # present in offers (or alias maps to one)
            try:
                offers.get_by_slug(slug)
                continue
            except KeyError:
                pass

        # Derive a stable orphan key from URL
        m = re.search(r"/offres/([^/]+)/", url)
        raw_key = m.group(1) if m else re.sub(r"\W+", "-", url)[-40:]
        name = raw_key.replace("-", " ").title()

        try:
            page = fetch_text(url)
            text = csp.extract_message(page) or ""
        except Exception as exc:  # noqa: BLE001
            items.append(
                {
                    "name": name,
                    "program_key": raw_key,
                    "url": url,
                    "platform": "super-parrain",
                    "language": "fr",
                    "status": "needs_canonical_data",
                    "reason": f"absent de offers.json; fetch error: {exc}",
                    "golden_text": None,
                    "detected_values": {},
                }
            )
            continue

        values, conf, notes = detect_platform_values(text, offer=None)
        golden_path = GOLDEN_DIR / f"{raw_key}.fr.golden.txt"
        if text:
            golden_path.write_bytes(text.encode("utf-8"))

        items.append(
            {
                "name": name,
                "program_key": raw_key,
                "url": url,
                "platform": "super-parrain",
                "language": "fr",
                "status": "needs_canonical_data",
                "reason": "aucune entree canonique dans offers.json (pas de creation auto de donnees metier incertaines)",
                "golden_file": str(golden_path.relative_to(ROOT)).replace("\\", "/"),
                "golden_text": text,
                "detected_values": values,
                "confidences": conf,
                "notes": notes,
            }
        )
        print(f"  NCD {raw_key}: {len(text)} chars values={list(values)}")

    payload = {
        "version": 1,
        "source_profile": profile,
        "count": len(items),
        "items": items,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} count={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
