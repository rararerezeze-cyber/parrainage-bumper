#!/usr/bin/env python3
"""Capture publique READ-ONLY de toutes les annonces Parrainage.co (profil Adrien89).

- Re-fetch chaque /offers/{id}
- golden = texte réellement publié (pas offers.json)
- rebuild template/mapping via build_from_text
- orphelins (pas dans offers.json) → data/orphans + needs_canonical_data
- 0 write sur le site
"""
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

PROFILE = "https://parrainage.co/user/adrien89"
BASE = "https://parrainage.co"
ORPHANS_DIR = ROOT / "data" / "orphans" / "parrainage-co"
NCD_PATH = ROOT / "data" / "needs_canonical_data.json"
REPORT_PATH = ROOT / "data" / "captures" / "parrainage-co-capture-report.json"

# Title fragment → program slug (offers lk or orphan key)
TITLE_ALIASES = {
    "boursobank": "boursobank",
    "bourso bank": "boursobank",
    "paypal": "paypal",
    "pay pal": "paypal",
    "revolut": "revolut",
    "trade republic": "traderepublic",
    "traderepublic": "traderepublic",
    "coinbase": "coinbase",
    "binance": "binance",
    "kraken": "kraken",
    "swissborg": "swissborg",
    "bitstack": "bitstack",
    "bybit": "bybit",
    "gemini": "gemini",
    "ledger": "ledger",
    "robinhood": "robinhood",
    "igraal": "igraal",
    "poulpeo": "poulpeo",
    "ebuyclub": "ebuyclub",
    "joko": "joko",
    "widilo": "widilo",
    "betclic": "betclic",
    "unibet": "unibet",
    "winamax": "winamax",
    "totalenergies": "totalenergies",
    "total énergies": "totalenergies",
    "total energies": "totalenergies",
    "acheel": "acheel",
    "vinted": "vinted",
    "plum": "plum",
    "okx": "okx",
    "okex": "okx",
    "nrj mobile": "nrj-mobile",
    "nrj": "nrj-mobile",
    "whatnot": "whatnot",
}


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>|</div>|</li>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trim_site_noise(body: str) -> str:
    # Trailing site chrome / footer (case-insensitive)
    stops = (
        "catégories populaires",
        "categories populaires",
        "se connecter / s'inscrire",
        "alimentation, supermarchés",
        "2 000+",
        "2000+",
        "codes publies",
        "codes publiés",
        "code parrainage ou partagez",
        "partagez le votre",
        "parmi plus de 2 000 marchands",
        "parmi plus de 2000 marchands",
    )
    low = body.lower()
    cut = len(body)
    for stop in stops:
        idx = low.find(stop)
        if 80 < idx < cut:
            cut = idx
    return body[:cut].strip()


def _skip_code_widget_prefix(body: str) -> tuple[str, str | None]:
    """Skip 'AD8RAY\\nCopier' widget after 'Code parrainage Brand'; return body, discrete_code."""
    lines = body.split("\n")
    discrete_code = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        low = s.lower()
        if low in {"copier", "copy", "signaler", "envoyer", "autre", "adrien89"}:
            i += 1
            continue
        if "code/lien" in low or "annonce publi" in low:
            i += 1
            continue
        # short token code (no spaces, not a sentence, not emoji header)
        if (
            3 <= len(s) <= 40
            and " " not in s
            and not s.startswith("http")
            and not re.search(r"[⭐⚡✨🔥⚽⚽️✅➡️]", s)
            and not s.startswith("•")
            and discrete_code is None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-]{2,39}", s)
        ):
            discrete_code = s
            i += 1
            continue
        # start of real body
        break
    return "\n".join(lines[i:]).strip(), discrete_code


