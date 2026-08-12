#!/usr/bin/env python3
"""Capture authentifiee READ-ONLY (compte / pages edition).

Modele session (anti-ban) — voir lib/auth_policy.py :
  1 login par plateforme et par cycle
  → meme contexte navigateur pour toutes les annonces
  → inventaire / edit sequentiel
  → fin session
  jamais storage-state/cookies dans repo ou artifacts
  jamais credentials dans logs

Source principale = pages authentifiees (pas le profil public).
Aucun boost/save/enregistrer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.auth_policy import (
    AuthFailureKind,
    classify_auth_failure,
    policy_snapshot,
    should_stop_platform,
)
from lib.offers import OffersRepository
from lib.template_builder import build_from_text, detect_platform_values, write_build_result

# Import utilitaires bumper sans executer main
sys.path.insert(0, str(ROOT))
import bumper as bumper_mod  # noqa: E402

REPORT_DIR = ROOT / "data" / "captures"



def _has_creds(site: str) -> bool:
    if site == "parrainage":
        return bool(
            os.environ.get("PARRAINAGE_CO_RM_COOKIE")
            or (os.environ.get("PARRAINAGE_CO_EMAIL") and os.environ.get("PARRAINAGE_CO_PASSWORD"))
        )
    if site == "code":
        return bool(os.environ.get("CODE_PARRAINAGE_EMAIL") and os.environ.get("CODE_PARRAINAGE_PASSWORD"))
    if site == "referralcode":
        return bool(os.environ.get("REFERRALCODE_EMAIL") and os.environ.get("REFERRALCODE_PASSWORD"))
    return False


def _slug_from_text(name: str, offers: OffersRepository) -> str | None:
    key = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    for o in offers.load_all():
        on = re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower())
        lk = o.get("lk") or ""
        if on and on == key:
            return lk
        if lk and re.sub(r"[^a-z0-9]+", "", lk) == key:
            return lk
    aliases = {
        "traderepublic": "traderepublic",
        "traderepublique": "traderepublic",
        "boursobank": "boursobank",
        "boursorama": "boursobank",
    }
    return aliases.get(key)


def _save_result(platform: str, program: str, language: str, text: str, url: str | None, offer) -> dict:
    result = build_from_text(
        platform=platform,
        program=program,
        language=language,
        golden_text=text,
        offer=offer,
        announcement_url=url,
    )
    paths = write_build_result(result)
    return {
        "program": program,
        "status": "ok",
        "mutable": result.mutable_fields,
        "sync_mode": result.sync_mode,
        "paths": {k: str(v.relative_to(ROOT)).replace("\\", "/") for k, v in paths.items()},
    }


def _prune_null_offer_mutables(result, offer: dict | None) -> None:
    """Si offers.json n'a pas code/link, ne pas marquer le champ mutable (laisse immutable)."""
    if not offer:
        return
    from lib.template_builder import DEFAULT_MARKERS

    cleaned = []
    for f in list(result.mutable_fields):
        ofield = {
            "personal_code": "code",
            "personal_link": "link",
            "referee_reward": "reward",
        }.get(f)
        if ofield is None:
            cleaned.append(f)
            continue
        ov = offer.get(ofield)
        if ov is None or str(ov).strip() == "":
            marker = DEFAULT_MARKERS.get(f)
            if marker and marker in result.template and f in result.platform_values:
                result.template = result.template.replace(marker, result.platform_values[f])
            result.notes.append(f"{f}: offers.json absent — immutable")
            continue
        cleaned.append(f)
    result.mutable_fields = cleaned
    result.platform_values = {
        k: v for k, v in result.platform_values.items() if k in cleaned
    }
    result.confidences = {k: v for k, v in result.confidences.items() if k in cleaned}


# Known offer-id → program (account path /offers/edit/{id}) when body title is generic
KNOWN_OFFER_IDS = {
    "75195": "boursobank",
    "92118": "acheel",
    "75178": "igraal",
    "75172": "betclic",
    "75173": "unibet",
    "75174": "traderepublic",
    "75175": "winamax",
    "113735": "kraken",
    "76562": "coinbase",
    "76563": "revolut",
    "84354": "poulpeo",
    "84358": "ebuyclub",
    "86502": "joko",
    "109021": "widilo",
    "110585": "totalenergies",
    "114464": "bitstack",
    "114509": "swissborg",
    "115110": "robinhood",
    "115388": "gemini",
    "115693": "ledger",
    "127408": "bybit",
    "118536": "vinted",
    "122884": "plum",
    "123892": "okx",
    "125100": "nrj-mobile",
    "125961": "whatnot",
}


