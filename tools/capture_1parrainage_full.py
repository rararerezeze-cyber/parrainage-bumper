#!/usr/bin/env python3
"""Capture complete 1Parrainage list inventory (READ-ONLY, native style).

Uses public list page (always available) and optionally can be extended with auth.
Does NOT save/edit anything on the site.
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
from lib.template_builder import build_from_text, write_build_result

LIST_URL = "https://www.1parrainage.com/listeannonces_98906_Adrien89.php"
PLATFORM = "1parrainage"


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>|</div>|</tr>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def brand_to_slug(brand: str, offers: OffersRepository) -> str | None:
    low = re.sub(r"[^a-z0-9]+", "", brand.lower())
    # strip coupon prefix noise
    for prefix in ("couponpromotionnel", "promo", "offre"):
        if low.startswith(prefix):
            low = low[len(prefix) :]
    aliases = {
        "boursoramabanque": "boursobank",
        "boursobank": "boursobank",
        "unibetsport": "unibet",
        "traderepublic": "traderepublic",
        "totalenergies": "totalenergies",
        "francaisedesjeuxfdj": "fdj-francaise-des-jeux",
        "fdj": "fdj-francaise-des-jeux",
        "ledgerwallet": "ledger",
        "parionssport": "parionssport",
        "airbnbhote": "airbnb",
        "airbnb": "airbnb",
        "lolivierassurance": "lolivier",
        "lolivierassurancehabitation": "lolivier",
        "ubber eats": "ubereats",
        "ubereats": "ubereats",
        "paypal": "paypal",
        "igraal": "igraal",
        "nrjmobile": "nrj-mobile",
        "swissborg": "swissborg",
    }
    if low in aliases:
        return aliases[low]
    for o in offers.load_all():
        name = re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower())
        lk = re.sub(r"[^a-z0-9]+", "", (o.get("lk") or "").lower())
        if low and (low == name or low == lk or (len(name) >= 4 and name in low) or (len(lk) >= 4 and lk in low)):
            return o.get("lk")
    return aliases.get(low)


def extract_blocks(html: str) -> list[dict]:
    """Split list HTML into offer-ish blocks with brand + body + offer id."""
    blocks = []
    # Pattern: logo alt + nearby text until next logo or J'en profite
    # Use id_par pairs
    parts = re.split(r'(?i)(coupon promotionnel[^"\']*|id_par=98906&(?:amp;)?id=\d+)', html)
    # Simpler: find each coupon alt and take following 3000 chars of text
    for m in re.finditer(
        r'alt=["\']([^"\']*coupon promotionnel[^"\']*)["\']',
        html,
        flags=re.I,
    ):
        brand_raw = unescape(m.group(1))
        start = m.end()
        chunk = html[start : start + 5000]
        # stop at next coupon or footer
        stop = re.search(r'alt=["\'][^"\']*coupon promotionnel', chunk, flags=re.I)
        if stop:
            chunk = chunk[: stop.start()]
        text = html_to_text(chunk)
        # trim chrome
        lines = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                lines.append("")
                continue
            if s.lower() in {"signaler", "j'en profite", "jen profite", "lire la suite"}:
                continue
            if "1parrainage" in s.lower() and "blog" in s.lower():
                continue
            lines.append(s)
        body = "\n".join(lines).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        if len(body) < 40:
            continue
        oid_m = re.search(r"id_par=98906&(?:amp;)?id=(\d+)", chunk)
        if not oid_m:
            oid_m = re.search(r"id=(\d+)", chunk)
        blocks.append(
            {
                "brand": brand_raw,
                "body": body[:5000],
                "offer_id": oid_m.group(1) if oid_m else None,
            }
        )
    return blocks


def main() -> int:
    offers = OffersRepository()
    html = fetch_text(LIST_URL)
    (ROOT / "data/captures/1parrainage-strict-list.html").write_text(html, encoding="utf-8")
    blocks = extract_blocks(html)
    report = {
        "platform": PLATFORM,
        "list_url": LIST_URL,
        "blocks_found": len(blocks),
        "items": [],
        "errors": [],
        "unmapped": [],
        "duplicates": [],
    }
    seen = set()
    for b in blocks:
        brand = b["brand"]
        slug = brand_to_slug(brand, offers)
        if not slug:
            report["unmapped"].append({"brand": brand, "offer_id": b.get("offer_id")})
            continue
        if slug in seen:
            report["duplicates"].append(slug)
            continue
        body = b["body"]
        # Prefer body starting at Offre/Bonus if present
        for pat in (r"(Offre[\s\S]{40,})", r"(⭐[\s\S]{40,})", r"(Code[\s\S]{40,})"):
            m = re.search(pat, body, flags=re.I)
            if m and len(m.group(1)) > 60:
                body = m.group(1).strip()
                break
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
            report["unmapped"].append({"brand": brand, "slug_guess": slug, "reason": "not_in_offers"})
            continue
        try:
            result = build_from_text(
                platform=PLATFORM,
                program=slug,
                language="fr",
                golden_text=body,
                offer=offer,
                announcement_url=LIST_URL
                + (f"#id={b['offer_id']}" if b.get("offer_id") else ""),
            )
            # prune null offer fields
            cleaned = []
            from lib.template_builder import DEFAULT_MARKERS

            for f in result.mutable_fields:
                of = {
                    "personal_code": "code",
                    "personal_link": "link",
                    "referee_reward": "reward",
                }.get(f)
                if of and (offer.get(of) is None or str(offer.get(of)).strip() == ""):
                    marker = DEFAULT_MARKERS[f]
                    if marker in result.template and f in result.platform_values:
                        result.template = result.template.replace(
                            marker, result.platform_values[f]
                        )
                    continue
                cleaned.append(f)
            result.mutable_fields = cleaned
            result.platform_values = {
                k: v for k, v in result.platform_values.items() if k in cleaned
            }
            paths = write_build_result(result)
            mpath = paths["mapping"]
            data = json.loads(mpath.read_text(encoding="utf-8"))
            data["quality"] = "native_list_full_inventory"
            data["style_policy"] = "native_platform_style_only"
            data["platform_offer_id"] = b.get("offer_id")
            data["notes"] = "; ".join(
                filter(
                    None,
                    [
                        data.get("notes"),
                        "native style only — no emoji reinjection",
                        f"list_brand={brand}",
                    ],
                )
            )
            mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            seen.add(slug)
            report["items"].append(
                {
                    "program": slug,
                    "brand": brand,
                    "chars": len(body),
                    "offer_id": b.get("offer_id"),
                    "mutable": result.mutable_fields,
                }
            )
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"brand": brand, "slug": slug, "error": str(exc)})

    report["captured"] = len(report["items"])
    report["programs"] = sorted(seen)
    out = ROOT / "data" / "captures" / "1parrainage-full-inventory.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"1parrainage blocks={report['blocks_found']} captured={report['captured']} "
        f"unmapped={len(report['unmapped'])} errors={len(report['errors'])}"
    )
    print("programs", report["programs"])
    if report["unmapped"]:
        print("unmapped sample", report["unmapped"][:15])
    print("report", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
