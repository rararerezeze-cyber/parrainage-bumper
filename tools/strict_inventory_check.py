#!/usr/bin/env python3
"""Strict completeness inventory (READ-ONLY). No writes."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.coverage import mapping_quality
from lib.http_fetch import fetch_text
from lib.inventory import list_mapping_refs
from lib.mapping_guards import load_mapping_raw
from lib.offers import OffersRepository


def count_qualities() -> dict:
    by = {}
    for ref in list_mapping_refs():
        q = mapping_quality(ref.path)
        raw = load_mapping_raw(ref.platform, ref.program, ref.language)
        by.setdefault(ref.platform, Counter())
        by[ref.platform][q] += 1
        by[ref.platform]["__total"] += 1
        st = str(raw.get("status") or "")
        if st:
            by[ref.platform][f"status:{st}"] += 1
    return {k: dict(v) for k, v in by.items()}


def inventory_1parrainage_public() -> dict:
    url = "https://www.1parrainage.com/listeannonces_98906_Adrien89.php"
    html = fetch_text(url)
    (ROOT / "data/captures/1parrainage-strict-list.html").write_text(html, encoding="utf-8")
    ids = re.findall(r"id_par=98906&(?:amp;)?id=(\d+)", html)
    ids += re.findall(r"id=(\d+)&(?:amp;)?id_par=98906", html)
    ids = list(dict.fromkeys(ids))
    # logo alts often brand names
    alts = re.findall(r'alt=["\']([^"\']+)["\']', html, flags=re.I)
    brands = []
    for a in alts:
        a = unescape(a).strip()
        if len(a) < 2:
            continue
        low = a.lower()
        if any(x in low for x in ("logo", "icon", "facebook", "twitter", "pixel")):
            continue
        brands.append(a)
    # unique brands preserving order
    brands = list(dict.fromkeys(brands))
    # map to offers
    offers = OffersRepository()
    mapped = []
    unmapped = []
    for b in brands:
        low = re.sub(r"[^a-z0-9]+", "", b.lower())
        hit = None
        for o in offers.load_all():
            name = re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower())
            lk = re.sub(r"[^a-z0-9]+", "", (o.get("lk") or "").lower())
            if low and (low == name or low == lk or low in name or name in low):
                hit = o.get("lk")
                break
        if hit:
            mapped.append({"brand": b, "program": hit})
        else:
            unmapped.append(b)
    existing = {r.program for r in list_mapping_refs() if r.platform == "1parrainage"}
    return {
        "list_url": url,
        "offer_ids_found": len(ids),
        "offer_ids": ids,
        "brand_alts_found": len(brands),
        "brands": brands,
        "matched_to_offers": mapped,
        "unmatched_brands": unmapped,
        "mappings_present": sorted(existing),
        "mappings_count": len(existing),
        "coverage_of_ids": f"{len(existing)}/{len(ids) if ids else '?'}",
    }


def inventory_super_public() -> dict:
    # Use existing super-parrain mappings count vs profile if possible
    mapped = [r for r in list_mapping_refs() if r.platform == "super-parrain"]
    return {"platform": "super-parrain", "mappings": len(mapped), "expected_min": 35}


def inventory_parrainage_co() -> dict:
    mapped = []
    stale = []
    for ref in list_mapping_refs():
        if ref.platform != "parrainage-co":
            continue
        raw = load_mapping_raw(ref.platform, ref.program, ref.language)
        q = mapping_quality(ref.path)
        if q == "stale_mapping" or str(raw.get("status") or "").startswith("NOT_PRESENT"):
            stale.append(ref.program)
        else:
            mapped.append(ref.program)
    return {
        "present_active": len(mapped),
        "stale": len(stale),
        "stale_programs": stale,
        "total_files": len(mapped) + len(stale),
        "auth_account_offers_known": 26,
        "note": "auth inventory previously 26 active offers; PayPal not on account",
    }


def main() -> int:
    qualities = count_qualities()
    missing = []
    partial = []
    for ref in list_mapping_refs():
        q = mapping_quality(ref.path)
        if q == "missing_source":
            missing.append(f"{ref.platform}/{ref.program}")
        if q == "capture_partial":
            partial.append(f"{ref.platform}/{ref.program}")

    one = inventory_1parrainage_public()
    report = {
        "qualities_by_platform": qualities,
        "missing_source": missing,
        "unexplained_partial": partial,
        "super_parrain": inventory_super_public(),
        "parrainage_co": inventory_parrainage_co(),
        "code_parrainage": {
            "mappings": sum(1 for r in list_mapping_refs() if r.platform == "code-parrainage"),
            "expected": 24,
        },
        "referralcode_tv": {
            "mappings": sum(1 for r in list_mapping_refs() if r.platform == "referralcode-tv"),
        },
        "referralcodes": {
            "mappings": sum(1 for r in list_mapping_refs() if r.platform == "referralcodes"),
        },
        "referraldrop": {
            "mappings": sum(1 for r in list_mapping_refs() if r.platform == "referraldrop"),
        },
        "1parrainage": one,
    }
    out = ROOT / "data" / "captures" / "strict-inventory-check.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
