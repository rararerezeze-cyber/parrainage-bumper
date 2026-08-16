#!/usr/bin/env python3
"""READ-ONLY discovery of the real Code-Parrainage Kraken edit mechanism.

Explicit operator authorization (2026-08-16), Étape A only. Real login,
real navigation, real DOM inspection -- no fill, no click on
Actualiser/Enregistrer/Sauvegarder/Valider, no override, no public change.

Why this exists instead of reusing execute_write(inspect_only=True):
platforms/code_parrainage/writer.py's _resolve_edit_url() short-circuits
on `if plan.edit_url: return plan.edit_url` -- and the cached mapping
value (data/platform-mappings/code-parrainage.kraken.fr.json,
edit_url="https://code-parrainage.net/annonce/84601") was already proven
wrong on 2026-08-16 (a prior inspect_only run showed that URL is the
PUBLIC view page: title "Code Parrainage kraken : cpbrgddy", buttons
"Parcourir les offres / Fermer / Copier", no Enregistrer, no editable
code/link/content field). Because that prior run trusted the cached
edit_url, it never actually ran real discovery on /moncompte at all. This
script forces a genuinely fresh discovery by never populating
plan.edit_url with the cached (known-wrong) value.

Captures every "modifier"/edit-like link found on /moncompte (not just
the one the real writer's own selection logic would pick), so this
inspection does not need to be re-run to audit the decision later.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from lib.offers import OffersRepository  # noqa: E402
from lib.renderer import MappingRepository, Renderer, TemplateRepository  # noqa: E402
from platforms.code_parrainage.writer import (  # noqa: E402
    WritePlan,
    _dump_form_debug,
    _extract_public_body,
    _login,
    _norm,
)

PLATFORM = "code-parrainage"
PROGRAM = "kraken"
LANGUAGE = "fr"
KNOWN_PUBLIC_URL = "https://code-parrainage.net/annonce/84601"

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "inspect-code-parrainage-edit-mechanism.json"


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


async def _discover_all_candidates(page, base: str, program: str) -> list[dict]:
    """Full inventory of every modif/edit-labeled link on /moncompte --
    same detection rule as the real writer's _resolve_edit_url, but
    returns every candidate instead of just the first program match.
    """
    await page.goto(f"{base}/moncompte", wait_until="networkidle", timeout=60000)
    await _bumper().human_sleep(1.5, 2.5)
    rows = await page.evaluate(
        """
        (program) => {
          const out = [];
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href || '';
            const label = ((a.innerText||'') + ' ' + href).toLowerCase();
            if (!href) continue;
            if (label.includes('actualis') || label.includes('supprim') || label.includes('boost')) continue;
            const isEdit = label.includes('modif') || href.includes('modif') || href.includes('edit');
            if (!isEdit) continue;
            const row = a.closest('tr, .card, li, article, div');
            const rowText = row ? (row.innerText||'').toLowerCase() : '';
            const isProg = label.includes(program) || rowText.includes(program) || href.includes(program);
            out.push({href, label: label.slice(0, 120), rowText: rowText.slice(0, 200), isProg});
          }
          return out;
        }
        """,
        program,
    )
    return rows or []


async def main() -> int:
    report: dict = {
        "at": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "known_public_url": KNOWN_PUBLIC_URL,
        "mode": "inspect_only_edit_mechanism_discovery",
    }

    bumper = _bumper()
    cfg = bumper.CONFIG["code"]
    cfg = {
        "url": cfg["url"],
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
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        try:
            report["step"] = "login"
            await _login(page, cfg)
            report["logged_in"] = True

            report["step"] = "discover_moncompte_candidates"
            candidates = await _discover_all_candidates(page, cfg["url"], PROGRAM)
            report["all_edit_candidates"] = candidates
            report["moncompte_url"] = page.url

            kraken_candidates = [c for c in candidates if c.get("isProg")]
            report["kraken_matching_candidates"] = kraken_candidates
            # The listing page's row context only exposed "Actualiser" as
            # text (no visible company/program name per row), so label-based
            # isProg matching found nothing. Known public offer id 84601
            # (https://code-parrainage.net/annonce/84601, confirmed Kraken:
            # title "Code Parrainage kraken : cpbrgddy") appeared in the
            # discovered /modif/{id} candidate list -- these platforms
            # consistently reuse the same numeric id across public/edit
            # views (confirmed pattern on parrainage-co, offer 113735). Try
            # that specific id FIRST, with explicit content verification,
            # before falling back to isProg or the first candidate.
            known_id_candidate = next(
                (c["href"] for c in candidates if c["href"].rstrip("/").endswith("/84601")), None
            )
            chosen = known_id_candidate or (
                kraken_candidates[0]["href"] if kraken_candidates else (
                    candidates[0]["href"] if candidates else None
                )
            )
            report["chosen_edit_url"] = chosen
            report["chosen_via"] = (
                "known_public_id_84601" if known_id_candidate
                else ("isProg_label_match" if kraken_candidates else "first_candidate_fallback")
            )

            if chosen:
                report["step"] = "navigate_chosen_edit_url"
                await page.goto(chosen, wait_until="domcontentloaded", timeout=60000)
                await bumper.human_sleep(1.2, 2.0)
                try:
                    await page.screenshot(
                        path="debug_code_inspect_edit_mechanism.png", full_page=True
                    )
                except Exception:
                    pass
                dump = await _dump_form_debug(page, "debug_code_edit_mechanism_form.json")
                report["chosen_url_form_dump"] = dump
                report["chosen_url_title"] = dump.get("title")
                report["chosen_url_has_save_button"] = any(
                    any(
                        kw in (b.get("text") or "").lower()
                        for kw in ("enregistr", "sauvegard", "valider", "modifier", "mettre")
                    )
                    for b in dump.get("buttons") or []
                )
                report["chosen_url_looks_like_public_view"] = any(
                    (b.get("text") or "").strip().lower() in ("copier", "fermer", "parcourir les offres")
                    for b in dump.get("buttons") or []
                )
                # Explicit content check: does this specific edit form
                # actually belong to Kraken? (chosen-by-id-guess still
                # needs real confirmation, not just "id matched".)
                company_val = next(
                    (i.get("preview") for i in dump.get("inputs") or [] if i.get("name") == "company"),
                    None,
                )
                code_ou_lien_val = next(
                    (i.get("preview") for i in dump.get("inputs") or [] if i.get("name") == "code_ou_lien"),
                    None,
                )
                offre_val = next(
                    (i.get("preview") for i in dump.get("inputs") or [] if i.get("name") == "offre"),
                    None,
                )
                report["chosen_url_company_field"] = company_val
                report["chosen_url_code_ou_lien_field"] = code_ou_lien_val
                report["chosen_url_offre_preview"] = offre_val
                report["chosen_url_confirmed_kraken"] = bool(
                    (company_val and "kraken" in company_val.lower())
                    or (code_ou_lien_val and (
                        "cpbrgddy" in code_ou_lien_val or "kraken" in code_ou_lien_val.lower()
                    ))
                    or (offre_val and "kraken" in offre_val.lower())
                )

            # --- independently confirm the known /annonce/84601 URL is public-only ---
            report["step"] = "reconfirm_known_public_url"
            await page.goto(KNOWN_PUBLIC_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.0, 1.8)
            known_dump = await _dump_form_debug(page, "debug_code_known_public_url_form.json")
            report["known_public_url_title"] = known_dump.get("title")
            report["known_public_url_buttons"] = known_dump.get("buttons")
            report["known_public_url_has_save_button"] = any(
                any(
                    kw in (b.get("text") or "").lower()
                    for kw in ("enregistr", "sauvegard", "valider")
                )
                for b in known_dump.get("buttons") or []
            )

        except Exception as exc:  # noqa: BLE001
            report["error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    # --- public fetch (no login needed) for comparison ---
    report["step"] = "public_fetch"
    try:
        html = fetch_text(KNOWN_PUBLIC_URL)
        report["public_extracted_text"] = _extract_public_body(html)
    except Exception as exc:  # noqa: BLE001
        report["public_fetch_error"] = str(exc)

    # --- compare observed values against expected ---
    try:
        mapping = MappingRepository().load(PLATFORM, PROGRAM, LANGUAGE)
        renderer = Renderer(OffersRepository())
        offer = renderer.offers.get_by_slug(PROGRAM)
        expected_vars = renderer.build_variables(mapping, offer=offer)
        report["expected"] = {
            "personal_code": expected_vars.get("personal_code"),
            "personal_link": expected_vars.get("personal_link"),
            "referee_reward": expected_vars.get("referee_reward"),
        }
        public_text = report.get("public_extracted_text") or ""
        report["public_matches_expected"] = {
            "code_present": bool(expected_vars.get("personal_code")) and expected_vars["personal_code"] in public_text,
            "link_present": bool(expected_vars.get("personal_link")) and expected_vars["personal_link"] in public_text,
            "reward_present": bool(expected_vars.get("referee_reward")) and expected_vars["referee_reward"] in public_text,
        }
    except Exception as exc:  # noqa: BLE001
        report["expected_compare_error"] = str(exc)

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
