#!/usr/bin/env python3
"""READ-ONLY — full extraction of every script relevant to the Code-Parrainage
edit form (/modif/84601), to settle whether "Enregistrer les modifications"
is a plain native HTML form submit or has additional JS logic (preventDefault,
fetch/XHR, anti-duplicate checks, etc).

Explicit operator authorization (2026-08-16), diagnostic-only. Real login,
real navigation -- structurally cannot click Save/Actualiser or submit
anything (no click, no form submit, no requestSubmit anywhere in this
module -- see tests/test_inspect_code_parrainage_offre_scripts_full.py).

Unlike tools/diagnose_code_parrainage_offre_save.py's script_scan (which
only kept +/-60 char keyword excerpts), this dumps each script's FULL text
to its own file for direct review, plus a small JSON summary (length,
sha256, whether a submitBtn click listener exists, whether it references
fetch/XHR/preventDefault).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from platforms.code_parrainage.writer import _login  # noqa: E402
from tools.canary_write_code_parrainage import EDIT_URL, PLATFORM, PROGRAM  # noqa: E402

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "inspect-code-parrainage-offre-scripts-full.json"

SUBMIT_KEYWORDS = (
    "preventDefault", "fetch(", "XMLHttpRequest", "xhr", "requestSubmit",
    ".submit(", "addEventListener('click'", 'addEventListener("click"',
    "async", "await", "similar", "duplicate", "identique", "spam",
    "modification.php", "modifpost",
)


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "edit_url": EDIT_URL,
        "mode": "diagnostic_read_only_full_script_dump_never_saves",
        "scripts": [],
    }

    bumper_mod = __import__("bumper")
    cfg = {
        "url": bumper_mod.CONFIG["code"]["url"],
        "email": os.environ.get("CODE_PARRAINAGE_EMAIL") or "",
        "password": os.environ.get("CODE_PARRAINAGE_PASSWORD") or "",
    }

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            await _login(page, cfg)
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.2, 2.0)

            srcs = await page.evaluate(
                """
                () => Array.from(document.scripts).map(s => ({
                  src: s.src || '', inline: s.src ? '' : (s.textContent || ''),
                }))
                """
            )

            for i, s in enumerate(srcs or []):
                if s.get("src"):
                    origin = s["src"]
                    try:
                        text = fetch_text(s["src"])
                    except Exception as exc:  # noqa: BLE001
                        report["scripts"].append(
                            {"index": i, "src": origin, "fetch_error": str(exc)}
                        )
                        continue
                else:
                    origin = f"inline#{i}"
                    text = s.get("inline") or ""

                if not text:
                    continue

                fname = f"debug_code_script_{i}.js"
                (ROOT / fname).write_text(text, encoding="utf-8")

                has_offre_ref = "offre" in text
                submit_signals = sorted(
                    {kw for kw in SUBMIT_KEYWORDS if kw.lower() in text.lower()}
                )
                report["scripts"].append(
                    {
                        "index": i,
                        "src": origin,
                        "file": fname,
                        "length": len(text),
                        "sha256": _sha256(text),
                        "references_offre": has_offre_ref,
                        "submit_related_signals_found": submit_signals,
                    }
                )

        except Exception as exc:  # noqa: BLE001
            report["fatal_error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["at_end"] = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report.get("fatal_error") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
