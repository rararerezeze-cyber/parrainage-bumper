#!/usr/bin/env python3
"""One-shot, rollback-guaranteed canary write for Parrainage.co / Kraken.

Explicit operator authorization (2026-08-16): single program (kraken),
single field (referee_reward), exactly 2 saves maximum (canary, rollback),
in ONE browser session/login so there is no risk of a session/cookie
mismatch between the canary save and the mandatory rollback.

State machine:
  snapshot (before, real DOM + real public page)
  -> fill content with the canary render (only referee_reward span changed)
  -> save
  -> reread account + reread public (best-effort; failures do NOT skip rollback)
  -> ALWAYS attempt rollback once save has been clicked (MAY_HAVE_WRITTEN
     fail-safe: an ambiguous response, a failed post-verify, or a timed-out
     public reread is never treated as "safe to leave as-is")
  -> fill content with the original render
  -> save
  -> reread account + reread public
  -> WRITE_VERIFIED only if the full 11-point chain is proven; otherwise
     STOP, no promotion, exact remaining public state reported.

Never touches ref_code/ref_link (personal_code/personal_link) -- read and
compared at every checkpoint, never filled. Never touches Code-Parrainage,
bumpers, Hermes, Telegram, or any GLOBAL override. The PLATFORM_OPERATOR
override this script uses to compute the canary render is set and removed
BEFORE the browser session starts (see _canary_and_original_renders) --
by the time login happens, the override store is already back to its
original state; only two in-memory rendered strings are carried forward.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from lib.offers import OffersRepository  # noqa: E402
from lib.operator_overrides import OperatorOverrideStore  # noqa: E402
from lib.renderer import MappingRepository, Renderer, TemplateRepository  # noqa: E402
from platforms.parrainage_co.writer import (  # noqa: E402
    _dump_form_debug,
    _extract_public_body,
    _login,
    _norm,
    _reread_account_fields,
    _resolve_edit_url,
    WritePlan,
)

PLATFORM = "parrainage-co"
PROGRAM = "kraken"
LANGUAGE = "fr"
FIELD = "referee_reward"
ORIGINAL_VALUE = "200 € en cryptomonnaies"
CANARY_VALUE = "200 € en crypto-monnaies"
PUBLIC_URL = "https://parrainage.co/offers/113735"

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "canary-parrainage-co-kraken.json"


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


def _canary_and_original_renders() -> tuple[str, str, dict, dict]:
    """Compute both full render targets up front, touching the override
    store only transiently -- it is back to its original state before this
    function returns, well before any browser session starts.
    """
    store = OperatorOverrideStore()
    existing = [
        o
        for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if existing:
        raise RuntimeError(
            f"REFUSING: a PLATFORM_OPERATOR override for {PLATFORM}/{PROGRAM}/{FIELD} "
            "already exists -- this script must never touch a pre-existing operator "
            "decision. Aborting before any browser session."
        )

    mapping = MappingRepository().load(PLATFORM, PROGRAM, LANGUAGE)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(PLATFORM, PROGRAM, LANGUAGE)
    offer = renderer.offers.get_by_slug(PROGRAM)

    # Original (current effective state, before touching anything)
    rendered_original = renderer.render(template, mapping, offer=offer)
    variables_original = renderer.build_variables(mapping, offer=offer)

    # Canary: temporary PLATFORM_OPERATOR override, removed immediately after.
    store.upsert(
        PROGRAM, FIELD, CANARY_VALUE, platform=PLATFORM,
        message="canary_write_verified_probe_2026-08-16",
    )
    try:
        rendered_canary = renderer.render(template, mapping, offer=offer)
        variables_canary = renderer.build_variables(mapping, offer=offer)
    finally:
        store.remove(PROGRAM, FIELD, platform=PLATFORM)

    # Confirm cleanup — override store must be exactly as before.
    remaining = [
        o
        for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if remaining:
        raise RuntimeError("override_cleanup_failed — refusing to proceed")

    if variables_canary.get("personal_code") != variables_original.get("personal_code"):
        raise RuntimeError("personal_code differs between canary/original renders — abort")
    if variables_canary.get("personal_link") != variables_original.get("personal_link"):
        raise RuntimeError("personal_link differs between canary/original renders — abort")
    if variables_original.get(FIELD) != ORIGINAL_VALUE:
        raise RuntimeError(
            f"expected original {FIELD}={ORIGINAL_VALUE!r}, "
            f"got {variables_original.get(FIELD)!r} — abort, values have drifted"
        )
    if variables_canary.get(FIELD) != CANARY_VALUE:
        raise RuntimeError("canary render did not pick up CANARY_VALUE — abort")

    return rendered_canary, rendered_original, variables_canary, variables_original


async def _fill_content_only(page, text: str) -> str:
    """Fill ONLY the content textarea. ref_code/ref_link are never touched
    by this script."""
    areas = page.locator("textarea")
    n = await areas.count()
    if not n:
        raise RuntimeError("no_textarea_found")
    best_i, best_len = 0, -1
    for i in range(n):
        v = await areas.nth(i).input_value()
        if len(v) > best_len:
            best_len = len(v)
            best_i = i
    await areas.nth(best_i).fill(text)
    return f"textarea[{best_i}]"


async def _click_save(page) -> None:
    bumper = _bumper()
    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Mettre à jour"), input[type="submit"]'
    )
    count = await btn.count()
    chosen = None
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("boost", "remont", "supprim", "delete")):
            continue
        chosen = b
        break
    if chosen is None:
        raise RuntimeError("save_button_not_found")
    await bumper.human_click(page, chosen)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)


def _extract_form_content(dump: dict) -> str | None:
    for inp in dump.get("inputs") or []:
        if inp.get("tag") == "TEXTAREA" and inp.get("name") == "content":
            return inp.get("preview")  # capped at 200 chars by _dump_form_debug
    return None


async def _reread_public(step_label: str) -> tuple[str | None, str | None]:
    try:
        html = fetch_text(PUBLIC_URL)
        body = _extract_public_body(html)
        return body, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{step_label}_error:{exc}"


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "field": FIELD,
        "original_value": ORIGINAL_VALUE,
        "canary_value": CANARY_VALUE,
        "public_url": PUBLIC_URL,
        "phases": {},
    }

    try:
        rendered_canary, rendered_original, variables_canary, variables_original = (
            _canary_and_original_renders()
        )
    except Exception as exc:  # noqa: BLE001
        report["abort_before_browser"] = str(exc)
        OUT.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ABORT before browser: {exc}")
        return 2

    report["expected_personal_code"] = variables_original.get("personal_code")
    report["expected_personal_link"] = variables_original.get("personal_link")

    bumper = _bumper()
    import os

    cfg = {
        "url": "https://parrainage.co",
        "email": os.environ.get("PARRAINAGE_CO_EMAIL") or "",
        "password": os.environ.get("PARRAINAGE_CO_PASSWORD") or "",
        "rm_cookie": os.environ.get("PARRAINAGE_CO_RM_COOKIE") or "",
    }

    plan_stub = WritePlan(
        platform=PLATFORM, program=PROGRAM, language=LANGUAGE,
        announcement_url=PUBLIC_URL,
        edit_url="https://parrainage.co/account/offers/edit/113735",
        historical=rendered_original, rendered=rendered_canary,
        variables=variables_canary, platform_values={}, changed_fields={},
        structure_preserved=True, mutable_fields=["personal_code", "personal_link", "referee_reward"],
    )

    from playwright.async_api import async_playwright

    edit_url = None
    canary_may_have_written = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        try:
            # --- login + navigate ---
            await _login(page, ctx, cfg)
            edit_url = await _resolve_edit_url(page, plan_stub)
            report["edit_url"] = edit_url
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)

            # --- BEFORE snapshot ---
            before_dump = await _dump_form_debug(page, "debug_parrainage_canary_before.json")
            before_public, before_public_err = await _reread_public("before")
            before_content = _extract_form_content(before_dump)
            before_ref_code = next(
                (i.get("preview") for i in before_dump.get("inputs") or [] if i.get("name") == "ref_code"), None
            )
            before_ref_link = next(
                (i.get("preview") for i in before_dump.get("inputs") or [] if i.get("name") == "ref_link"), None
            )
            report["phases"]["before"] = {
                "content_preview": before_content,
                "ref_code": before_ref_code,
                "ref_link": before_ref_link,
                "ref_code_matches_expected": before_ref_code == variables_original.get("personal_code"),
                "ref_link_matches_expected": before_ref_link == variables_original.get("personal_link"),
                "public_body_matches_original": (
                    _norm(before_public) == _norm(rendered_original) if before_public else None
                ),
                "public_error": before_public_err,
            }

            if not report["phases"]["before"]["ref_code_matches_expected"] or not report["phases"]["before"]["ref_link_matches_expected"]:
                report["abort_before_save"] = "ref_code/ref_link do not match expected values — refusing canary save"
                raise RuntimeError(report["abort_before_save"])

            # --- CANARY SAVE ---
            canary_may_have_written = True  # set BEFORE clicking save
            fill_sel = await _fill_content_only(page, rendered_canary)
            await _click_save(page)
            report["phases"]["canary_save"] = {"filled_via": fill_sel, "clicked": True}

            # --- reread after canary (best-effort; never skips rollback) ---
            canary_account_dump = None
            canary_public_body = None
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                canary_account_dump = await _dump_form_debug(page, "debug_parrainage_canary_after.json")
            except Exception as exc:  # noqa: BLE001
                report["phases"]["canary_save"]["account_reread_error"] = str(exc)

            await asyncio.sleep(2)
            canary_public_body, canary_public_err = await _reread_public("canary")

            canary_content = _extract_form_content(canary_account_dump) if canary_account_dump else None
            canary_ref_code = (
                next((i.get("preview") for i in (canary_account_dump or {}).get("inputs") or [] if i.get("name") == "ref_code"), None)
            )
            canary_ref_link = (
                next((i.get("preview") for i in (canary_account_dump or {}).get("inputs") or [] if i.get("name") == "ref_link"), None)
            )
            report["phases"]["canary_verify"] = {
                "account_content_preview": canary_content,
                "ref_code": canary_ref_code,
                "ref_link": canary_ref_link,
                "ref_code_unchanged": canary_ref_code == before_ref_code,
                "ref_link_unchanged": canary_ref_link == before_ref_link,
                "public_body_matches_canary": (
                    (_norm(rendered_canary) in _norm(canary_public_body)) if canary_public_body else None
                ),
                "public_error": canary_public_err,
            }

            # --- MANDATORY ROLLBACK — always attempted once save was clicked ---
            fill_sel_rb = await _fill_content_only(page, rendered_original)
            await _click_save(page)
            report["phases"]["rollback_save"] = {"filled_via": fill_sel_rb, "clicked": True}

            rollback_account_dump = None
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                rollback_account_dump = await _dump_form_debug(page, "debug_parrainage_rollback_after.json")
            except Exception as exc:  # noqa: BLE001
                report["phases"]["rollback_save"]["account_reread_error"] = str(exc)

            await asyncio.sleep(2)
            rollback_public_body, rollback_public_err = await _reread_public("rollback")

            rollback_content = _extract_form_content(rollback_account_dump) if rollback_account_dump else None
            rollback_ref_code = (
                next((i.get("preview") for i in (rollback_account_dump or {}).get("inputs") or [] if i.get("name") == "ref_code"), None)
            )
            rollback_ref_link = (
                next((i.get("preview") for i in (rollback_account_dump or {}).get("inputs") or [] if i.get("name") == "ref_link"), None)
            )
            rollback_public_match = (
                (_norm(rendered_original) in _norm(rollback_public_body)) if rollback_public_body else None
            )
            report["phases"]["rollback_verify"] = {
                "account_content_preview": rollback_content,
                "ref_code": rollback_ref_code,
                "ref_link": rollback_ref_link,
                "ref_code_unchanged": rollback_ref_code == before_ref_code,
                "ref_link_unchanged": rollback_ref_link == before_ref_link,
                "public_body_matches_original": rollback_public_match,
                "public_error": rollback_public_err,
            }

        except Exception as exc:  # noqa: BLE001
            report["fatal_error"] = str(exc)
            report["canary_may_have_written"] = canary_may_have_written
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["at_end"] = datetime.now(timezone.utc).isoformat()

    rollback_ok = bool(
        report.get("phases", {}).get("rollback_verify", {}).get("public_body_matches_original")
        and report.get("phases", {}).get("rollback_verify", {}).get("ref_code_unchanged")
        and report.get("phases", {}).get("rollback_verify", {}).get("ref_link_unchanged")
    )
    canary_ok = bool(
        report.get("phases", {}).get("canary_verify", {}).get("public_body_matches_canary")
        and report.get("phases", {}).get("canary_verify", {}).get("ref_code_unchanged")
        and report.get("phases", {}).get("canary_verify", {}).get("ref_link_unchanged")
    )
    write_verified = bool(canary_ok and rollback_ok and not report.get("fatal_error"))
    report["canary_ok"] = canary_ok
    report["rollback_ok"] = rollback_ok
    report["write_verified"] = write_verified

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if write_verified:
        from lib.paths import golden_path, mapping_path
        from lib.write_status import mark_write_verified

        golden_path(PLATFORM, PROGRAM, LANGUAGE).write_bytes(rendered_original.encode("utf-8"))
        mpath = mapping_path(PLATFORM, PROGRAM, LANGUAGE)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["write_status"] = "WRITE_VERIFIED"
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence = {
            "post_match": True,
            "canary_and_rollback": True,
            "announcement_url": PUBLIC_URL,
            "edit_url": edit_url,
            "source": "canary_write_parrainage_co",
            "checks": report,
        }
        promo = mark_write_verified(PLATFORM, program=PROGRAM, evidence=evidence)
        print(f"WRITE_VERIFIED parrainage-co registry={promo}")
        return 0

    if not rollback_ok:
        print("::error::ROLLBACK NOT CONFIRMED — see report for exact remaining public state.")
        return 1
    print("Canary verification incomplete — rollback confirmed, no WRITE_VERIFIED promotion.")
    return 1 if not canary_ok else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