def extract_announcement_body(html: str) -> tuple[str | None, str | None]:
    """Extrait (corps, code_widget_site). Corps = texte publié, sans chrome."""
    text = html_to_text(html)
    discrete_code: str | None = None

    # 1) Full Super-Parrain-style block ending with Discord
    for pat in (
        r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+\s*↩️)",
        r"(⚡️ Offre Parrainage[\s\S]*?discord\.gg/\S+\s*↩️)",
        r"(⭐ Offre Parrainage[\s\S]*?discord\.gg/\S+\s*↩️)",
    ):
        m = re.search(pat, text)
        if m:
            for wm in re.finditer(r"(?im)^[ \t]*Code parrainage[^\n]*\n+", text):
                if "partagez" in wm.group(0).lower():
                    continue
                _, discrete_code = _skip_code_widget_prefix(
                    _trim_site_noise(text[wm.end() :])
                )
                if discrete_code:
                    break
            return _trim_site_noise(m.group(1)), discrete_code

    # 2) After each "Code parrainage …" header — keep the best (longest useful) block
    candidates: list[tuple[str, str | None]] = []
    for m in re.finditer(r"(?im)^[ \t]*Code parrainage[^\n]*\n+", text):
        header = m.group(0).lower()
        if "partagez" in header or "marchand" in header:
            continue
        tail = text[m.end() :]
        body = _trim_site_noise(tail)
        body, code = _skip_code_widget_prefix(body)
        body = _trim_site_noise(body)
        if len(body) >= 60:
            candidates.append((body, code))

    if candidates:
        def score(item: tuple[str, str | None]) -> tuple:
            b = item[0]
            return (
                1 if "discord.gg" in b else 0,
                b.count("http"),
                b.count("€"),
                1 if re.search(r"[⭐⚡✨🔥⚽]", b[:120]) else 0,
                len(b),
            )

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    # 3) First strong offer header (emoji) with substance
    best = None
    for m in re.finditer(
        r"(?m)^((?:⭐️|⚡️|⭐|⚽️|🔥|⚡).{8,120})\n([\s\S]{40,5000})",
        text,
    ):
        body = _trim_site_noise(m.group(1) + "\n" + m.group(2))
        if len(body) < 60:
            continue
        if best is None or len(body) > len(best):
            if "Catégories" in body[:80] or "Categories" in body[:80]:
                continue
            best = body
    if best:
        return best, discrete_code

    return None, None


