#!/usr/bin/env python3
"""Capture publique multi-plateformes (sans credentials)."""
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
sys.path.insert(0, str(ROOT / "tools"))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
from lib.template_builder import build_from_text, detect_platform_values, write_build_result
import capture_super_parrain as csp

REPORT_DIR = ROOT / "data" / "captures"


def _slug_match(text: str, offers: OffersRepository) -> str | None:
    low = text.lower()
    best = None
    best_len = 0
    for o in offers.load_all():
        name = (o.get("name") or "").strip()
        lk = o.get("lk") or ""
        if name and name.lower() in low and len(name) > best_len:
            best = lk
            best_len = len(name)
        if lk and re.search(rf"\b{re.escape(lk)}\b", low) and len(lk) > best_len:
            best = lk
            best_len = len(lk)
    return best


def _save(platform: str, program: str, language: str, text: str, url: str | None, offer, extra_notes: str = "") -> dict:
    result = build_from_text(
        platform=platform,
        program=program,
        language=language,
        golden_text=text,
        offer=offer,
        announcement_url=url,
    )
    if extra_notes:
        result.notes.append(extra_notes)
    paths = write_build_result(result)
    # patch notes on mapping
    mpath = paths["mapping"]
    data = json.loads(mpath.read_text(encoding="utf-8"))
    if extra_notes:
        data["notes"] = ((data.get("notes") or "") + "; " + extra_notes).strip("; ")
    if "1parrainage" in platform:
        data["style_policy"] = "native_platform_style_only"
        data["notes"] = (data.get("notes") or "") + "; preserve native 1parrainage typography/emojis as-is"
    mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "program": program,
        "status": "ok",
        "mutable": result.mutable_fields,
        "chars": len(text),
        "url": url,
    }


# ---------- Super-Parrain (refresh + orphans) ----------
def capture_super_parrain(offers: OffersRepository) -> dict:
    # reuse existing tool logic via inventory + capture
    from tools.inventory_needs_canonical import main as ncd_main  # type: ignore

    # Run bulk capture
    import subprocess

    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "capture_super_parrain.py"), "--sleep", "0.3"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "inventory_needs_canonical.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return {
        "platform": "super-parrain",
        "stdout_tail": (r.stdout or "")[-1500:],
        "returncode": r.returncode,
    }


# ---------- 1Parrainage ----------
def capture_1parrainage(offers: OffersRepository) -> dict:
    url = "https://www.1parrainage.com/listeannonces_98906_Adrien89.php"
    report = {"platform": "1parrainage", "items": [], "errors": []}
    try:
        html = fetch_text(url)
    except Exception as exc:
        report["errors"].append(str(exc))
        return report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "1parrainage-list.html").write_text(html, encoding="utf-8")

    # Collect announcement detail links
    links = re.findall(r'href=["\']([^"\']*(?:annonce|detail|offre|parrain)[^"\']*)["\']', html, re.I)
    links += re.findall(r'href=["\']([^"\']+\.php[^"\']*)["\']', html, re.I)
    abs_links = []
    seen = set()
    for href in links:
        if href.startswith("#") or "listeannonces" in href or "login" in href.lower():
            continue
        full = urljoin(url, href)
        if "1parrainage.com" not in full:
            continue
        if full in seen:
            continue
        seen.add(full)
        abs_links.append(full)

    # Also split list page into text blocks if few detail links
    list_text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    list_text = re.sub(r"<style[\s\S]*?</style>", " ", list_text, flags=re.I)
    list_text = re.sub(r"<br\s*/?>", "\n", list_text, flags=re.I)
    list_text = re.sub(r"</p>|</div>|</tr>|</li>", "\n", list_text, flags=re.I)
    list_text = re.sub(r"<[^>]+>", " ", list_text)
    list_text = unescape(list_text)
    list_text = re.sub(r"[ \t]+", " ", list_text)
    list_text = re.sub(r"\n{3,}", "\n\n", list_text)
    (REPORT_DIR / "1parrainage-list.txt").write_text(list_text, encoding="utf-8")

    # Try detail pages first
    for link in abs_links[:80]:
        time.sleep(0.35)
        try:
            page = fetch_text(link)
        except Exception:
            continue
        # extract main content
        m = re.search(
            r'(?is)<(?:td|div|p|article)[^>]{0,80}(?:class|id)=["\'][^"\']*(?:annonce|descr|content|message|texte)[^"\']*["\'][^>]*>(.*?)</(?:td|div|p|article)>',
            page,
        )
        if m:
            frag = m.group(1)
            body = re.sub(r"<br\s*/?>", "\n", frag, flags=re.I)
            body = re.sub(r"<[^>]+>", "", body)
            body = unescape(body).strip()
        else:
            body = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", page)
            body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            body = unescape(body)
            body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()
            # take a middle chunk if huge
            if len(body) > 4000:
                body = body[:4000]
        if len(body) < 40:
            continue
        slug = _slug_match(body, offers)
        if not slug:
            continue
        # skip duplicates
        if any(i.get("program") == slug for i in report["items"]):
            continue
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            item = _save(
                "1parrainage",
                slug,
                "fr",
                body,
                link,
                offer,
                extra_notes="native 1parrainage style; do not force Super-Parrain emojis",
            )
            report["items"].append(item)
        except Exception as exc:
            report["errors"].append({"program": slug, "error": str(exc)})

    # Fallback: split list_text by common offer headers
    if len(report["items"]) < 3:
        chunks = re.split(r"\n(?=(?:Parrainage|Offre|Code|BONUS|\*))", list_text)
        for ch in chunks:
            ch = ch.strip()
            if len(ch) < 50:
                continue
            slug = _slug_match(ch, offers)
            if not slug or any(i.get("program") == slug for i in report["items"]):
                continue
            try:
                offer = offers.get_by_slug(slug)
            except KeyError:
                offer = None
            try:
                item = _save(
                    "1parrainage",
                    slug,
                    "fr",
                    ch[:3000],
                    url,
                    offer,
                    extra_notes="from list page fallback; native style",
                )
                report["items"].append(item)
            except Exception as exc:
                report["errors"].append({"program": slug, "error": str(exc)})

    return report


