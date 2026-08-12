#!/usr/bin/env python3
"""Capture publique des cartes parrainage sur referralcodes.com/<user>."""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
from lib.template_builder import build_from_text, write_build_result

SLUG_MAP = {
    "kraken-referral-code": "kraken",
    "kraken": "kraken",
    "revolut-referral-code": "revolut",
    "coinbase-referral-code": "coinbase",
    "binance-referral-code": "binance",
    "trade-republic-referral-code": "traderepublic",
    "swissborg-referral-code": "swissborg",
    "bybit-referral-code": "bybit",
    "paypal-referral-code": "paypal",
    "wise-referral-code": "wise",
    "airbnb-referral-code": "airbnb",
    "ledger-referral-code": "ledger",
    "gemini-referral-code": "gemini",
    "bitstack-referral-code": "bitstack",
    "deblock-referral-code": "deblock",
    "robinhood-referral-code": "robinhood",
    "igraal-referral-code": "igraal",
    "poulpeo-referral-code": "poulpeo",
    "ebuyclub-referral-code": "ebuyclub",
    "joko-referral-code": "joko",
    "widilo-referral-code": "widilo",
    "betclic-referral-code": "betclic",
    "unibet-referral-code": "unibet",
    "winamax-referral-code": "winamax",
    "heetch-referral-code": "heetch",
    "omio-referral-code": "omio",
    "finary-referral-code": "finary",
    "totalenergies-referral-code": "totalenergies",
    "boursobank-referral-code": "boursobank",
    "acheel-referral-code": "acheel",
}


