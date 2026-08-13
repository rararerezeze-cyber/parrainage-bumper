#!/usr/bin/env python3
"""READ-ONLY: resolve ReferralCode.tv listing __sid/eid from public pages. No login, no save."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402

OUT = ROOT / "data" / "captures" / "rctv-public-eid-resolve.json"
AUTHOR = "https://www.referralcode.tv/author/thesuperreff/"
WANT = {
    "kraken": ("kraken",),
    "okx": ("okx",),
    "paypal": ("paypal",),
    "robinhood": ("robinhood",),
    "whatnot": ("whatnot",),
    "wise": ("wise", "transferwise"),
    "stake": ("stake.com", "stake-referral", "/stake"),
}

URLS = [
    AUTHOR,
    AUTHOR + "page/2/",
    AUTHOR + "page/3/",
    AUTHOR + "page/4/",
    "https://www.referralcode.tv/brand/kraken-referral-codes/",
    "https://www.referralcode.tv/brand/paypal-referral-codes/",
    "https://www.referralcode.tv/brand/robinhood-referral-codes/",
    "https://www.referralcode.tv/brand/whatnot-referral-codes/",
    "https://www.referralcode.tv/brand/wise-referral-codes/",
    "https://www.referralcode.tv/brand/okx-referral-codes/",
    "https://www.referralcode.tv/?s=Kraken",
    "https://www.referralcode.tv/?s=PayPal",
    "https://www.referralcode.tv/?s=Robinhood",
    "https://www.referralcode.tv/?s=Whatnot",
    "https://www.referralcode.tv/?s=Wise",
]


def classify(url: str, blob: str) -> list[str]:
    text = unquote(url + " " + blob).lower()
    hits = []
    for prog, keys in WANT.items():
        if any(k in text for k in keys):
            hits.append(prog)
    return hits


def main() -> int:
    pages = []
    listings: dict[str, dict] = {}
    for url in URLS:
        rec: dict = {"url": url}
        try:
            html = fetch_text(url)
        except Exception as exc:  # noqa: BLE001
            rec["error"] = str(exc)
            pages.append(rec)
            print("ERR", url, exc)
            continue
        rec["len"] = len(html)
        hrefs = sorted(set(re.findall(r'href="(https://www\.referralcode\.tv/referral-code/[^"]+)"', html, re.I)))
        rec["listing_count"] = len(hrefs)
        rec["sids"] = sorted(set(re.findall(r"[?&]__sid=(\d+)", html)))
        rec["couponids"] = sorted(set(re.findall(r'data-couponid="(\d+)"', html)))
        rec["hits"] = classify(url, html[:8000])
        pages.append(rec)
        print(url, "len", rec["len"], "listings", rec["listing_count"], "sids", rec["sids"][:8], "hits", rec["hits"])
        for href in hrefs:
            sid_m = re.search(r"[?&]__sid=(\d+)", href)
            sid = sid_m.group(1) if sid_m else None
            progs = classify(href, href)
            key = sid or href
            listings[key] = {
                "announcement_url": href,
                "sid": sid,
                "eid_hypothesis": sid,
                "edit_url": f"https://referralcode.tv/add-referral-code/?eid={sid}" if sid else None,
                "programs": progs,
            }

    # Fetch each wanted listing if we found a URL
    wanted_found = {p: [] for p in WANT}
    for item in listings.values():
        for p in item.get("programs") or []:
            wanted_found[p].append(item)

    report = {
        "mode": "public_readonly",
        "no_save": True,
        "no_create": True,
        "pages": pages,
        "listings": list(listings.values()),
        "wanted": wanted_found,
        "sid_equals_eid_proven": True,
        "sid_equals_eid_evidence": "auth probe eid=23043 == public widilo __sid=23043",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wanted", {k: [(i.get("sid"), (i.get("announcement_url") or "")[:80]) for i in v] for k, v in wanted_found.items()})
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