# ---------- ReferralDrop public ----------
def capture_referraldrop(offers: OffersRepository) -> dict:
    url = "https://referraldrop.com/en/user/TheSuperPenguins"
    report = {
        "platform": "referraldrop",
        "items": [],
        "errors": [],
        "auth_status": "AUTH_BLOCKED_GOOGLE",
    }
    try:
        html = fetch_text(url)
    except Exception as exc:
        report["errors"].append(str(exc))
        return report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "referraldrop-profile.html").write_text(html, encoding="utf-8")

    # Extract cards / links
    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    detail_links = []
    for href in links:
        full = urljoin(url, href)
        if "referraldrop.com" not in full:
            continue
        if any(x in full for x in ("/drop/", "/referral/", "/code/", "/offer/", "/en/")):
            if full.rstrip("/") != url.rstrip("/"):
                detail_links.append(full)
    detail_links = list(dict.fromkeys(detail_links))[:100]

    # Text blocks from profile
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text))
    (REPORT_DIR / "referraldrop-profile.txt").write_text(text, encoding="utf-8")

    for link in detail_links:
        time.sleep(0.3)
        try:
            page = fetch_text(link)
        except Exception:
            continue
        body = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", page)
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", "\n", body)
        body = unescape(body)
        body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()
        if len(body) > 5000:
            body = body[:5000]
        if len(body) < 40:
            continue
        slug = _slug_match(body[:800], offers)
        if not slug or any(i.get("program") == slug for i in report["items"]):
            continue
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            item = _save(
                "referraldrop",
                slug,
                "en",
                body,
                link,
                offer,
                extra_notes="public capture only; AUTH_BLOCKED_GOOGLE",
            )
            # force MANUAL sync mode
            mpath = ROOT / "data" / "platform-mappings" / f"referraldrop.{slug}.en.json"
            if mpath.exists():
                d = json.loads(mpath.read_text(encoding="utf-8"))
                d["sync_mode"] = "manual_review_required"
                d["auth_status"] = "AUTH_BLOCKED_GOOGLE"
                mpath.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report["items"].append(item)
        except Exception as exc:
            report["errors"].append({"program": slug, "error": str(exc)})

    # Fallback split profile text by offer names
    if len(report["items"]) < 2:
        for o in offers.load_all():
            name = o.get("name") or ""
            lk = o.get("lk")
            if not name or not lk:
                continue
            if name.lower() not in text.lower():
                continue
            if any(i.get("program") == lk for i in report["items"]):
                continue
            # extract window around name
            idx = text.lower().find(name.lower())
            chunk = text[max(0, idx - 50) : idx + 600].strip()
            if len(chunk) < 40:
                continue
            try:
                item = _save(
                    "referraldrop",
                    lk,
                    "en",
                    chunk,
                    url,
                    o,
                    extra_notes="public profile window; AUTH_BLOCKED_GOOGLE",
                )
                mpath = ROOT / "data" / "platform-mappings" / f"referraldrop.{lk}.en.json"
                if mpath.exists():
                    d = json.loads(mpath.read_text(encoding="utf-8"))
                    d["sync_mode"] = "manual_review_required"
                    d["auth_status"] = "AUTH_BLOCKED_GOOGLE"
                    mpath.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                report["items"].append(item)
            except Exception as exc:
                report["errors"].append({"program": lk, "error": str(exc)})

    return report


