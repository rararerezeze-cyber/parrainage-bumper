#!/usr/bin/env python3
"""Read-only diagnosis of the final 1Parrainage headless-canary gaps.

The probe reads the public Kraken list/detail pages and, when credentials are
available, the authenticated CKEditor value.  It may call ``CKEDITOR.setData``
with the value already loaded in the editor to measure browser-side
normalization, but it never submits the edit form and never saves anything.

The JSON artifact deliberately excludes the account body, cookies, storage,
credentials, and tokens.  It retains only hashes, lengths, structural newline
facts, identity booleans, and the first differing code point.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from platforms.oneparrainage.writer import (  # noqa: E402
    _bumper,
    _cfg,
    _ck_get,
    _ck_ready,
    _detect_challenge,
    _login,
    build_write_plan,
)

PLATFORM = "1parrainage"
PROGRAM = "kraken"
LANGUAGE = "fr"
EXPECTED_CODE = "cpbrgddy"
EXPECTED_LINK = "https://invite.kraken.com/JDNW/s5qudqe4"
EXPECTED_REWARD = "200 € en cryptomonnaies"
DEFAULT_OUTPUT = ROOT / "diagnostic-artifacts" / "1parrainage-final-gaps.json"
ALLOWED_HOST = "www.1parrainage.com"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain_html(value: str) -> str:
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _identity(value: str) -> dict[str, bool]:
    plain = _plain_html(value)
    return {
        "code_present": EXPECTED_CODE in plain,
        "link_present": EXPECTED_LINK in plain,
        "reward_present": EXPECTED_REWARD in plain,
    }


def _identity_ok(checks: dict[str, bool]) -> bool:
    return all(checks.values())


def _assert_public_1p_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != ALLOWED_HOST:
        raise RuntimeError(f"unexpected public detail host: {parts.hostname!r}")
    return url


def _extract_bridge_url(list_html: str, list_url: str, offer_id: str) -> str:
    match = re.search(
        rf"pr_open_window\(\s*['\"]([^'\"]*parrain_definit\.php\?"
        rf"id_par=\d+&(?:amp;)?id={re.escape(offer_id)})['\"]",
        list_html,
        flags=re.I,
    )
    if not match:
        raise RuntimeError("public read-more bridge not found for target offer")
    return _assert_public_1p_url(urljoin(list_url, unescape(match.group(1))))


def _extract_detail_url(bridge_html: str, bridge_url: str) -> str:
    match = re.search(
        r"<iframe\b(?=[^>]*\bid=['\"]offreDetail['\"])[^>]*\bsrc=['\"]([^'\"]+)['\"]",
        bridge_html,
        flags=re.I,
    )
    if not match:
        raise RuntimeError("public full-content iframe not found")
    return _assert_public_1p_url(urljoin(bridge_url, unescape(match.group(1))))


def _extract_detail_block(detail_html: str) -> str:
    match = re.search(
        r"<div\b(?=[^>]*\bid=['\"]desc_detail['\"])[^>]*>([\s\S]*?)</div>",
        detail_html,
        flags=re.I,
    )
    if not match:
        raise RuntimeError("public #desc_detail block not found")
    return match.group(1).strip()


def _list_target_excerpt(list_html: str, offer_id: str) -> str:
    marker = f"id={offer_id}"
    position = list_html.find(marker)
    if position < 0:
        raise RuntimeError("target offer absent from public list")
    start = max(0, position - 1500)
    end_match = re.search(r"coupon-wrapper|coupon-list", list_html[position + 1 :], re.I)
    end = position + 5000
    if end_match:
        end = position + 1 + end_match.start()
    return list_html[start:end]


def _structure(value: str) -> dict[str, Any]:
    trailing = value[len(value.rstrip()) :]
    return {
        "len": len(value),
        "sha256": _sha256(value),
        "crlf_count": value.count("\r\n"),
        "bare_lf_count": value.count("\n") - value.count("\r\n"),
        "bare_cr_count": value.count("\r") - value.count("\r\n"),
        "trailing_whitespace_len": len(trailing),
        "trailing_codepoints": [f"U+{ord(char):04X}" for char in trailing[-16:]],
        "nbsp_entity_count": value.lower().count("&nbsp;"),
        "literal_nbsp_count": value.count("\u00a0"),
    }


def _first_diff(before: str, after: str) -> dict[str, Any] | None:
    limit = min(len(before), len(after))
    index = next((i for i in range(limit) if before[i] != after[i]), limit)
    if index == len(before) == len(after):
        return None

    def point(value: str) -> str | None:
        return f"U+{ord(value[index]):04X}" if index < len(value) else None

    return {
        "index": index,
        "before_codepoint": point(before),
        "after_codepoint": point(after),
        "before_remaining": len(before) - index,
        "after_remaining": len(after) - index,
    }


async def _public_probe(plan) -> dict[str, Any]:
    list_url = (plan.announcement_url or "").split("#", 1)[0]
    list_html = await asyncio.to_thread(fetch_text, list_url)
    offer_id = str(plan.platform_offer_id)
    excerpt = _list_target_excerpt(list_html, offer_id)
    bridge_url = _extract_bridge_url(list_html, list_url, offer_id)
    bridge_html = await asyncio.to_thread(fetch_text, bridge_url)
    detail_url = _extract_detail_url(bridge_html, bridge_url)
    detail_html = await asyncio.to_thread(fetch_text, detail_url)
    full_block = _extract_detail_block(detail_html)
    list_identity = _identity(excerpt)
    detail_identity = _identity(full_block)
    return {
        "list_url": list_url,
        "offer_id": offer_id,
        "list_excerpt_len": len(excerpt),
        "list_excerpt_sha256": _sha256(excerpt),
        "list_has_read_more": bool(re.search(r"Lire\s+la\s+suite", excerpt, re.I)),
        "list_has_explicit_truncation": " ... " in unescape(excerpt),
        "list_identity": list_identity,
        "list_identity_ok": _identity_ok(list_identity),
        "read_more_bridge_url": bridge_url,
        "full_content_url": detail_url,
        "full_block_len": len(full_block),
        "full_block_sha256": _sha256(full_block),
        "full_identity": detail_identity,
        "full_identity_ok": _identity_ok(detail_identity),
        "full_block_has_terminal_content": "discord.gg/dDEMb6jEbn" in unescape(full_block),
    }


async def _ckeditor_roundtrip(page, value: str) -> str:
    return (
        await page.evaluate(
            """
            ({id, value}) => new Promise((resolve, reject) => {
              const inst = window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances[id];
              if (!inst) { reject(new Error('CKEditor instance missing')); return; }
              let done = false;
              const finish = () => {
                if (done) return;
                done = true;
                if (inst.updateElement) inst.updateElement();
                resolve(inst.getData() || '');
              };
              inst.once('dataReady', finish);
              inst.setData(value);
              setTimeout(finish, 5000);
            })
            """,
            {"id": "edit_parrainage_presentation", "value": value},
        )
        or ""
    )


async def _account_probe(plan) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    context = None
    page = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
        )
        context = await _bumper().new_context(browser)
        page = await context.new_page()
        await page.set_viewport_size({"width": 1280, "height": 720})
        await _login(page, _cfg())
        await page.goto(plan.edit_url, wait_until="domcontentloaded", timeout=60000)
        await _detect_challenge(page)
        if not await _ck_ready(page):
            raise RuntimeError("CKEditor not ready on exact target edit page")

        current = await _ck_get(page)
        if not current:
            raise RuntimeError("empty current CKEditor body")
        current_identity = _identity(current)
        first = await _ckeditor_roundtrip(page, current)
        second = await _ckeditor_roundtrip(page, first)
        return {
            "edit_url": plan.edit_url,
            "authenticated": True,
            "target_edit_loaded": page.url.rstrip("/") == plan.edit_url.rstrip("/"),
            "current": _structure(current),
            "current_identity": current_identity,
            "current_identity_ok": _identity_ok(current_identity),
            "roundtrip_1": _structure(first),
            "roundtrip_2": _structure(second),
            "current_to_roundtrip_1": _first_diff(current, first),
            "roundtrip_1_to_2": _first_diff(first, second),
            "current_exact_after_roundtrip": current == first,
            "normalization_idempotent": first == second,
            "save_attempts": 0,
            "form_submits_after_login": 0,
        }
    finally:
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        await pw.stop()


async def run(output: Path, *, public_only: bool = False) -> dict[str, Any]:
    plan = build_write_plan(PLATFORM, PROGRAM, LANGUAGE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "public_and_authenticated_ckeditor_read_only_no_save",
        "started_at": _now(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "platform_writes": 0,
        "public": await _public_probe(plan),
    }
    if public_only:
        report["account"] = {"skipped": True, "reason": "public_only"}
    else:
        if not os.environ.get("ONEPARRAINAGE_EMAIL") or not os.environ.get(
            "ONEPARRAINAGE_PASSWORD"
        ):
            raise RuntimeError("ONEPARRAINAGE_EMAIL/PASSWORD missing")
        report["account"] = await _account_probe(plan)
    report["finished_at"] = _now()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-only", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.output, public_only=args.public_only))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {
            "schema_version": 1,
            "mode": "public_and_authenticated_ckeditor_read_only_no_save",
            "finished_at": _now(),
            "platform_writes": 0,
            "error": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
