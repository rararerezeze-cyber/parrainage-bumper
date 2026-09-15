#!/usr/bin/env python3
"""Read-only authenticated inventory for every 1Parrainage announcement.

Purpose:
- enumerate every internal /espace_parrain/parrainages/edit/<id>/ page;
- fetch every public full-detail body from the already-known occurrence list;
- match editor pages to public programs by normalized body similarity;
- produce a reusable program -> edit_urls/public_offer_ids map.

ABSOLUTE: no submit, no click on Envoyer/Boost/Remonter, no form POST after login.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text
from platforms.oneparrainage.writer import (
    BASE,
    _bumper,
    _cfg,
    _ck_get,
    _ck_ready,
    _detect_challenge,
    _extract_public_detail_block,
    _login,
)

OCCURRENCES = ROOT / "data/platform-occurrences/1parrainage.json"
MAPPINGS = ROOT / "data/platform-mappings"
TEMPLATES = ROOT / "data/platform-templates/1parrainage"
DEFAULT_OUT = ROOT / "diagnostic-artifacts/1parrainage-edit-inventory.json"
DEFAULT_INDEX_OUT = ROOT / "diagnostic-artifacts/1parrainage-edit-index.json"
EDIT_RE = re.compile(r"/espace_parrain/parrainages/edit/(\d+)/?", re.I)
DISCOVERY_STARTS = (
    f"{BASE}/espace_parrain/",
    f"{BASE}/espace_parrain/parrainages/",
    f"{BASE}/espace_parrain/parrainages",
)


def _plain(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _same_member_page(url: str) -> bool:
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != "www.1parrainage.com":
        return False
    path = p.path.lower()
    if not path.startswith("/espace_parrain/"):
        return False
    if any(x in path for x in ("/logout", "/profile/", "/messagerie")):
        return False
    return True


def _public_occurrences() -> list[dict]:
    data = json.loads(OCCURRENCES.read_text(encoding="utf-8"))
    out = []
    for program, rows in (data.get("programs") or {}).items():
        for row in rows:
            oid = str(row.get("offer_id") or "").strip()
            if not oid:
                continue
            url = f"{BASE}/detail_parrain.php?par=98906&offre={oid}"
            try:
                html = fetch_text(url)
                block = _extract_public_detail_block(html)
                plain = _plain(block)
                if not plain:
                    raise RuntimeError("empty public detail")
                out.append({
                    "program": program,
                    "offer_id": oid,
                    "brand": row.get("brand"),
                    "detail_url": url,
                    "body": block,
                    "plain": plain,
                    "chars": len(plain),
                })
            except Exception as exc:  # noqa: BLE001
                out.append({
                    "program": program,
                    "offer_id": oid,
                    "brand": row.get("brand"),
                    "detail_url": url,
                    "error": str(exc),
                    "plain": "",
                    "chars": 0,
                })
    return out


async def _links(page) -> list[str]:
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]'))
          .map(a => a.href).filter(Boolean)"""
    )


async def _discover_edit_urls(page) -> tuple[list[str], list[str]]:
    queue = list(DISCOVERY_STARTS)
    seen_pages: set[str] = set()
    edits: set[str] = set()
    crawled: list[str] = []

    while queue and len(seen_pages) < 40:
        url = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await _detect_challenge(page)
            if "/login" in page.url:
                continue
            crawled.append(page.url)
            for href in await _links(page):
                m = EDIT_RE.search(href)
                if m:
                    edits.add(f"{BASE}/espace_parrain/parrainages/edit/{m.group(1)}/")
                    continue
                if _same_member_page(href):
                    low = href.lower()
                    if any(k in low for k in ("parrainage", "annonce", "offre")) and href not in seen_pages:
                        queue.append(href)
        except Exception:
            continue

    return sorted(edits), crawled


async def _capture_edit(page, url: str) -> dict:
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await _detect_challenge(page)
    if "/login" in page.url:
        raise RuntimeError("session_lost")
    if not await _ck_ready(page):
        raise RuntimeError("ckeditor_not_ready")
    body = await _ck_get(page)
    if not body:
        raise RuntimeError("empty_ckeditor_body")
    title = await page.title()
    visible = await page.locator("body").inner_text()
    return {
        "edit_url": page.url,
        "internal_id": (EDIT_RE.search(page.url).group(1) if EDIT_RE.search(page.url) else None),
        "title": title,
        "visible_excerpt": " ".join(visible.split())[:1000],
        "body": body,
        "plain": _plain(body),
    }