# ---------- ReferralCodes.com public ----------
def capture_referralcodes_public(offers: OffersRepository) -> dict:
    url = "https://referralcodes.com/TheSuperReff"
    report = {
        "platform": "referralcodes",
        "items": [],
        "errors": [],
        "prefer_official_import": True,
    }
    try:
        html = fetch_text(url)
    except Exception as exc:
        report["errors"].append(str(exc))
        return report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "referralcodes-TheSuperReff.html").write_text(html, encoding="utf-8")

    # Reuse Livewire extraction ideas from capture_referralcodes
    brands = []
    for m in re.finditer(r"wire:initial-data=\"([^\"]+)\"", html):
        raw = unescape(m.group(1))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        memo = data.get("serverMemo", {}).get("data", {})
        if "trustedBrands" in memo:
            brands.extend(memo["trustedBrands"])
        brand = memo.get("brand")
        if isinstance(brand, dict):
            brands.append(brand)
    # shop slugs in HTML entities
    for m in re.finditer(r"&quot;shop_slug&quot;:&quot;([^&]+)&quot;", html):
        slug = m.group(1)
        window = html[max(0, m.start() - 400) : m.start() + 1200]

        def grab(key: str) -> str | None:
            mm = re.search(rf"&quot;{key}&quot;:&quot;([^&]*)&quot;", window)
            return unescape(mm.group(1).replace("\\/", "/")) if mm else None

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

    # de-dupe
    seen = set()
    uniq = []
    for b in brands:
        key = (b.get("shop_slug"), b.get("referral_url"), b.get("shop_name"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)

    for brand in uniq:
        name = brand.get("shop_name") or brand.get("shop_slug") or ""
        slug = _slug_match(name, offers)
        if not slug:
            s = (brand.get("shop_slug") or "").replace("-referral-code", "").replace("-referral", "")
            slug = _slug_match(s.replace("-", " "), offers)
        if not slug:
            continue
        lines = [f"ReferralCodes — {name}"]
        if brand.get("referral_discount"):
            lines.append(f"Bonus: {brand['referral_discount']}")
        if brand.get("referral_description"):
            lines.append(str(brand["referral_description"]))
        if brand.get("referral_code"):
            lines.append(f"Code: {brand['referral_code']}")
        if brand.get("referral_url"):
            lines.append(f"Link: {brand['referral_url']}")
        text = "\n".join(lines)
        if len(text) < 20:
            continue
        force = {}
        if brand.get("referral_code"):
            force["personal_code"] = str(brand["referral_code"])
        if brand.get("referral_url"):
            force["personal_link"] = str(brand["referral_url"])
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            result = build_from_text(
                platform="referralcodes",
                program=slug,
                language="en",
                golden_text=text,
                offer=offer,
                announcement_url=url,
                force_values=force or None,
            )
            result.sync_mode = "manual_review_required"
            paths = write_build_result(result)
            mpath = paths["mapping"]
            d = json.loads(mpath.read_text(encoding="utf-8"))
            d["sync_mode"] = "manual_review_required"
            d["prefer_official_import"] = True
            d["notes"] = (d.get("notes") or "") + "; public capture TheSuperReff; prefer Agent Import for write"
            mpath.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report["items"].append({"program": slug, "status": "ok", "mutable": result.mutable_fields})
        except Exception as exc:
            report["errors"].append({"program": slug, "error": str(exc)})

    report["brands_raw"] = len(uniq)
    return report


# ---------- ReferralCode.tv public author ----------
def capture_referralcode_tv_public(offers: OffersRepository) -> dict:
    url = "https://www.referralcode.tv/author/thesuperreff/"
    report = {"platform": "referralcode-tv", "items": [], "errors": []}
    try:
        html = fetch_text(url, timeout=60)
    except Exception as exc:
        report["errors"].append(str(exc))
        return report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "referralcode-tv-author.html").write_text(html, encoding="utf-8")

    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    posts = []
    for href in links:
        full = urljoin(url, href)
        if "referralcode.tv" not in full:
            continue
        if any(x in full for x in ("/author/", "/wp-", "/login", "/cart", "#")):
            continue
        if re.search(r"/\d{4}/|/listing|/job|/referral|/code", full) or full.count("/") >= 4:
            posts.append(full)
    posts = list(dict.fromkeys(posts))[:80]
    report["detail_links"] = len(posts)

    for link in posts:
        time.sleep(0.4)
        try:
            page = fetch_text(link, timeout=45)
        except Exception as exc:
            report["errors"].append({"url": link, "error": str(exc)})
            continue
        # extract article
        m = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
        frag = m.group(1) if m else page
        body = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", frag)
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", "\n", body)
        body = unescape(body)
        body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()
        if len(body) > 6000:
            body = body[:6000]
        if len(body) < 50:
            continue
        slug = _slug_match(body[:1000], offers)
        if not slug or any(i.get("program") == slug for i in report["items"]):
            continue
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            item = _save(
                "referralcode-tv",
                slug,
                "en",
                body,
                link,
                offer,
                extra_notes="public author capture; distinct from referralcodes.com",
            )
            report["items"].append(item)
        except Exception as exc:
            report["errors"].append({"program": slug, "error": str(exc)})

    return report


