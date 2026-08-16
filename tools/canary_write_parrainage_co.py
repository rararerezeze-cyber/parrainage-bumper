#!/usr/bin/env python3
"""One-shot, rollback-guaranteed canary write for Parrainage.co / Kraken — v2.

Explicit operator authorization (2026-08-16): single program (kraken),
single field (referee_reward), exactly 2 saves maximum (canary, rollback),
in ONE browser session/login so there is no risk of a session/cookie
mismatch between the canary save and the mandatory rollback.

v2 changes from the first run (2026-08-16, run 31948143274):
  - Uses platforms.parrainage_co.writer's canonical line-sequence
    comparison (_canonical_match / _canonical_contains) instead of the old
    _norm()-based substring/equality checks -- the exact same mechanism
    execute_write() now uses for a real write. The first run's rollback
    genuinely succeeded (independently re-confirmed by direct fetch +
    account reread afterwards) but its own public-body verification
    reported false at EVERY phase, including the pre-save baseline,
    because the old comparison had no tolerance for parrainage.co's public
    renderer inserting an extra blank line after every stored line break.
  - Captures full evidence at each of the five phases (before, canary
    account, canary public, rollback account, rollback public): raw text,
    its SHA-256, its canonical line sequence, observed referee_reward/
    code/link, and the canonical match boolean against the phase's
    expected value -- enough to audit after the fact without re-running
    anything.

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
import hashlib
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
    WritePlan,
    _canonical_contains,
    _canonical_lines,
    _canonical_match,
    _dump_form_debug,
    _extract_public_body,
    _login,
    _reread_account_fields,
    _resolve_edit_url,
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


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _account_phase_evidence(label: str, dump: dict | None, plain_text: str | None, expected_rendered: str) -> dict:
    """Full auditable evidence for an account/edit-side checkpoint."""
    ref_code = next(
        (i.get("preview") for i in (dump or {}).get("inputs") or [] if i.get("name") == "ref_code"), None
    )
    ref_link = next(
        (i.get("preview") for i in (dump or {}).get("inputs") or [] if i.get("name") == "ref_link"), None
    )
    content_preview = next(
        (i.get("preview") for i in (dump or {}).get("inputs") or [] if i.get("name") == "content"), None
    )
    return {
        "phase": label,
        "side": "account",
        "raw_text_sha256": _sha256(plain_text),
        "raw_text_len": len(plain_text or ""),
        "canonical_lines": _canonical_lines(plain_text or ""),
        "content_field_preview": content_preview,
        "ref_code": ref_code,
        "ref_link": ref_link,
        "contains_original_reward": ORIGINAL_VALUE in (plain_text or ""),
        "contains_canary_reward": CANARY_VALUE in (plain_text or ""),
        "canonical_match_vs_expected": (
            _canonical_contains(plain_text, expected_rendered) if plain_text else None
        ),
    }


def _public_phase_evidence(label: str, raw_html_sha: str | None, extracted_text: str | None, expected_rendered: str) -> dict:
    """Full auditable evidence for a public-page checkpoint."""
    return {
        "phase": label,
        "side": "public",
        "raw_html_sha256": raw_html_sha,
        "extracted_text_sha256": _sha256(extracted_text),
        "extracted_text_len": len(extracted_text or ""),
        "canonical_lines": _canonical_lines(extracted_text or ""),
        "contains_original_reward": ORIGINAL_VALUE in (extracted_text or ""),
        "contains_canary_reward": CANARY_VALUE in (extracted_text or ""),
        "canonical_match_vs_expected": (
            _canonical_match(extracted_text, expected_rendered) if extracted_text else None
        ),
    }


async def _fetch_public_evidence(label: str, expected_rendered: str) -> dict:
    try:
        html = fetch_text(PUBLIC_URL)
        raw_sha = _sha256(html)
        extracted = _extract_public_body(html)
        return _public_phase_evidence(label, raw_sha, extracted, expected_rendered)
    except Exception as exc:  # noqa: BLE001
        return {
            "phase": label,
            "side": "public",
            "error": str(exc),
            "canonical_match_vs_expected": None,
        }


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

    rendered_original = renderer.render(template, mapping, offer=offer)
    variables_original = renderer.build_variables(mapping, offer=offer)

    store.upsert(
        PROGRAM, FIELD, CANARY_VALUE, platform=PLATFORM,
        message="canary_write_verified_probe_2026-08-16",
    )
    try:
        rendered_canary = renderer.render(template, mapping, offer=offer)
        variables_canary = renderer.build_variables(mapping, offer=offer)
    finally:
        store.remove(PROGRAM, FIELD, platform=PLATFORM)

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


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "field": FIELD,
        "original_value": ORIGINAL_VALUE,
        "canary_value": CANARY_VALUE,
        "public_url": PUBLIC_URL,
        "comparison_mechanism": "canonical_line_sequence_v2",
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

            # --- BEFORE: account + public ---
            before_dump = await _dump_form_debug(page, "debug_parrainage_canary_before.json")
            before_account_text = await _reread_account_fields(page)
            before_account_ev = _account_phase_evidence(
                "before", before_dump, before_account_text, rendered_original
            )
            before_public_ev = await _fetch_public_evidence("before", rendered_original)
            report["phases"]["before_account"] = before_account_ev
            report["phases"]["before_public"] = before_public_ev

            if before_account_ev["ref_code"] != variables_original.get("personal_code") or (
                before_account_ev["ref_link"] != variables_original.get("personal_link")
            ):
                report["abort_before_save"] = (
                    "ref_code/ref_link do not match expected values — refusing canary save"
                )
                raise RuntimeError(report["abort_before_save"])

            # --- CANARY SAVE ---
            canary_may_have_written = True  # set BEFORE clicking save
            fill_sel = await _fill_content_only(page, rendered_canary)
            await _click_save(page)
            report["phases"]["canary_save"] = {"filled_via": fill_sel, "clicked": True}

            # --- CANARY: account + public (best-effort; never skips rollback) ---
            canary_dump = None
            canary_account_text = None
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                canary_dump = await _dump_form_debug(page, "debug_parrainage_canary_after.json")
                canary_account_text = await _reread_account_fields(page)
            except Exception as exc:  # noqa: BLE001
                report["phases"]["canary_save"]["account_reread_error"] = str(exc)

            report["phases"]["canary_account"] = _account_phase_evidence(
                "canary", canary_dump, canary_account_text, rendered_canary
            )
            await asyncio.sleep(2)
            report["phases"]["canary_public"] = await _fetch_public_evidence("canary", rendered_canary)

            # --- MANDATORY ROLLBACK — always attempted once save was clicked ---
            fill_sel_rb = await _fill_content_only(page, rendered_original)
            await _click_save(page)
            report["phases"]["rollback_save"] = {"filled_via": fill_sel_rb, "clicked": True}

            rollback_dump = None
            rollback_account_text = None
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                rollback_dump = await _dump_form_debug(page, "debug_parrainage_rollback_after.json")
                rollback_account_text = await _reread_account_fields(page)
            except Exception as exc:  # noqa: BLE001
                report["phases"]["rollback_save"]["account_reread_error"] = str(exc)

            report["phases"]["rollback_account"] = _account_phase_evidence(
                "rollback", rollback_dump, rollback_account_text, rendered_original
            )
            await asyncio.sleep(2)
            report["phases"]["rollback_public"] = await _fetch_public_evidence("rollback", rendered_original)

        except Exception as exc:  # noqa: BLE001
            report["fatal_error"] = str(exc)
            report["canary_may_have_written"] = canary_may_have_written
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["at_end"] = datetime.now(timezone.utc).isoformat()

    def _match(phase_key: str) -> bool | None:
        return report.get("phases", {}).get(phase_key, {}).get("canonical_match_vs_expected")

    ref_code_ok = (
        report.get("phases", {}).get("canary_account", {}).get("ref_code")
        == report.get("phases", {}).get("before_account", {}).get("ref_code")
        == report.get("phases", {}).get("rollback_account", {}).get("ref_code")
    )
    ref_link_ok = (
        report.get("phases", {}).get("canary_account", {}).get("ref_link")
        == report.get("phases", {}).get("before_account", {}).get("ref_link")
        == report.get("phases", {}).get("rollback_account", {}).get("ref_link")
    )

    canary_ok = bool(_match("canary_account") and _match("canary_public"))
    rollback_ok = bool(_match("rollback_account") and _match("rollback_public"))
    write_verified = bool(
        canary_ok and rollback_ok and ref_code_ok and ref_link_ok and not report.get("fatal_error")
    )
    report["canary_ok"] = canary_ok
    report["rollback_ok"] = rollback_ok
    report["ref_code_unchanged_throughout"] = ref_code_ok
    report["ref_link_unchanged_throughout"] = ref_link_ok
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
            "source": "canary_write_parrainage_co_v2",
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