def extract_title_program(html: str) -> tuple[str | None, str | None]:
    """Return (brand_title, program_slug_guess)."""
    m = re.search(
        r"(?i)(?:vous parraine sur|parraine sur)\s+([^<\n→\-]+)",
        html,
    )
    if not m:
        m = re.search(r"<title>\s*Adrien89 vous parraine sur ([^→<\n]+)", html, re.I)
    if not m:
        return None, None
    brand = unescape(m.group(1)).strip()
    brand = re.sub(r"\s+", " ", brand)
    low = brand.lower()
    # strip trailing promo in title
    low_core = re.split(r"\s+→|\s+\(|\s+–|\s+-", low)[0].strip()
    for key, slug in sorted(TITLE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if key in low_core or key in low:
            return brand, slug
    # fuzzy: first word
    first = re.sub(r"[^a-z0-9\-]", "", low_core.split()[0] if low_core else "")
    if first in TITLE_ALIASES:
        return brand, TITLE_ALIASES[first]
    return brand, None


def profile_offer_ids(profile_html: str) -> list[str]:
    ids = list(
        dict.fromkeys(re.findall(r'href="(/offers/(\d+))"', profile_html))
    )
    # re.findall with 2 groups returns tuples
    out = []
    for item in re.findall(r'href="/offers/(\d+)"', profile_html):
        if item not in out:
            out.append(item)
    return out


def load_ncd() -> dict:
    if NCD_PATH.exists():
        return json.loads(NCD_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "items": [], "count": 0}


def upsert_ncd_item(item: dict) -> None:
    data = load_ncd()
    items = data.get("items") or []
    key = (item.get("program_key"), item.get("platform"))
    new_items = [
        x
        for x in items
        if (x.get("program_key"), x.get("platform")) != key
    ]
    new_items.append(item)
    data["items"] = new_items
    data["count"] = len(new_items)
    data["source_profile"] = data.get("source_profile") or PROFILE
    NCD_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def structure_ok(platform: str, program: str, language: str = "fr") -> tuple[bool, str]:
    """render(template, platform_values) == golden  AND  render(template, offers) succeeds.

    Drift offers vs platform is expected; structure only requires golden round-trip.
    """
    from lib.renderer import MappingRepository, Renderer, TemplateRepository

    try:
        mapping = MappingRepository().load(platform, program, language)
        tpl = TemplateRepository()
        golden = tpl.load_golden(platform, program, language)
        template = tpl.load_text(platform, program, language)
        rend = Renderer(OffersRepository())
        # Round-trip with platform historical values (not offers)
        hist = dict(mapping.platform_values or {})
        overrides = {f: hist.get(f) for f in mapping.mutable_fields}
        # If a mutable lacks platform value, fail
        missing = [f for f in mapping.mutable_fields if not overrides.get(f)]
        if missing:
            return False, f"missing_platform_values:{missing}"
        rendered_hist = rend.render(template, mapping, offer=None, overrides=overrides)
        if rendered_hist != golden:
            return False, "golden_roundtrip_fail"
        # Also ensure offers can render (all mutable offer fields present)
        try:
            offer = rend.offers.get_by_slug(program)
            rend.render(template, mapping, offer=offer)
        except Exception as exc:  # noqa: BLE001
            return False, f"offers_render_fail:{exc}"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

def capture_one(
    oid: str,
    offers: OffersRepository,
    *,
    sleep_s: float = 0.35,
) -> dict:
    url = f"{BASE}/offers/{oid}"
    time.sleep(sleep_s)
    html = fetch_text(url)
    brand, slug = extract_title_program(html)
    body, site_code = extract_announcement_body(html)
    row: dict = {
        "offer_id": oid,
        "url": url,
        "brand": brand,
        "program": slug,
        "body_len": len(body or ""),
        "site_code_widget": site_code,
    }
    if not body:
        row["status"] = "NO_BODY"
        raw = html_to_text(html)
        (ROOT / "data" / "captures" / f"parrainage-co-offer-{oid}.txt").write_text(
            raw[:8000], encoding="utf-8"
        )
        return row
    if not slug:
        row["status"] = "UNKNOWN_PROGRAM"
        row["body_preview"] = body[:200]
        return row

    # Known offer?
    try:
        offer = offers.get_by_slug(slug)
        in_offers = True
    except KeyError:
        offer = None
        in_offers = False

    if not in_offers:
        # Orphan / needs_canonical — do not invent offers.json entry
        ORPHANS_DIR.mkdir(parents=True, exist_ok=True)
        golden_rel = f"data/orphans/parrainage-co/{slug}.fr.golden.txt"
        golden_path = ROOT / golden_rel
        golden_path.write_text(body, encoding="utf-8")
        from lib.template_builder import detect_platform_values

        vals, conf, notes = detect_platform_values(body, None)
        if site_code and "personal_code" not in vals:
            vals["personal_code"] = site_code
            conf["personal_code"] = "high"
            notes.append("code extrait du widget site Code parrainage")
        upsert_ncd_item(
            {
                "name": brand or slug,
                "program_key": slug,
                "url": url,
                "platform": "parrainage-co",
                "language": "fr",
                "status": "needs_canonical_data",
                "reason": "aucune entree canonique dans offers.json (pas de creation auto)",
                "golden_file": golden_rel,
                "golden_text": body,
                "detected_values": vals,
                "confidences": conf,
                "notes": notes,
            }
        )
        row["status"] = "NEEDS_CANONICAL"
        row["detected"] = vals
        return row

    # Build from live published text only
    try:
        force = {}
        # Do not force code into body if absent; widget code is platform UI field
        result = build_from_text(
            platform="parrainage-co",
            program=slug,
            language="fr",
            golden_text=body,
            offer=offer,
            announcement_url=url,
            force_values=force or None,
        )
        # Note site code widget separately (platform field, may equal personal_code)
        if site_code:
            result.notes.append(f"site_code_widget={site_code}")
        # If offer missing code/link, ensure we didn't mark them mutable without offer value
        # build_from_text only marks values present in text; render uses offers.
        # Drop mutable fields where offer value is null so structure render works.
        cleaned_mutable = []
        for f in result.mutable_fields:
            ofield = {
                "personal_code": "code",
                "personal_link": "link",
                "referee_reward": "reward",
            }.get(f)
            if ofield is None:
                cleaned_mutable.append(f)
                continue
            ov = offer.get(ofield)
            if ov is None or str(ov).strip() == "":
                # Keep platform baseline as immutable literal in template
                marker = {
                    "personal_code": "{{PERSONAL_CODE}}",
                    "personal_link": "{{PERSONAL_LINK}}",
                    "referee_reward": "{{REFEREE_REWARD}}",
                }[f]
                if marker in result.template and f in result.platform_values:
                    result.template = result.template.replace(
                        marker, result.platform_values[f]
                    )
                result.notes.append(
                    f"{f}: offers.json absent — laisse immutable dans le template"
                )
                continue
            cleaned_mutable.append(f)
        result.mutable_fields = cleaned_mutable
        result.platform_values = {
            k: v for k, v in result.platform_values.items() if k in cleaned_mutable
        }
        result.confidences = {
            k: v for k, v in result.confidences.items() if k in cleaned_mutable
        }
        # Re-check roundtrip after immutable restore
        roundtrip = result.template
        for field_name in result.mutable_fields:
            from lib.template_builder import DEFAULT_MARKERS

            roundtrip = roundtrip.replace(
                DEFAULT_MARKERS[field_name], result.platform_values[field_name]
            )
        if roundtrip != result.golden:
            # Fall back: no mutables, golden-only
            result.mutable_fields = []
            result.template = result.golden
            result.platform_values = {}
            result.confidences = {}
            result.notes.append("roundtrip fail after null-offer prune — golden immutable only")

        paths = write_build_result(result)
        # quality note
        mpath = paths["mapping"]
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["quality"] = "public_refetch"
        data["notes"] = "; ".join(result.notes) if result.notes else data.get("notes")
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        ok, reason = structure_ok("parrainage-co", slug)
        row["status"] = "OK" if ok else "STRUCTURE_FAIL"
        row["structure_reason"] = reason
        row["mutable_fields"] = result.mutable_fields
        row["paths"] = {k: str(v) for k, v in paths.items()}
    except Exception as exc:  # noqa: BLE001
        row["status"] = "BUILD_FAIL"
        row["error"] = str(exc)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--only", nargs="*", help="offer ids only")
    args = ap.parse_args()

    offers = OffersRepository()
    print(f"Fetch profile {PROFILE}")
    profile = fetch_text(PROFILE)
    (ROOT / "data/captures/parrainage-co-public.html").write_text(profile, encoding="utf-8")
    ids = profile_offer_ids(profile)
    if args.only:
        ids = [i for i in ids if i in args.only] or args.only
    print(f"offers_on_profile={len(ids)}")

    rows = []
    for i, oid in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] offer {oid} …", end=" ", flush=True)
        try:
            row = capture_one(oid, offers, sleep_s=args.sleep)
        except Exception as exc:  # noqa: BLE001
            row = {"offer_id": oid, "status": "FETCH_FAIL", "error": str(exc)}
        rows.append(row)
        print(row.get("program"), row.get("status"), row.get("mutable_fields") or row.get("error") or "")

    # Summary structure check for all known mappings after capture
    from lib.inventory import list_mapping_refs

    mapped = [r for r in list_mapping_refs() if r.platform == "parrainage-co"]
    struct = []
    for r in mapped:
        ok, reason = structure_ok(r.platform, r.program, r.language)
        struct.append({"program": r.program, "ok": ok, "reason": reason})

    report = {
        "platform": "parrainage-co",
        "mode": "PUBLIC_RECAPTURE_READ_ONLY",
        "profile": PROFILE,
        "profile_offer_count": len(ids),
        "captured": rows,
        "status_counts": {},
        "mappings_after": len(mapped),
        "structure_checks": struct,
        "structure_ok_count": sum(1 for s in struct if s["ok"]),
        "structure_fail": [s for s in struct if not s["ok"]],
    }
    for row in rows:
        st = row.get("status") or "?"
        report["status_counts"][st] = report["status_counts"].get(st, 0) + 1

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n=== SUMMARY ===")
    print("status_counts", report["status_counts"])
    print("structure_ok", report["structure_ok_count"], "/", report["mappings_after"])
    if report["structure_fail"]:
        print("structure_fail:")
        for s in report["structure_fail"]:
            print(" ", s["program"], s["reason"][:100])
    print("report", REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
