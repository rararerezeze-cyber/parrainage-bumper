#!/usr/bin/env python3
"""Authenticated READ-ONLY inspection of the 1Parrainage boost page.

Never clicks a boost/remonter control and never submits a form.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platforms.oneparrainage.writer import BASE, _bumper, _cfg, _detect_challenge, _login

OUT = ROOT / "diagnostic-artifacts/1parrainage-boost-readonly.json"
BOOST_URL = f"{BASE}/espace_parrain/parrainages/boost"


async def run() -> dict:
    if not os.environ.get("ONEPARRAINAGE_EMAIL") or not os.environ.get("ONEPARRAINAGE_PASSWORD"):
        raise RuntimeError("ONEPARRAINAGE_EMAIL/PASSWORD missing")

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
        )
        ctx = await _bumper().new_context(browser)
        page = await ctx.new_page()
        try:
            await _login(page, _cfg())
            await page.goto(BOOST_URL, wait_until="domcontentloaded", timeout=60000)
            await _detect_challenge(page)
            if "/login" in page.url:
                raise RuntimeError("session_lost")

            payload = await page.evaluate(
                """() => {
                  const text = document.body ? document.body.innerText : '';
                  const forms = Array.from(document.forms).map((f, i) => ({
                    index: i,
                    action: f.action || '',
                    method: (f.method || 'get').toLowerCase(),
                    text: (f.innerText || '').trim().slice(0, 3000),
                    inputs: Array.from(f.querySelectorAll('input,button,select,textarea')).map(el => ({
                      tag: el.tagName.toLowerCase(),
                      type: el.getAttribute('type') || '',
                      name: el.getAttribute('name') || '',
                      value: el.getAttribute('value') || '',
                      text: (el.innerText || '').trim(),
                      disabled: !!el.disabled,
                    })),
                  }));
                  const buttons = Array.from(document.querySelectorAll('button,input[type=submit],a'))
                    .map(el => ({
                      tag: el.tagName.toLowerCase(),
                      text: (el.innerText || el.value || '').trim(),
                      href: el.href || '',
                      type: el.getAttribute('type') || '',
                      name: el.getAttribute('name') || '',
                      value: el.getAttribute('value') || '',
                      disabled: !!el.disabled,
                    }))
                    .filter(x => /boost|remont|premium|actualis/i.test(
                      [x.text,x.href,x.name,x.value].join(' ')
                    ));
                  return {
                    url: location.href,
                    title: document.title,
                    body_text: text.slice(0, 16000),
                    forms,
                    controls: buttons,
                  };
                }"""
            )
            payload["platform"] = "1parrainage"
            payload["mode"] = "authenticated_read_only"
            payload["platform_writes"] = 0
            payload["boost_clicks"] = 0
            return payload
        finally:
            await page.close()
            await ctx.close()
            await browser.close()


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        report = {
            "platform": "1parrainage",
            "mode": "authenticated_read_only",
            "platform_writes": 0,
            "boost_clicks": 0,
            "error": f"{type(exc).__name__}:{exc}",
        }
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "url": report.get("url"),
        "forms": len(report.get("forms") or []),
        "controls": report.get("controls"),
        "platform_writes": 0,
        "boost_clicks": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