def _slug_from_edit_url(url: str) -> str | None:
    m = re.search(r"/offers/(?:edit/)?(\d+)", url or "")
    if not m:
        return None
    return KNOWN_OFFER_IDS.get(m.group(1))


def _guess_slug(title: str, body: str, offers: OffersRepository) -> str | None:
    """Identifie le programme depuis le titre d'offre, pas un sous-mot fortuit du corps.

    Exemple: iGraal mentionne 'Paypal' comme moyen de retrait → ne pas mapper paypal.
    """
    head = f"{title}\n{(body or '')[:400]}"
    # 1) Explicit "Offre Parrainage {Brand}"
    m = re.search(
        r"(?i)offre\s+parrainage\s+([A-Za-z0-9][A-Za-z0-9 .&\-']{1,40})",
        head,
    )
    if m:
        brand = m.group(1).strip()
        # trim trailing promo words
        brand = re.split(r"\s+[–\-—]\s+|\s+→", brand)[0].strip()
        slug = _slug_from_text(brand, offers)
        if slug:
            return slug
        low = brand.lower()
        aliases = {
            "igraal": "igraal",
            "i graal": "igraal",
            "vinted": "vinted",
            "plum": "plum",
            "okx": "okx",
            "whatnot": "whatnot",
            "nrj mobile": "nrj-mobile",
            "nrj": "nrj-mobile",
            "paypal": "paypal",
            "pay pal": "paypal",
            "trade republic": "traderepublic",
            "boursobank": "boursobank",
            "bourso bank": "boursobank",
            "totalenergies": "totalenergies",
            "total energies": "totalenergies",
        }
        for k, sk in aliases.items():
            if k in low:
                return sk

    # 2) Title alone (page h1)
    slug = _slug_from_text(title, offers)
    if slug and (title or "").strip().lower() not in {"ajouter mon annonce", "modifier", "édition", "edition"}:
        return slug

    # 3) Brand match only in first lines (not full body — avoids false Paypal hits)
    head_low = head.lower()
    best, best_len = None, 0
    for o in offers.load_all():
        n = (o.get("name") or "").strip()
        lk = o.get("lk") or ""
        if n and n.lower() in head_low and len(n) > best_len:
            best, best_len = lk, len(n)
        if lk and re.search(rf"\b{re.escape(lk)}\b", head_low) and len(lk) > best_len:
            best, best_len = lk, len(lk)
    if best:
        return best

    # 4) Orphans — only in header, not full body
    for key, sk in (
        ("vinted", "vinted"),
        ("plum", "plum"),
        ("okx", "okx"),
        ("whatnot", "whatnot"),
        ("nrj mobile", "nrj-mobile"),
        ("paypal", "paypal"),
    ):
        if key in head_low:
            return sk
    return None