def extract_brands(html: str) -> list[dict]:
    brands: list[dict] = []

    for m in re.finditer(r"wire:initial-data=\"([^\"]+)\"", html):
        raw = htmlmod.unescape(m.group(1))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        memo = data.get("serverMemo", {}).get("data", {})
        if "trustedBrands" in memo:
            for b in memo["trustedBrands"]:
                brands.append(b)
        brand = memo.get("brand")
        if isinstance(brand, dict) and brand.get("shop_name"):
            brands.append(brand)

    # Flip-cards / shop-now anchors
    for m in re.finditer(
        r'data-url="(https?://[^"]+)"[^>]*>\s*<img[^>]+alt="([^"]*)"',
        html,
        re.I | re.S,
    ):
        brands.append(
            {
                "referral_url": htmlmod.unescape(m.group(1)),
                "shop_name": htmlmod.unescape(m.group(2)).replace("-referrals", "").replace(" referrals", ""),
                "shop_slug": re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-"),
            }
        )

    # data-preview + nearby shop slug
    for m in re.finditer(
        r'data-preview="https://referralcodes\.com/referral/(\d+)/preview"[^>]*data-url="([^"]+)"',
        html,
        re.I,
    ):
        brands.append(
            {
                "referral_id": int(m.group(1)),
                "referral_url": htmlmod.unescape(m.group(2)),
            }
        )

    # shop pages linked
    for m in re.finditer(r'href="(https://referralcodes\.com/shop/([^"/]+)/shop-now/\d+)"', html):
        brands.append(
            {
                "referral_url": m.group(1),
                "shop_slug": m.group(2),
                "shop_name": m.group(2).replace("-", " "),
            }
        )

    # Textual shop names near referral
    for m in re.finditer(
        r'shop_name&quot;:&quot;([^&]+)&quot;.*?shop_slug&quot;:&quot;([^&]+)&quot;.*?referral_url&quot;:&quot;([^&]+)&quot;',
        html,
        re.S,
    ):
        brands.append(
            {
                "shop_name": htmlmod.unescape(m.group(1)),
                "shop_slug": m.group(2),
                "referral_url": htmlmod.unescape(m.group(3).replace("\\/", "/")),
            }
        )

    # Also decode &quot; style JSON fragments for referral_discount
    for m in re.finditer(r"&quot;shop_slug&quot;:&quot;([^&]+)&quot;", html):
        slug = m.group(1)
        # find a window
        idx = m.start()
        window = html[max(0, idx - 500) : idx + 1500]
        def grab(key: str) -> str | None:
            mm = re.search(rf"&quot;{key}&quot;:&quot;([^&]*)&quot;", window)
            return htmlmod.unescape(mm.group(1).replace("\\/", "/")) if mm else None
        brands.append(
            {
                "shop_slug": slug,
                "shop_name": grab("shop_name") or slug,
                "referral_url": grab("referral_url"),
                "referral_discount": grab("referral_discount"),
                "referral_description": grab("referral_description"),
                "referral_code": grab("referral_code"),
            }
        )

    seen = set()
    out = []
    for b in brands:
        key = (
            b.get("referral_id"),
            b.get("shop_slug"),
            b.get("referral_url"),
            b.get("shop_name"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def brand_to_slug(brand: dict, offers: OffersRepository) -> str | None:
    slug = (brand.get("shop_slug") or "").lower()
    name = (brand.get("shop_name") or "").lower()
    if slug in SLUG_MAP:
        return SLUG_MAP[slug]
    base = slug.replace("-referral-code", "").replace("-referral", "").replace("-referrals", "")
    if base in SLUG_MAP:
        return SLUG_MAP[base]
    offer_slugs = {o.get("lk") for o in offers.load_all()}
    if base.replace("-", "") in {s.replace("-", "") for s in offer_slugs if s}:
        for s in offer_slugs:
            if s and s.replace("-", "") == base.replace("-", ""):
                return s
    if base in offer_slugs:
        return base
    key = re.sub(r"[^a-z0-9]+", "", name)
    for o in offers.load_all():
        on = re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower())
        if on and on == key:
            return o.get("lk")
    return None


def build_announcement_text(brand: dict) -> str:
    name = brand.get("shop_name") or "Offer"
    code = brand.get("referral_code") or brand.get("code") or ""
    link = brand.get("referral_url") or brand.get("referral_link") or brand.get("link") or ""
    discount = brand.get("referral_discount") or brand.get("discount") or ""
    desc = brand.get("referral_description") or brand.get("description") or ""
    lines = [f"ReferralCodes — {name}"]
    if discount:
        lines.append(f"Bonus: {discount}")
    if desc:
        lines.append(str(desc).strip())
    if code:
        lines.append(f"Code: {code}")
    if link:
        lines.append(f"Link: {link}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="https://referralcodes.com/adrien")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    offers = OffersRepository()
    html = fetch_text(args.profile)
    brands = extract_brands(html)
    print(f"Brands extracted: {len(brands)}")

    stats = {"ok": 0, "skip": 0, "error": 0}
    report = []
    for brand in brands:
        slug = brand_to_slug(brand, offers)
        if not slug:
            stats["skip"] += 1
            report.append({"shop": brand.get("shop_name") or brand.get("shop_slug"), "status": "no_offer"})
            continue
        text = build_announcement_text(brand)
        force = {}
        code = brand.get("referral_code") or brand.get("code")
        link = brand.get("referral_url") or brand.get("referral_link") or brand.get("link")
        if code:
            force["personal_code"] = str(code)
        if link:
            force["personal_link"] = str(link)
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            if len(text.strip()) < 8:
                stats["skip"] += 1
                continue
            for k, v in list(force.items()):
                if v and v not in text:
                    if k == "personal_code":
                        text += f"\nCode: {v}"
                    elif k == "personal_link":
                        text += f"\nLink: {v}"
            # reward from discount
            if brand.get("referral_discount") and "Bonus:" in text:
                force.setdefault("referee_reward", str(brand["referral_discount"]))
            result = build_from_text(
                platform="referralcodes",
                program=slug,
                language=args.language,
                golden_text=text,
                offer=offer,
                announcement_url=args.profile,
                force_values=force or None,
            )
            result.sync_mode = "manual_review_required"
            paths = write_build_result(result)
            mpath = paths["mapping"]
            data = json.loads(mpath.read_text(encoding="utf-8"))
            data["sync_mode"] = "manual_review_required"
            data["notes"] = (
                (data.get("notes") or "")
                + "; capture publique referralcodes.com — publication MANUAL (prefer agent import)"
            )
            mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["ok"] += 1
            report.append({"program": slug, "status": "ok", "mutable": result.mutable_fields})
            print(f"  OK {slug}: {result.mutable_fields} link={bool(link)}")
        except Exception as exc:  # noqa: BLE001
            stats["error"] += 1
            report.append({"shop": brand.get("shop_name"), "status": "error", "error": str(exc)})
            print(f"  ERR {brand.get('shop_name')}: {exc}")

    out = ROOT / "data" / "captures" / "referralcodes-report.json"
    out.write_text(
        json.dumps({"stats": stats, "brands_raw_count": len(brands), "items": report}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("Stats", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