def _match(editor: dict, public_rows: list[dict]) -> dict:
    text = editor.get("plain") or ""
    scored = []
    for row in public_rows:
        candidate = row.get("plain") or ""
        if not candidate:
            continue
        ratio = SequenceMatcher(None, text, candidate, autojunk=False).ratio()
        # Exact containment is common when one side has platform chrome.
        if text and candidate and (text in candidate or candidate in text):
            ratio = max(ratio, min(len(text), len(candidate)) / max(len(text), len(candidate)))
        scored.append((ratio, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0] if scored else (0.0, {})
    second = scored[1] if len(scored) > 1 else (0.0, {})
    return {
        "program": best[1].get("program"),
        "offer_id": best[1].get("offer_id"),
        "score": round(best[0], 6),
        "second_program": second[1].get("program"),
        "second_offer_id": second[1].get("offer_id"),
        "second_score": round(second[0], 6),
        "margin": round(best[0] - second[0], 6),
        "confident": bool(best[0] >= 0.70 and (best[0] - second[0] >= 0.03 or best[1].get("program") == second[1].get("program"))),
    }


def _mapping_policy(program: str, editor_bodies: list[str], public_offer_ids: list[str]) -> dict:
    path = MAPPINGS / f"1parrainage.{program}.fr.json"
    template_path = TEMPLATES / f"{program}.fr.txt"
    if not path.exists() or not template_path.exists():
        return {
            "mapping_exists": path.exists(),
            "template_exists": template_path.exists(),
            "live_replace_ready": False,
            "reason": "mapping_or_template_missing",
        }
    mapping = json.loads(path.read_text(encoding="utf-8"))
    template = template_path.read_text(encoding="utf-8")
    markers = mapping.get("markers") or {}
    mutable = list(mapping.get("mutable_fields") or [])
    values = dict(mapping.get("platform_values") or {})
    declared_live_counts = {
        str(k): int(v)
        for k, v in (mapping.get("live_marker_counts") or {}).items()
        if isinstance(v, (int, float)) and int(v) > 0
    }
    marker_counts = {}
    field_checks = {}
    for field in mutable:
        marker = str(markers.get(field) or "")
        old = values.get(field)
        # Prefer an explicitly audited live CKEditor span count. This is used
        # only when the historical public-list template was truncated. The
        # live writer revalidates the exact count against the editor body
        # immediately before Save, so this cannot turn a stale template into
        # blind/global replacement.
        count = declared_live_counts.get(field, template.count(marker) if marker else 0)
        marker_counts[field] = count
        checks = []
        for body in editor_bodies:
            visible = _plain(body)
            checks.append({
                "old_value_present": bool(old is not None and str(old).strip() and str(old).lower() in visible),
                "visible_occurrences": visible.count(str(old).lower()) if old is not None and str(old).strip() else 0,
            })
        field_checks[field] = {
            "old_value": old,
            "marker_count": count,
            "editors": checks,
            "ready": bool(count > 0 and old is not None and str(old).strip() and all(c["visible_occurrences"] >= count for c in checks)),
        }
    return {
        "mapping_exists": True,
        "template_exists": True,
        "mutable_fields": mutable,
        "platform_values": values,
        "marker_counts": marker_counts,
        "field_checks": field_checks,
        "public_offer_ids": public_offer_ids,
        "live_replace_ready": bool(mutable and all(v["ready"] for v in field_checks.values())),
        "reason": None if mutable and all(v["ready"] for v in field_checks.values()) else (
            "no_mutable_fields" if not mutable else "marker_or_live_value_not_proven"
        ),
    }


async def run(out: Path) -> dict:
    public_rows = _public_occurrences()
    if not os.environ.get("ONEPARRAINAGE_EMAIL") or not os.environ.get("ONEPARRAINAGE_PASSWORD"):
        raise RuntimeError("ONEPARRAINAGE_EMAIL/PASSWORD missing")

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = context = page = None
    editors = []
    crawled = []
    try:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"])
        context = await _bumper().new_context(browser)
        page = await context.new_page()
        await _login(page, _cfg())
        edit_urls, crawled = await _discover_edit_urls(page)
        for url in edit_urls:
            try:
                item = await _capture_edit(page, url)
                item["match"] = _match(item, public_rows)
                editors.append(item)
            except Exception as exc:  # noqa: BLE001
                editors.append({"edit_url": url, "error": str(exc), "match": {"confident": False}})
    finally:
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        await pw.stop()

    programs: dict[str, dict] = {}
    for item in editors:
        m = item.get("match") or {}
        if not m.get("confident") or not m.get("program"):
            continue
        p = programs.setdefault(m["program"], {"edit_urls": [], "internal_ids": [], "matched_public_offer_ids": [], "scores": []})
        p["edit_urls"].append(item["edit_url"])
        if item.get("internal_id"):
            p["internal_ids"].append(item["internal_id"])
        if m.get("offer_id"):
            p["matched_public_offer_ids"].append(m["offer_id"])
        p["scores"].append(m.get("score"))

    # Enrich each resolved program with exact public occurrence IDs and a
    # conservative live-replacement readiness policy derived from the current
    # mapping/template. This does not alter the account.
    occurrence_data = json.loads(OCCURRENCES.read_text(encoding="utf-8"))
    for program, info in programs.items():
        public_offer_ids = [
            str(x.get("offer_id"))
            for x in (occurrence_data.get("programs") or {}).get(program, [])
            if x.get("offer_id")
        ]
        editor_bodies = [
            e.get("body") or ""
            for e in editors
            if (e.get("match") or {}).get("confident")
            and (e.get("match") or {}).get("program") == program
        ]
        info["public_offer_ids"] = public_offer_ids
        info["policy"] = _mapping_policy(program, editor_bodies, public_offer_ids)

    public_programs = sorted({r["program"] for r in public_rows if r.get("plain")})
    resolved_programs = sorted(programs)
    unresolved_public_programs = sorted(set(public_programs) - set(resolved_programs))
    ambiguous_editors = [
        {
            "edit_url": e.get("edit_url"),
            "internal_id": e.get("internal_id"),
            "error": e.get("error"),
            "match": e.get("match"),
        }
        for e in editors
        if not (e.get("match") or {}).get("confident")
    ]

    report = {
        "schema_version": 1,
        "platform": "1parrainage",
        "mode": "authenticated_inventory_read_only_no_submit",
        "platform_writes": 0,
        "public_occurrences_total": len(public_rows),
        "public_occurrences_ok": sum(1 for r in public_rows if r.get("plain")),
        "edit_urls_found": len(editors),
        "resolved_program_count": len(resolved_programs),
        "public_program_count": len(public_programs),
        "resolved_programs": resolved_programs,
        "unresolved_public_programs": unresolved_public_programs,
        "ambiguous_editors": ambiguous_editors,
        "programs": programs,
        "crawled_member_pages": crawled,
        "editors": editors,
        "public": public_rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    index = {
        "schema_version": 1,
        "platform": "1parrainage",
        "source": "authenticated_read_only_inventory",
        "platform_writes": 0,
        "program_count": len(programs),
        "public_occurrences_total": len(public_rows),
        "edit_urls_found": len(editors),
        "unresolved_public_programs": unresolved_public_programs,
        "ambiguous_editors": ambiguous_editors,
        "programs": programs,
    }
    DEFAULT_INDEX_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_INDEX_OUT.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.output))
        print(json.dumps({
            "platform_writes": report["platform_writes"],
            "public_occurrences_total": report["public_occurrences_total"],
            "public_occurrences_ok": report["public_occurrences_ok"],
            "edit_urls_found": report["edit_urls_found"],
            "resolved_program_count": report["resolved_program_count"],
            "public_program_count": report["public_program_count"],
            "unresolved_public_programs": report["unresolved_public_programs"],
            "ambiguous_editor_count": len(report["ambiguous_editors"]),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"platform_writes": 0, "error": str(exc)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"inventory_failed={type(exc).__name__}:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