# ---------- Parrainage.co public profile ----------
def capture_parrainage_co_public(offers: OffersRepository) -> dict:
    url = "https://parrainage.co/user/adrien89"
    report = {"platform": "parrainage-co", "items": [], "errors": [], "mode": "public_profile"}
    try:
        html = fetch_text(url)
    except Exception as exc:
        report["errors"].append(str(exc))
        return report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "parrainage-co-public.html").write_text(html, encoding="utf-8")
    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    annonces = []
    for href in links:
        full = urljoin(url, href)
        if "parrainage.co" not in full:
            continue
        if any(x in full for x in ("/offer", "/annonce", "/parrainage", "/code")):
            annonces.append(full)
    annonces = list(dict.fromkeys(annonces))[:60]
    report["links"] = len(annonces)
    for link in annonces:
        time.sleep(0.3)
        try:
            page = fetch_text(link)
        except Exception:
            continue
        body = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", page)
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", "\n", body)
        body = unescape(body)
        body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()
        if len(body) > 5000:
            body = body[:5000]
        if len(body) < 60:
            continue
        slug = _slug_match(body[:900], offers)
        if not slug:
            continue
        # Prefer existing detailed auth capture if longer
        existing = ROOT / "data" / "platform-templates" / "parrainage-co" / f"{slug}.fr.golden.txt"
        if existing.exists() and existing.stat().st_size > len(body.encode("utf-8")):
            continue
        try:
            offer = offers.get_by_slug(slug)
        except KeyError:
            offer = None
        try:
            item = _save("parrainage-co", slug, "fr", body, link, offer, extra_notes="public profile")
            report["items"].append(item)
        except Exception as exc:
            report["errors"].append({"program": slug, "error": str(exc)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default="all",
        help="comma list: super-parrain,1parrainage,referraldrop,referralcodes,referralcode-tv,parrainage-co,all",
    )
    args = parser.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if "all" in only:
        only = {
            "super-parrain",
            "1parrainage",
            "referraldrop",
            "referralcodes",
            "referralcode-tv",
            "parrainage-co",
        }

    offers = OffersRepository()
    summary = {}
    if "super-parrain" in only:
        print("=== super-parrain ===")
        summary["super-parrain"] = capture_super_parrain(offers)
        print(summary["super-parrain"].get("stdout_tail", "")[-500:])
    if "parrainage-co" in only:
        print("=== parrainage-co public ===")
        summary["parrainage-co"] = capture_parrainage_co_public(offers)
        print("items", len(summary["parrainage-co"].get("items") or []), "errors", summary["parrainage-co"].get("errors"))
    if "1parrainage" in only:
        print("=== 1parrainage ===")
        summary["1parrainage"] = capture_1parrainage(offers)
        print("items", len(summary["1parrainage"].get("items") or []), "errors", summary["1parrainage"].get("errors"))
    if "referraldrop" in only:
        print("=== referraldrop ===")
        summary["referraldrop"] = capture_referraldrop(offers)
        print("items", len(summary["referraldrop"].get("items") or []), "auth", summary["referraldrop"].get("auth_status"))
    if "referralcodes" in only:
        print("=== referralcodes.com ===")
        summary["referralcodes"] = capture_referralcodes_public(offers)
        print("items", len(summary["referralcodes"].get("items") or []), "brands", summary["referralcodes"].get("brands_raw"))
    if "referralcode-tv" in only:
        print("=== referralcode.tv ===")
        summary["referralcode-tv"] = capture_referralcode_tv_public(offers)
        print("items", len(summary["referralcode-tv"].get("items") or []), "links", summary["referralcode-tv"].get("detail_links"))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "onboard-public-report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("report", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