async def capture_parrainage_co(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: login compte → /account/offers → pages Modifier — jamais boost/save.

    Source de verite capture = formulaire d'edition authentifie (complet),
    pas le seul profil public.
    """
    cfg = bumper_mod.CONFIG["parrainage"]
    platform = "parrainage-co"
    report: dict = {
        "platform": platform,
        "items": [],
        "errors": [],
        "orphans": [],
        "quality": "auth_edit_pages",
        "login": "pending",
    }
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        rm_cookie = cfg.get("rm_cookie", "")
        email = cfg.get("email", "")
        password = cfg.get("password", "")
        if rm_cookie:
            await ctx.add_cookies(
                [
                    {
                        "name": "parrainageco_rm",
                        "value": rm_cookie.strip(),
                        "domain": "parrainage.co",
                        "path": "/",
                    }
                ]
            )
        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
        await bumper_mod.human_sleep(2, 3)
        # --- AUTH UNE FOIS (meme page/context pour toutes les annonces ci-dessous) ---
        if "/login" in page.url or "connexion" in (page.url or "").lower():
            if not email or not password:
                raise RuntimeError("session requise (cookie/login manquant)")
            await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1, 2)
            # Detect challenge before password spray
            body_probe = (await page.inner_text("body")).lower()[:2000]
            kind = classify_auth_failure(body_probe)
            if kind == AuthFailureKind.CAPTCHA_OR_ANTIBOT:
                raise RuntimeError(f"captcha_or_antibot_challenge on login page")
            ok = await bumper_mod.smart_login_parrainage(page, email, password)
            if not ok:
                body_after = (await page.inner_text("body")).lower()[:2000]
                kind = classify_auth_failure(body_after + " login echoue")
                raise RuntimeError(f"{kind.value}: login non abouti")
            report["login"] = "password"
            await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(2, 3)
        else:
            report["login"] = "cookie_or_session"

        if "/login" in page.url:
            raise RuntimeError(f"{AuthFailureKind.EXPIRED_SESSION.value}: toujours sur /login apres auth")
        report["session_model"] = "single_login_then_sequential_edits"

        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "parrainage-co-raw.txt").write_text(body_text[:80000], encoding="utf-8")
        try:
            await page.screenshot(path=str(REPORT_DIR / "parrainage-co-account-offers.png"), full_page=True)
        except Exception:
            pass

        # Collect edit + public URLs from dashboard rows
        rows = await page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              // Prefer table/card rows
              const anchors = Array.from(document.querySelectorAll('a[href]'));
              for (const a of anchors) {
                const href = a.href || '';
                const label = ((a.innerText || a.textContent || '') + ' ' + href).toLowerCase();
                if (!href) continue;
                if (href.includes('boost') || href.includes('delete') || href.includes('supprim')) continue;
                const isEdit = label.includes('modifier') || href.includes('/edit')
                  || /\\/account\\/offers\\/\\d+/.test(href)
                  || href.includes('/account/offers/edit');
                if (!isEdit) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                // try find public /offers/ID nearby
                let publicUrl = null;
                let row = a.closest('tr, .card, .offer, li, article, div');
                if (row) {
                  for (const x of row.querySelectorAll('a[href*="/offers/"]')) {
                    const h = x.href || '';
                    if (/\\/offers\\/\\d+/.test(h) && !h.includes('/account/')) {
                      publicUrl = h; break;
                    }
                  }
                  const rowText = (row.innerText || '').slice(0, 200);
                  out.push({edit: href, public: publicUrl, rowText});
                } else {
                  out.push({edit: href, public: null, rowText: label.slice(0,120)});
                }
              }
              return out;
            }
            """
        )
        report["edit_urls_found"] = len(rows)
        report["dashboard_preview"] = [
            {"edit": r.get("edit"), "public": r.get("public"), "row": (r.get("rowText") or "")[:80]}
            for r in (rows or [])[:40]
        ]

        seen: set[str] = set()
        for row in (rows or [])[:100]:
            url = row.get("edit")
            if not url:
                continue
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await bumper_mod.human_sleep(1.0, 1.8)
                payload = await page.evaluate(
                    """
                    () => {
                      const pick = (sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return '';
                        return (el.value || el.innerText || '').trim();
                      };
                      const areas = Array.from(document.querySelectorAll('textarea'))
                        .map(t => (t.value || t.innerText || '').trim())
                        .filter(t => t.length > 20);
                      const inputs = {};
                      document.querySelectorAll('input').forEach(i => {
                        const n = ((i.name || '') + ' ' + (i.id || '') + ' ' + (i.placeholder || '')).toLowerCase();
                        const v = (i.value || '').trim();
                        if (v) inputs[n || 'field'] = v;
                      });
                      // contenteditable
                      const ce = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror');
                      let body = areas.sort((a,b)=>b.length-a.length)[0] || '';
                      if (ce) {
                        const t = (ce.innerText || '').trim();
                        if (t.length > body.length) body = t;
                      }
                      const title = pick('h1') || pick('h2') || document.title || '';
                      // public link on page
                      let publicUrl = null;
                      for (const a of document.querySelectorAll('a[href*="/offers/"]')) {
                        const h = a.href || '';
                        if (/\\/offers\\/\\d+/.test(h) && !h.includes('/account/')) { publicUrl = h; break; }
                      }
                      return {title, body, inputs, url: location.href, publicUrl};
                    }
                    """
                )
                body = (payload.get("body") or "").strip()
                for noise in (
                    "Remonter toutes mes annonces",
                    "Remettre en haut",
                    "Vote admin",
                    "Supprimer",
                    "Copier",
                    "Enregistrer",
                    "Sauvegarder",
                ):
                    if body.startswith(noise):
                        body = body.replace(noise, "", 1).strip()
                if len(body) < 40:
                    report["errors"].append({"url": url, "error": "body_too_short", "len": len(body)})
                    continue

                title = payload.get("title") or row.get("rowText") or body.split("\n", 1)[0]
                # Prefer first content line if page title is generic ("Ajouter mon annonce")
                first_line = ""
                for ln in body.splitlines():
                    if ln.strip() and len(ln.strip()) > 8:
                        first_line = ln.strip()
                        break
                if (title or "").strip().lower() in {
                    "ajouter mon annonce",
                    "modifier",
                    "édition",
                    "edition",
                    "",
                }:
                    title = first_line or title
                # Prefer stable offer-id map (fixes BoursoBank/Acheel without brand in first line)
                slug = _slug_from_edit_url(url) or _guess_slug(title, body, offers)
                if not slug:
                    report["errors"].append(
                        {
                            "url": url,
                            "error": "program_unknown",
                            "title": (title or "")[:80],
                            "first_line": first_line[:80],
                        }
                    )
                    continue
                if slug in seen:
                    continue

                force: dict[str, str] = {}
                inputs = payload.get("inputs") or {}
                for k, v in inputs.items():
                    if not v:
                        continue
                    if "code" in k and "postal" not in k and len(v) < 80:
                        force.setdefault("personal_code", v)
                    if any(x in k for x in ("lien", "link", "url", "invite")) and v.startswith("http"):
                        if "parrainage.co" not in v.lower() and "discord" not in v.lower():
                            force.setdefault("personal_link", v)

                public_url = (
                    row.get("public")
                    or payload.get("publicUrl")
                    or None
                )
                # Never store account/edit as announcement_url if we have public
                announcement_url = public_url
                if not announcement_url:
                    # try extract offer id from edit path
                    m = re.search(r"/offers/(?:edit/)?(\d+)", url)
                    if m:
                        announcement_url = f"https://parrainage.co/offers/{m.group(1)}"

                try:
                    offer = offers.get_by_slug(slug)
                    in_offers = True
                except KeyError:
                    offer = None
                    in_offers = False

                if not in_offers:
                    # Orphan: save golden only + NCD
                    orphans_dir = ROOT / "data" / "orphans" / "parrainage-co"
                    orphans_dir.mkdir(parents=True, exist_ok=True)
                    gpath = orphans_dir / f"{slug}.fr.golden.txt"
                    gpath.write_text(body, encoding="utf-8")
                    vals, conf, notes = detect_platform_values(body, None)
                    vals.update(force)
                    ncd_path = ROOT / "data" / "needs_canonical_data.json"
                    data = json.loads(ncd_path.read_text(encoding="utf-8")) if ncd_path.exists() else {"items": [], "version": 1}
                    items = [
                        x for x in (data.get("items") or [])
                        if not (x.get("program_key") == slug and x.get("platform") == platform)
                    ]
                    items.append(
                        {
                            "name": title[:80],
                            "program_key": slug,
                            "url": announcement_url or url,
                            "edit_url": url,
                            "platform": platform,
                            "language": "fr",
                            "status": "needs_canonical_data",
                            "reason": "auth capture: pas dans offers.json",
                            "golden_file": f"data/orphans/parrainage-co/{slug}.fr.golden.txt",
                            "golden_text": body,
                            "detected_values": vals,
                            "confidences": conf,
                            "notes": notes,
                        }
                    )
                    data["items"] = items
                    data["count"] = len(items)
                    ncd_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    seen.add(slug)
                    report["orphans"].append({"program": slug, "url": announcement_url or url, "edit": url})
                    continue

                result = build_from_text(
                    platform=platform,
                    program=slug,
                    language="fr",
                    golden_text=body,
                    offer=offer,
                    announcement_url=announcement_url,
                    force_values=force or None,
                )
                _prune_null_offer_mutables(result, offer)
                result.notes.append(f"auth_edit_url={url}")
                if not announcement_url:
                    result.notes.append("public announcement_url unknown")
                paths = write_build_result(result)
                # patch mapping edit_url
                mpath = paths["mapping"]
                mdata = json.loads(mpath.read_text(encoding="utf-8"))
                mdata["edit_url"] = url
                mdata["quality"] = "auth_edit_refetch"
                mdata["notes"] = "; ".join(result.notes) if result.notes else mdata.get("notes")
                mpath.write_text(json.dumps(mdata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                seen.add(slug)
                report["items"].append(
                    {
                        "program": slug,
                        "status": "ok",
                        "mutable": result.mutable_fields,
                        "chars": len(body),
                        "announcement_url": announcement_url,
                        "edit_url": url,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"url": url, "error": str(exc)})

        report["programs_captured"] = sorted(seen)
        if not report["items"] and not report["orphans"]:
            report["errors"].append(
                {
                    "error": "aucune annonce detail extraite",
                    "hint": "raw dump: data/captures/parrainage-co-raw.txt",
                    "edit_urls_found": report.get("edit_urls_found"),
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
        report["login"] = "failed"
    finally:
        await page.close()
        await ctx.close()
    return report


async def capture_code_parrainage(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: /moncompte — pas de clic Actualiser."""
    cfg = bumper_mod.CONFIG["code"]
    platform = "code-parrainage"
    report = {"platform": platform, "items": [], "errors": []}
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(1, 2)
        await bumper_mod.robust_fill(page, 'input[type="email"]', cfg["email"])
        await bumper_mod.robust_fill(page, 'input[type="password"]', cfg["password"])
        slider_ok = False
        for attempt in range(3):
            if await bumper_mod.solve_slider(page):
                slider_ok = True
                break
            await bumper_mod.human_sleep(1.0, 2.0)
            # recharger le login pour un nouveau puzzle
            await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
            await bumper_mod.robust_fill(page, 'input[type="email"]', cfg["email"])
            await bumper_mod.robust_fill(page, 'input[type="password"]', cfg["password"])
        if not slider_ok:
            raise RuntimeError("slider captcha non resolu apres 3 essais (pas de contournement)")
        await asyncio.sleep(random.uniform(0.8, 1.5))
        await bumper_mod.human_click(
            page, page.locator('button:has-text("Se connecter"), button[type="submit"]').first
        )
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        if not await bumper_mod.verify_login(page, "/login", platform):
            raise RuntimeError("login echoue")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(2, 3)
        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "code-parrainage-raw.txt").write_text(body_text[:50000], encoding="utf-8")

        # Prefer Modifier / edit links for full text (not list previews)
        edit_urls = await page.evaluate(
            """
            () => {
              const out = [];
              for (const a of document.querySelectorAll('a[href], button')) {
                const href = a.href || a.getAttribute('data-href') || '';
                const label = ((a.innerText || a.textContent || '') + ' ' + href).toLowerCase();
                if (!href && !label.includes('modif')) continue;
                if (label.includes('actualis') || label.includes('supprim') || label.includes('delete')) continue;
                if (label.includes('modif') || href.includes('edit') || href.includes('modif')) {
                  if (href) out.push(href);
                }
              }
              return Array.from(new Set(out));
            }
            """
        )
        report["edit_urls_found"] = len(edit_urls)
        seen = set()
        for url in edit_urls[:80]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await bumper_mod.human_sleep(1.0, 1.8)
                payload = await page.evaluate(
                    """
                    () => {
                      const areas = Array.from(document.querySelectorAll('textarea'))
                        .map(t => (t.value || t.innerText || '').trim())
                        .filter(t => t.length > 20);
                      const body = areas.sort((a,b)=>b.length-a.length)[0]
                        || (document.querySelector('main, form, .content, article') || document.body).innerText || '';
                      const title = (document.querySelector('h1,h2') || {}).innerText || document.title || '';
                      return {title, body: body.slice(0, 8000), url: location.href};
                    }
                    """
                )
                body = (payload.get("body") or "").strip()
                # Reject pure list chrome / truncated previews when we expected full form
                if len(body) < 60:
                    continue
                title = payload.get("title") or body.split("\n", 1)[0]
                slug = _slug_from_text(title, offers)
                if not slug:
                    for o in offers.load_all():
                        n = o.get("name") or ""
                        if n and n.lower() in body.lower():
                            slug = o.get("lk")
                            break
                if not slug or slug in seen:
                    continue
                try:
                    offer = offers.get_by_slug(slug)
                except KeyError:
                    offer = None
                item = _save_result(platform, slug, "fr", body, payload.get("url") or url, offer)
                # Clear partial quality if we got a real edit body
                from pathlib import Path
                import json as _json
                mp = Path(item["paths"]["mapping"]) if "paths" in item else None
                # _save_result returns paths relative — rebuild
                mpath = ROOT / "data" / "platform-mappings" / f"code-parrainage.{slug}.fr.json"
                if mpath.exists():
                    d = _json.loads(mpath.read_text(encoding="utf-8"))
                    d.pop("quality", None)
                    d["quality"] = "full_edit" if len(body) > 200 else "capture_partial"
                    if len(body) <= 200:
                        d["quality"] = "capture_partial"
                    mpath.write_text(_json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                seen.add(slug)
                report["items"].append({**item, "chars": len(body)})
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"url": url, "error": str(exc)})

        if not report["items"]:
            report["errors"].append(
                {
                    "error": "aucune annonce complete via pages Modifier — DOM a cartographier",
                    "hint": "raw dump: data/captures/code-parrainage-raw.txt",
                    "edit_urls_found": len(edit_urls),
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
    finally:
        await page.close()
        await ctx.close()
    return report


async def capture_referralcode_tv(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: my-account listings — pas de #cliccami."""
    cfg = bumper_mod.CONFIG["referralcode"]
    platform = "referralcode-tv"
    report = {"platform": platform, "items": [], "errors": []}
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        await page.goto(f"{cfg['url']}/login/", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(1, 2)
        EMAIL_SEL = [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="mail" i]',
            'input[name="username"]',
        ]
        ok_email = await bumper_mod.smart_fill(page, EMAIL_SEL, cfg["email"], timeout=15000)
        if not ok_email:
            raise RuntimeError("champ email introuvable")
        await bumper_mod.smart_fill(
            page, ['input[type="password"]', 'input[name="password"]'], cfg["password"], timeout=10000
        )
        await bumper_mod.human_click(
            page,
            page.locator(
                'button:has-text("SIGN IN"), button[type="submit"], input[type="submit"]'
            ).first,
        )
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        if "/login" in page.url:
            raise RuntimeError("login echoue")

        # Prefer public author page first if reachable without timeout, then account listings
        for listings_url in (
            "https://www.referralcode.tv/author/thesuperreff/",
            f"{cfg['url']}/my-account/?tab=listings",
            f"{cfg['url']}/my-account/",
        ):
            try:
                await page.goto(listings_url, wait_until="domcontentloaded", timeout=60000)
                await bumper_mod.human_sleep(2, 3)
                if "/login" not in page.url:
                    break
            except Exception:
                continue
        await bumper_mod.human_sleep(1, 2)
        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "referralcode-tv-raw.txt").write_text(body_text[:50000], encoding="utf-8")

        # Prefer edit links per listing (READ-ONLY open)
        edit_urls = await page.evaluate(
            """
            () => Array.from(new Set(
              Array.from(document.querySelectorAll('a[href*="edit"], a:has-text("Edit"), button:has-text("Edit Code")'))
                .map(a => a.href || '')
                .filter(h => h && h.startsWith('http'))
            ))
            """
        )
        cards = []
        if not edit_urls:
            cards = await page.evaluate(
                """
                () => {
                  const out = [];
                  document.querySelectorAll('.listing, .card, article, .job_listing').forEach(n => {
                    const t = (n.innerText || '').trim();
                    if (t.length < 40 || t.length > 2500) return;
                    if (!/(code|link|http|referral|bonus)/i.test(t)) return;
                    // un seul bloc — ignorer mega-textes multi-offres
                    if ((t.match(/Live/g) || []).length > 2) return;
                    const a = n.querySelector('a[href]');
                    out.push({href: a ? a.href : location.href, body: t});
                  });
                  return out;
                }
                """
            )
        seen = set()
        targets = [{"href": u, "body": None} for u in edit_urls[:60]] + cards
        for card in targets:
            body = card.get("body")
            href = card.get("href")
            if href and body is None:
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                    await bumper_mod.human_sleep(1, 2)
                    body = await page.evaluate(
                        """
                        () => {
                          const areas = Array.from(document.querySelectorAll('textarea'))
                            .map(t => (t.value||'').trim()).filter(t => t.length>20);
                          if (areas.length) return areas.sort((a,b)=>b.length-a.length)[0];
                          const main = document.querySelector('main, .content, form, article');
                          return (main || document.body).innerText.slice(0, 6000);
                        }
                        """
                    )
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append({"url": href, "error": str(exc)})
                    continue
            if not body or len(body) < 40:
                continue
            slug = None
            for o in offers.load_all():
                n = o.get("name") or ""
                if n and n.lower() in body.lower()[:400]:
                    slug = o.get("lk")
                    break
            if not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                offer = offers.get_by_slug(slug)
            except KeyError:
                offer = None
            try:
                item = _save_result(platform, slug, "en", body, href, offer)
                report["items"].append(item)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"program": slug, "error": str(exc)})

        if not report["items"]:
            report["errors"].append(
                {
                    "error": "aucune annonce structuree extraite — DOM a cartographier",
                    "hint": "raw dump: data/captures/referralcode-tv-raw.txt",
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
    finally:
        await page.close()
        await ctx.close()
    return report


async def amain(sites: list[str]) -> dict:
    """Un browser process ; 1 context/session par plateforme ; 0 storage-state disque."""
    offers = OffersRepository()
    summary: dict = {
        "sites": {},
        "missing_credentials": [],
        "auth_policy": policy_snapshot(),
        "stopped_platforms": [],
    }
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            # Une plateforme a la fois, sequentiel — jamais parallele multi-login
            if "parrainage" in sites:
                if _has_creds("parrainage"):
                    try:
                        summary["sites"]["parrainage-co"] = await capture_parrainage_co(
                            browser, offers
                        )
                    except Exception as exc:  # noqa: BLE001
                        kind = classify_auth_failure(str(exc))
                        summary["sites"]["parrainage-co"] = {
                            "platform": "parrainage-co",
                            "items": [],
                            "errors": [{"error": str(exc), "kind": kind.value}],
                            "login": "failed",
                        }
                        if should_stop_platform(kind):
                            summary["stopped_platforms"].append(
                                {"platform": "parrainage-co", "kind": kind.value}
                            )
                else:
                    summary["missing_credentials"].append("parrainage-co")
            if "code" in sites:
                if _has_creds("code"):
                    try:
                        summary["sites"]["code-parrainage"] = await capture_code_parrainage(
                            browser, offers
                        )
                    except Exception as exc:  # noqa: BLE001
                        kind = classify_auth_failure(str(exc))
                        summary["sites"]["code-parrainage"] = {
                            "platform": "code-parrainage",
                            "items": [],
                            "errors": [{"error": str(exc), "kind": kind.value}],
                        }
                        if should_stop_platform(kind):
                            summary["stopped_platforms"].append(
                                {"platform": "code-parrainage", "kind": kind.value}
                            )
                else:
                    summary["missing_credentials"].append("code-parrainage")
            if "referralcode" in sites:
                if _has_creds("referralcode"):
                    try:
                        summary["sites"]["referralcode-tv"] = await capture_referralcode_tv(
                            browser, offers
                        )
                    except Exception as exc:  # noqa: BLE001
                        kind = classify_auth_failure(str(exc))
                        summary["sites"]["referralcode-tv"] = {
                            "platform": "referralcode-tv",
                            "items": [],
                            "errors": [{"error": str(exc), "kind": kind.value}],
                        }
                        if should_stop_platform(kind):
                            summary["stopped_platforms"].append(
                                {"platform": "referralcode-tv", "kind": kind.value}
                            )
                else:
                    summary["missing_credentials"].append("referralcode-tv")
        finally:
            await browser.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sites",
        default="parrainage,code,referralcode",
        help="Liste separee par virgules: parrainage,code,referralcode",
    )
    args = parser.parse_args()
    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    print("READ-ONLY capture — no boost/save/actualiser")
    print("auth policy: 1 login/platform/cycle, no storage-state, no aggressive retries")
    print("sites:", ",".join(sites))
    # Never print secret values — only presence
    for label, ok in [
        ("PARRAINAGE_CO", _has_creds("parrainage")),
        ("CODE_PARRAINAGE", _has_creds("code")),
        ("REFERRALCODE", _has_creds("referralcode")),
    ]:
        print(f"  creds {label}: {'yes' if ok else 'no'}")

    summary = asyncio.run(amain(sites))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "auth-readonly-report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for pid, rep in summary.get("sites", {}).items():
        n = len(rep.get("items") or [])
        e = len(rep.get("errors") or [])
        print(f"  {pid}: items={n} errors={e}")
    if summary.get("missing_credentials"):
        print("missing_credentials:", ",".join(summary["missing_credentials"]))
    print("report:", out)
    # exit 0 even if partial — CI will upload dumps
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
