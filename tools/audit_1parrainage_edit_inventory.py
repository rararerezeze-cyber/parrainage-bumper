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
DEFAULT_OUT = ROOT / "diagnostic-artifacts/1parrainage-edit-inventory.json"
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
