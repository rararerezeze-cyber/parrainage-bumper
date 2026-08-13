#!/usr/bin/env python3
"""READ-ONLY Super-Parrain Mes annonces discovery. Never clicks Enregistrer."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_resource import (  # noqa: E402
    CODES_PROMO_FORM,
    CODES_PROMO_PATH,
    HARD_STOP_WRONG_RESOURCE,
    POULPEO_CODE,
    POULPEO_PUBLIC,
    POULPEO_SPONSOR,
    assert_not_codes_promo_form,
    classify_edit_url,
)
from platforms.super_parrain.writer import (  # noqa: E402
    _dismiss_blocking_modals,
    _login_super,
    _page_blocked_reason,
)

OUT = ROOT / "data" / "captures" / "super-poulpeo-mes-annonces-discovery.json"
PUBLIC = POULPEO_PUBLIC
MES_ANNONCES_PAGES = (
    "/tableau-de-bord/annonces",
    "/tableau-de-bord/mes-annonces",
    "/tableau-de-bord/parrainages",
    "/tableau-de-bord",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _dump_links(page, needle: str = "poulpeo") -> list[dict[str, str]]:
    return await page.evaluate(
        """
        (needle) => Array.from(document.querySelectorAll('a[href]')).map(a => ({
            href: a.href || '',
            label: ((a.innerText || a.textContent || '') + '').trim().slice(0, 120),
        })).filter(x => {
            const h = (x.href || '').toLowerCase();
            const l = (x.label || '').toLowerCase();
            return h.includes(needle) || l.includes(needle)
              || h.includes('annonce') || l.includes('modifier') || l.includes('éditer');
        })
        """,
        needle,
    )


async def _dump_forms(page) -> dict:
    return await page.evaluate(
        """
        () => {
          const forms = Array.from(document.querySelectorAll('form')).map(f => ({
            action: f.getAttribute('action') || '',
            name: f.getAttribute('name') || '',
            id: f.id || '',
            method: (f.method || '').toLowerCase(),
          }));
          const fields = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable="true"]'))
            .map(el => ({
              tag: el.tagName,
              type: el.type || '',
              name: el.name || '',
              id: el.id || '',
              valueLen: ((el.value != null ? el.value : el.innerText) || '').length,
              preview: ((el.value != null ? el.value : el.innerText) || '').slice(0, 160),
            }));
          return {
            url: location.href,
            title: document.title,
            forms,
            fields,
            bodyPreview: (document.body.innerText || '').slice(0, 2500),
          };
        }
        """
    )


async def main() -> int:
    import bumper as bumper_mod

    cfg = bumper_mod.CONFIG["super"]
    if not cfg.get("email") or not cfg.get("password"):
        print("SUPER_PARRAIN_EMAIL/PASSWORD manquants — abort (no write)", file=sys.stderr)
        return 2

    report: dict = {
        "mode": "READ_ONLY",
        "save_clicked": False,
        "at": _now(),
        "public_listing": PUBLIC,
        "pages": [],
        "poulpeo_links": [],
        "candidate_edit_urls": [],
        "opened_forms": [],
        "real_mes_annonces_edit_url": None,
        "resource_type": None,
        "form_identifier": None,
        "body_field": None,
        "hard_stops": [],
        "error": None,
    }

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            await _login_super(page, cfg)
            report["login"] = "ok"
            report["after_login_url"] = page.url

            for path in MES_ANNONCES_PAGES:
                url = f"{cfg['url']}{path}"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await bumper_mod.human_sleep(1.0, 1.6)
                    await _dismiss_blocking_modals(page)
                except Exception as exc:
                    report["pages"].append({"url": url, "error": str(exc)})
                    continue
                blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
                if blocked:
                    report["pages"].append({"url": url, "blocked": blocked, "final": page.url})
                    continue
                links = await _dump_links(page)
                report["pages"].append(
                    {
                        "requested": url,
                        "final": page.url,
                        "link_count": len(links),
                    }
                )
                for ln in links:
                    href = ln.get("href") or ""
                    if href and href not in {x.get("href") for x in report["poulpeo_links"]}:
                        report["poulpeo_links"].append({**ln, "from": page.url})
                    low = href.lower()
                    if ("edit" in low or "modifier" in low) and "poulpeo" in (
                        href + " " + (ln.get("label") or "")
                    ).lower():
                        report["candidate_edit_urls"].append(
                            {
                                "href": href,
                                "label": ln.get("label"),
                                "from": page.url,
                                "class": classify_edit_url(href),
                            }
                        )

            # Logged-in public listing: owner "Modifier" link
            await page.goto(PUBLIC, wait_until="domcontentloaded", timeout=45000)
            await bumper_mod.human_sleep(1.0, 1.6)
            blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
            report["public_logged_in"] = {
                "final": page.url,
                "blocked": blocked,
                "has_5eur": (await page.inner_text("body")).count("5€"),
                "has_code": POULPEO_CODE in (await page.inner_text("body")),
                "has_sponsor": POULPEO_SPONSOR in (await page.inner_text("body")),
            }
            pub_links = await _dump_links(page)
            report["public_links"] = pub_links
            for ln in pub_links:
                href = ln.get("href") or ""
                label = (ln.get("label") or "").lower()
                if any(x in label for x in ("modifier", "éditer", "editer")) or "/edit" in href.lower():
                    report["candidate_edit_urls"].append(
                        {
                            "href": href,
                            "label": ln.get("label"),
                            "from": PUBLIC,
                            "class": classify_edit_url(href),
                        }
                    )

            # Dedup candidates, never open codes-promo
            seen = set()
            unique = []
            for c in report["candidate_edit_urls"]:
                h = c["href"]
                if h in seen:
                    continue
                seen.add(h)
                unique.append(c)
            report["candidate_edit_urls"] = unique

            for cand in unique:
                href = cand["href"]
                if CODES_PROMO_PATH in href.lower():
                    report["hard_stops"].append(
                        {"url": href, "reason": f"{HARD_STOP_WRONG_RESOURCE}: codes-promo"}
                    )
                    continue
                if cand["class"] == "CODES_PROMO":
                    report["hard_stops"].append(
                        {"url": href, "reason": f"{HARD_STOP_WRONG_RESOURCE}: classified codes-promo"}
                    )
                    continue
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                    await bumper_mod.human_sleep(1.0, 1.5)
                    dump = await _dump_forms(page)
                    names = [f.get("name") or "" for f in dump.get("forms") or []]
                    names += [f.get("name") or "" for f in dump.get("fields") or []]
                    try:
                        assert_not_codes_promo_form(
                            url=page.url, form_names=names, html=dump.get("bodyPreview")
                        )
                    except RuntimeError as stop:
                        report["hard_stops"].append({"url": page.url, "reason": str(stop)})
                        report["opened_forms"].append(
                            {"url": page.url, "rejected": str(stop), "dump": dump}
                        )
                        continue
                    body_field = None
                    for fld in dump.get("fields") or []:
                        n = (fld.get("name") or fld.get("id") or "").lower()
                        if any(
                            k in n
                            for k in ("message", "contenu", "annonce", "body", "texte", "presentation")
                        ) and "condition" not in n:
                            body_field = fld
                            break
                    if body_field is None:
                        # longest textarea that is not codes-promo conditions
                        texts = [
                            f
                            for f in (dump.get("fields") or [])
                            if (f.get("tag") or "").upper() == "TEXTAREA"
                            and CODES_PROMO_FORM not in (f.get("name") or "")
                        ]
                        if texts:
                            body_field = max(texts, key=lambda x: int(x.get("valueLen") or 0))
                    report["opened_forms"].append(
                        {
                            "url": page.url,
                            "class": classify_edit_url(page.url),
                            "forms": dump.get("forms"),
                            "body_field_candidate": body_field,
                            "field_count": len(dump.get("fields") or []),
                        }
                    )
                    if report["real_mes_annonces_edit_url"] is None and classify_edit_url(page.url) == "ANNOUNCEMENT":
                        report["real_mes_annonces_edit_url"] = page.url
                        report["resource_type"] = "ANNOUNCEMENT"
                        forms = dump.get("forms") or []
                        report["form_identifier"] = (
                            (forms[0].get("name") or forms[0].get("action") or forms[0].get("id"))
                            if forms
                            else None
                        )
                        report["body_field"] = (body_field or {}).get("name") or (body_field or {}).get("id")
                        try:
                            await page.screenshot(
                                path=str(ROOT / "data" / "captures" / "debug-super-poulpeo-mes-annonces.png"),
                                full_page=True,
                            )
                        except Exception:
                            pass
                except Exception as exc:
                    report["opened_forms"].append({"url": href, "error": str(exc)})

        except Exception as exc:
            report["error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["save_clicked"] = False
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in (
        "login", "real_mes_annonces_edit_url", "resource_type",
        "form_identifier", "body_field", "save_clicked", "error",
        "candidate_edit_urls", "hard_stops",
    )}, ensure_ascii=False, indent=2))
    print(f"report={OUT}")
    return 0 if report.get("real_mes_annonces_edit_url") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
