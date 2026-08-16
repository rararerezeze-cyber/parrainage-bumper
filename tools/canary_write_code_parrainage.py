#!/usr/bin/env python3
"""One-shot, rollback-guaranteed canary write for Code-Parrainage / Kraken.

Explicit operator authorization (2026-08-16): single program (kraken),
single field (the offre textarea's embedded referee_reward span), exactly
2 saves maximum (canary, rollback), in ONE browser session/login.

Real edit form (confirmed by content 2026-08-16, GH runs 31951629497 /
31951796737): https://code-parrainage.net/modif/84601
  - input#company = "Kraken" -- NEVER touched
  - input#code_ou_lien = "cpbrgddy" -- NEVER touched (this platform has a
    single combined code/link field, unlike parrainage-co/1parrainage)
  - textarea#offre = full 750-char description -- the ONLY field filled
  - button "Enregistrer les modifications" -- the real Save

Pre-Save gates, all mandatory, all ABORT-before-any-fill on failure:
  1. Real snapshot of company/code_ou_lien/offre/hidden modifpost.
  2. Real slider/captcha check via bumper.solve_slider() (same official
     solver already proven at login on this platform) -- if a widget is
     detected and NOT solved, ABORT. No bypass, no second solver, no retry
     of the same puzzle (solve_slider's own internal policy).
  3. Save button must be visible AND enabled with nothing else requested
     -- no click on "règles de rédaction" or any other link/button ever
     happens, so no modal it might open can be in the way. If the Save
     button is not directly visible+enabled anyway, ABORT (ambiguous
     blocking state) rather than guess a dismissal action.
  4. company == "Kraken" and code_ou_lien == "cpbrgddy" verified
     immediately before the fill.

MAY_HAVE_WRITTEN discipline: exactly one click per phase (canary,
rollback), never a retry of the same click. A timeout/exception after a
click is never treated as "no write happened" -- the next step is always
a FRESH reread (never cached data) to determine the real state before
deciding anything. If the canary reread is ambiguous, the rollback is
still attempted (single click) -- the canary click is never repeated.

CRITICAL FAIL: if company or code_ou_lien is ever observed to differ from
"Kraken"/"cpbrgddy" at ANY checkpoint (before, after canary, after
rollback), that is reported as a critical failure -- immediate stop, no
further action of any kind.
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

from lib.canonical_text import canonical_contains, canonical_lines, canonical_match  # noqa: E402
from lib.http_fetch import fetch_text  # noqa: E402
from lib.offers import OffersRepository  # noqa: E402
from lib.operator_overrides import OperatorOverrideStore  # noqa: E402
from lib.renderer import MappingRepository, Renderer, TemplateRepository  # noqa: E402
from platforms.code_parrainage.writer import (  # noqa: E402
    WritePlan,
    _dump_form_debug,
    _extract_public_body,
    _login,
)

PLATFORM = "code-parrainage"
PROGRAM = "kraken"
LANGUAGE = "fr"
FIELD = "referee_reward"
ORIGINAL_VALUE = "200 € en cryptomonnaies"
CANARY_VALUE = "200 € en crypto-monnaies"
EDIT_URL = "https://code-parrainage.net/modif/84601"
PUBLIC_URL = "https://code-parrainage.net/annonce/84601"
EXPECTED_COMPANY = "Kraken"
EXPECTED_CODE_OU_LIEN = "cpbrgddy"

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "canary-code-parrainage-kraken.json"


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canary_and_original_renders() -> tuple[str, str, dict, dict]:
    """Compute both full render targets up front, touching the override
    store only transiently -- byte-identical restoration afterward (same
    fix already proven for parrainage-co's canary script).
    """
    from lib.paths import OPERATOR_OVERRIDES_PATH

    original_bytes = (
        OPERATOR_OVERRIDES_PATH.read_bytes() if OPERATOR_OVERRIDES_PATH.exists() else None
    )

    store = OperatorOverrideStore()
    existing = [
        o for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if existing:
        raise RuntimeError(
            f"REFUSING: a PLATFORM_OPERATOR override for {PLATFORM}/{PROGRAM}/{FIELD} "
            "already exists -- must never touch a pre-existing operator decision."
        )

    mapping = MappingRepository().load(PLATFORM, PROGRAM, LANGUAGE)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(PLATFORM, PROGRAM, LANGUAGE)
    offer = renderer.offers.get_by_slug(PROGRAM)

    rendered_original = renderer.render(template, mapping, offer=offer)
    variables_original = renderer.build_variables(mapping, offer=offer)

    try:
        store.upsert(
            PROGRAM, FIELD, CANARY_VALUE, platform=PLATFORM,
            message="canary_write_verified_probe_2026-08-16_code-parrainage",
        )
        try:
            rendered_canary = renderer.render(template, mapping, offer=offer)
            variables_canary = renderer.build_variables(mapping, offer=offer)
        finally:
            store.remove(PROGRAM, FIELD, platform=PLATFORM)
    finally:
        if original_bytes is not None:
            OPERATOR_OVERRIDES_PATH.write_bytes(original_bytes)

    remaining = [
        o for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if remaining:
        raise RuntimeError("override_cleanup_failed — refusing to proceed")
    if original_bytes is not None and OPERATOR_OVERRIDES_PATH.read_bytes() != original_bytes:
        raise RuntimeError("override_store_not_byte_identical_after_cleanup — refusing to proceed")

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


def _read_dump_field(dump: dict, name: str) -> str | None:
    return next(
        (i.get("preview") for i in (dump or {}).get("inputs") or [] if i.get("name") == name),
        None,
    )


def _account_snapshot(dump: dict) -> dict:
    return {
        "company": _read_dump_field(dump, "company"),
        "code_ou_lien": _read_dump_field(dump, "code_ou_lien"),
        "offre": _read_dump_field(dump, "offre"),
        "modifpost": _read_dump_field(dump, "modifpost"),
        "offre_sha256": _sha256(_read_dump_field(dump, "offre")),
    }


def _guard_identity(snapshot: dict, *, phase: str, report: dict) -> None:
    """CRITICAL FAIL check: company/code_ou_lien must never drift. Raises
    to halt everything immediately -- no further action of any kind.
    """
    company = snapshot.get("company")
    code_ou_lien = snapshot.get("code_ou_lien")
    ok = company == EXPECTED_COMPANY and code_ou_lien == EXPECTED_CODE_OU_LIEN
    report.setdefault("identity_checks", {})[phase] = {
        "company": company,
        "code_ou_lien": code_ou_lien,
        "ok": ok,
    }
    if not ok:
        report["critical_fail"] = (
            f"IDENTITY DRIFT at phase={phase}: company={company!r} "
            f"(expected {EXPECTED_COMPANY!r}), code_ou_lien={code_ou_lien!r} "
            f"(expected {EXPECTED_CODE_OU_LIEN!r})"
        )
        raise RuntimeError(report["critical_fail"])


async def _check_slider_and_solve(page, report: dict, *, phase: str) -> None:
    """Real, live detection -- never presumed. Aborts (raises) if a
    widget is present and not solved. No bypass, no retry beyond
    solve_slider's own single-attempt policy.
    """
    bumper = _bumper()
    solved = await bumper.solve_slider(page)
    diag_path = ROOT / "data" / "captures" / "code-parrainage-slider-diag.json"
    diag = None
    try:
        if diag_path.exists():
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    report.setdefault("slider_checks", {})[phase] = {"solved": solved, "diag": diag}
    if not solved:
        report["abort_reason"] = f"slider present and not resolved at phase={phase} — no bypass"
        raise RuntimeError(report["abort_reason"])


async def _check_save_button_clickable(page, report: dict, *, phase: str):
    """No dismissal of any modal is ever attempted -- 'règles de rédaction'
    is never clicked, so its modal never opens. If Save isn't directly
    visible+enabled anyway, this is an ambiguous blocking state: abort.
    """
    btn = page.locator('button:has-text("Enregistrer les modifications")').first
    count = await btn.count()
    visible = bool(count) and await btn.is_visible()
    enabled = bool(count) and await btn.is_enabled()
    report.setdefault("save_button_checks", {})[phase] = {
        "count": count, "visible": visible, "enabled": enabled,
    }
    if not (visible and enabled):
        report["abort_reason"] = (
            f"Save button not directly visible+enabled at phase={phase} "
            "(ambiguous blocking state) — no dismissal attempted, aborting"
        )
        raise RuntimeError(report["abort_reason"])
    return btn


async def _fill_offre_only(page, text: str) -> None:
    await page.locator("textarea#offre").fill(text)


async def _click_save(page, btn) -> None:
    bumper = _bumper()
    await bumper.human_click(page, btn)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)


async def _fetch_public_evidence(expected_rendered: str, *, phase: str) -> dict:
    try:
        html = fetch_text(PUBLIC_URL)
        raw_sha = _sha256(html)
        extracted = _extract_public_body(html)
        return {
            "phase": phase,
            "raw_html_sha256": raw_sha,
            "extracted_text_sha256": _sha256(extracted),
            "extracted_text_len": len(extracted or ""),
            "canonical_lines": canonical_lines(extracted or ""),
            "contains_original_reward": ORIGINAL_VALUE in (extracted or ""),
            "contains_canary_reward": CANARY_VALUE in (extracted or ""),
            "canonical_match_vs_expected": (
                canonical_match(extracted, expected_rendered) if extracted else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"phase": phase, "error": str(exc), "canonical_match_vs_expected": None}


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "field": FIELD,
        "original_value": ORIGINAL_VALUE,
        "canary_value": CANARY_VALUE,
        "edit_url": EDIT_URL,
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

    bumper = _bumper()
    cfg = {
        "url": bumper.CONFIG["code"]["url"],
        "email": os.environ.get("CODE_PARRAINAGE_EMAIL") or "",
        "password": os.environ.get("CODE_PARRAINAGE_PASSWORD") or "",
    }

    from playwright.async_api import async_playwright

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
            # --- login (real slider solve already happens inside _login) ---
            await _login(page, cfg)
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)

            # --- BEFORE: full snapshot ---
            before_dump = await _dump_form_debug(page, "debug_code_canary_before.json")
            before_snapshot = _account_snapshot(before_dump)
            report["phases"]["before_account"] = before_snapshot
            try:
                await page.screenshot(path="debug_code_canary_before.png", full_page=True)
            except Exception:
                pass
            _guard_identity(before_snapshot, phase="before", report=report)
            before_public = await _fetch_public_evidence(rendered_original, phase="before")
            report["phases"]["before_public"] = before_public

            # --- Pre-Save gates (slider + Save-button clickability) ---
            await _check_slider_and_solve(page, report, phase="canary")
            save_btn = await _check_save_button_clickable(page, report, phase="canary")

            # --- CANARY SAVE (single click, no retry) ---
            canary_may_have_written = True  # set BEFORE clicking
            await _fill_offre_only(page, rendered_canary)
            await _click_save(page, save_btn)
            report["phases"]["canary_save"] = {"clicked": True}

            # --- CANARY: fresh reread, account + public (best-effort; never skips rollback) ---
            canary_dump = None
            try:
                await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                canary_dump = await _dump_form_debug(page, "debug_code_canary_after.json")
            except Exception as exc:  # noqa: BLE001
                report["phases"]["canary_save"]["account_reread_error"] = str(exc)

            canary_snapshot = _account_snapshot(canary_dump) if canary_dump else {}
            report["phases"]["canary_account"] = canary_snapshot
            if canary_dump:
                try:
                    _guard_identity(canary_snapshot, phase="canary", report=report)
                except RuntimeError:
                    raise  # CRITICAL FAIL must stop everything, even before rollback
            report["phases"]["canary_account"]["canonical_match_vs_expected"] = (
                canonical_contains(canary_snapshot.get("offre") or "", rendered_canary)
                if canary_snapshot.get("offre") else None
            )
            report["phases"]["canary_public"] = await _fetch_public_evidence(
                rendered_canary, phase="canary"
            )

            # --- MANDATORY ROLLBACK — single click, always attempted once Save was clicked ---
            await _check_slider_and_solve(page, report, phase="rollback")
            save_btn2 = await _check_save_button_clickable(page, report, phase="rollback")
            await _fill_offre_only(page, rendered_original)
            await _click_save(page, save_btn2)
            report["phases"]["rollback_save"] = {"clicked": True}

            rollback_dump = None
            try:
                await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.0, 1.8)
                rollback_dump = await _dump_form_debug(page, "debug_code_rollback_after.json")
            except Exception as exc:  # noqa: BLE001
                report["phases"]["rollback_save"]["account_reread_error"] = str(exc)

            rollback_snapshot = _account_snapshot(rollback_dump) if rollback_dump else {}
            report["phases"]["rollback_account"] = rollback_snapshot
            if rollback_dump:
                _guard_identity(rollback_snapshot, phase="rollback", report=report)
            report["phases"]["rollback_account"]["canonical_match_vs_expected"] = (
                canonical_contains(rollback_snapshot.get("offre") or "", rendered_original)
                if rollback_snapshot.get("offre") else None
            )
            report["phases"]["rollback_public"] = await _fetch_public_evidence(
                rendered_original, phase="rollback"
            )

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

    identity_ok = all(
        v.get("ok") for v in (report.get("identity_checks") or {}).values()
    ) and bool(report.get("identity_checks"))

    canary_ok = bool(_match("canary_account") and _match("canary_public"))
    rollback_ok = bool(_match("rollback_account") and _match("rollback_public"))
    write_verified = bool(
        canary_ok and rollback_ok and identity_ok
        and not report.get("fatal_error") and not report.get("critical_fail")
    )
    report["canary_ok"] = canary_ok
    report["rollback_ok"] = rollback_ok
    report["identity_ok"] = identity_ok
    report["write_verified"] = write_verified

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report.get("critical_fail"):
        print(f"::error::CRITICAL FAIL: {report['critical_fail']}")
        return 3

    if write_verified:
        from lib.paths import golden_path, mapping_path
        from lib.write_status import mark_write_verified

        golden_path(PLATFORM, PROGRAM, LANGUAGE).write_bytes(rendered_original.encode("utf-8"))
        mpath = mapping_path(PLATFORM, PROGRAM, LANGUAGE)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["write_status"] = "WRITE_VERIFIED"
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        legacy_checks = {
            "authenticated": True,
            "targeted_edit": bool(report["phases"].get("canary_save", {}).get("clicked")),
            "submit_ok": bool(
                report["phases"].get("canary_save", {}).get("clicked")
                and report["phases"].get("rollback_save", {}).get("clicked")
            ),
            "reread_account": bool(canary_ok and rollback_ok),
            "expected_values_present": identity_ok,
            "immutable_preserved": identity_ok,
        }
        evidence = {
            "post_match": True,
            "canary_and_rollback": True,
            "announcement_url": PUBLIC_URL,
            "edit_url": EDIT_URL,
            "source": "canary_write_code_parrainage",
            "checks": legacy_checks,
            "full_report": report,
        }
        promo = mark_write_verified(PLATFORM, program=PROGRAM, evidence=evidence)
        print(f"WRITE_VERIFIED code-parrainage registry={promo}")
        return 0 if promo.get("ok") else 1

    if not rollback_ok:
        print("::error::ROLLBACK NOT CONFIRMED — see report for exact remaining state.")
        return 1
    print("Canary verification incomplete — rollback confirmed, no WRITE_VERIFIED promotion.")
    return 1 if not canary_ok else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
