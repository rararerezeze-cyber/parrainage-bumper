#!/usr/bin/env python3
"""READ-ONLY: inspect official Agent Import Commit semantics. Never click Commit.

Login → /profile/import/agent → Validate Kraken only → dump DOM/JS/network.
  python -u tools/inspect_referralcodes_commit_semantics.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.referralcodes.agent_import import IMPORT_UI, SCHEMA_VERSION  # noqa: E402
from tools.validate_referralcodes_agent_import import (  # noqa: E402
    LOGIN,
    _classify_result,
    _kraken_item,
)

OUT = ROOT / "data" / "captures" / "referralcodes-commit-semantics.json"
KEYWORDS = (
    "upsert",
    "update",
    "insert",
    "duplicate",
    "existing",
    "overwrite",
    "replace",
    "draft",
    "commit",
    "shop_id",
    "referral_id",
    "profile",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interesting(url: str) -> bool:
    u = (url or "").lower()
    return any(
        x in u
        for x in (
            "import",
            "agent",
            "draft",
            "commit",
            "validate",
            "referral",
            "profile",
        )
    )


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("cookie", "token", "password", "authorization", "csrf")):
                out[k] = "<redacted>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj[:40]]
    if isinstance(obj, str) and len(obj) > 4000:
        return obj[:4000] + "…"
    return obj


async def _page_snapshot(page) -> dict:
    return await page.evaluate(
        """
        () => {
          const scripts = Array.from(document.scripts).map(s => ({
            src: s.src || null,
            id: s.id || null,
            type: s.type || null,
            text_len: (s.textContent || '').length,
          }));
          const forms = Array.from(document.forms).map(f => ({
            action: f.action || null,
            method: f.method || null,
            id: f.id || null,
            inputs: Array.from(f.elements).slice(0, 40).map(el => ({
              tag: el.tagName,
              type: el.type || null,
              name: el.name || null,
              id: el.id || null,
              value: (el.type === 'password') ? '<redacted>'
                : ((el.value || '').length > 200 ? (el.value || '').slice(0, 200) : (el.value || null)),
            })),
          }));
          const buttons = Array.from(document.querySelectorAll('button, input[type=submit], a'))
            .map(el => {
              const label = ((el.innerText || el.value || '') + '').trim().slice(0, 80);
              const blob = (label + ' ' + (el.id||'') + ' ' + (el.className||'') + ' ' + (el.href||'')).toLowerCase();
              if (!/(commit|import|validate|save|submit|confirm)/.test(blob)) return null;
              return {
                tag: el.tagName,
                label,
                id: el.id || null,
                name: el.name || null,
                type: el.getAttribute('type'),
                href: el.href || null,
                formaction: el.getAttribute('formaction'),
                formmethod: el.getAttribute('formmethod'),
                dataset: Object.assign({}, el.dataset || {}),
                disabled: !!el.disabled,
              };
            })
            .filter(Boolean);
          const resultEl = document.getElementById('agent-import-result');
          let result = null;
          if (resultEl) {
            try { result = JSON.parse(resultEl.textContent || ''); }
            catch (e) { result = resultEl.textContent || null; }
          }
          const hidden = Array.from(document.querySelectorAll('input[type=hidden], [data-draft], [data-draft-id]'))
            .map(el => ({
              name: el.name || null,
              id: el.id || null,
              value: (el.value || '').slice(0, 300),
              dataset: Object.assign({}, el.dataset || {}),
            }));
          return {
            url: location.href,
            title: document.title,
            scripts,
            forms,
            buttons,
            hidden,
            result,
            body_snip: (document.body && document.body.innerText || '').slice(0, 2500),
          };
        }
        """
    )


async def _scan_scripts(page, script_urls: list[str]) -> dict:
    hits: list[dict] = []
    seen: set[str] = set()
    for src in list(script_urls or []):
        if not src or src in seen:
            continue
        seen.add(src)
        try:
            resp = await page.request.get(src)
            text = await resp.text()
        except Exception as exc:
            hits.append({"src": src, "error": str(exc)})
            continue
        low = (text or "").lower()
        if not any(k in low for k in KEYWORDS):
            continue
        lines = []
        for i, line in enumerate((text or "").splitlines()):
            ll = line.lower()
            if any(k in ll for k in KEYWORDS):
                lines.append({"n": i + 1, "line": line.strip()[:240]})
            if len(lines) >= 40:
                break
        paths = sorted(set(re.findall(r"/[a-zA-Z0-9_./-]{6,80}", text or "")))
        import_paths = [
            p
            for p in paths
            if any(x in p.lower() for x in ("import", "draft", "commit", "agent", "referral"))
        ][:40]
        hits.append(
            {
                "src": src,
                "len": len(text or ""),
                "keyword_lines": lines,
                "paths": import_paths,
            }
        )
    try:
        inline = await page.evaluate(
            """
            (keys) => {
              const out = [];
              const list = Array.from(document.scripts || []);
              for (const s of list) {
                if (s.src) continue;
                const t = s.textContent || '';
                const low = t.toLowerCase();
                if (!keys.some(k => low.includes(k))) continue;
                out.push({id: s.id || null, len: t.length, text: t.slice(0, 4000)});
              }
              return out;
            }
            """,
            list(KEYWORDS),
        )
    except Exception as exc:
        inline = [{"error": str(exc)}]
    return {"external": hits, "inline": inline}


async def main() -> int:
    email = os.environ.get("REFERRALCODES_EMAIL") or ""
    password = os.environ.get("REFERRALCODES_PASSWORD") or ""
    item = _kraken_item()
    payload = {"version": SCHEMA_VERSION, "items": [item]}
    report: dict = {
        "platform": "referralcodes",
        "phase": "INSPECT_COMMIT_SEMANTICS",
        "commit_clicked": False,
        "live_write": False,
        "payload": payload,
        "at": _now(),
    }
    if not email or not password:
        report["ok"] = False
        report["error"] = "AUTH_REQUIRED"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("missing REFERRALCODES_*")
        return 1

    import bumper as bumper_mod
    from playwright.async_api import async_playwright

    net: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=en-US"],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()

        def on_response(resp) -> None:
            try:
                url = resp.url
                if not _interesting(url):
                    return
                method = resp.request.method
                if method.upper() == "OPTIONS":
                    return
                post = ""
                try:
                    post = resp.request.post_data or ""
                except Exception:
                    post = ""
                net.append(
                    _redact(
                        {
                            "method": method,
                            "url": url,
                            "status": resp.status,
                            "resource": resp.request.resource_type,
                            "post": post[:4000],
                        }
                    )
                )
            except Exception:
                pass

        page.on("response", on_response)
        try:
            await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.0, 1.6)
            if any(
                x in ((await page.inner_text("body")) or "").lower()
                for x in ("captcha", "just a moment", "cf-browser")
            ):
                raise RuntimeError("captcha on /login — no bypass")
            if not await bumper_mod.smart_fill(
                page, ['input[type="email"]', 'input[name="email"]'], email, timeout=10000
            ):
                raise RuntimeError("login email missing")
            if not await bumper_mod.smart_fill(
                page, ['input[type="password"]'], password, timeout=8000
            ):
                raise RuntimeError("login password missing")
            await bumper_mod.human_click(
                page, page.locator('button[type="submit"], input[type="submit"]').first
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            if "/login" in page.url:
                raise RuntimeError("login failed")

            await page.goto(IMPORT_UI, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.2, 2.0)
            if "/login" in page.url:
                raise RuntimeError("import UI bounced")
            before = await _page_snapshot(page)
            script_urls = [s.get("src") for s in (before.get("scripts") or []) if s.get("src")]
            report["before_validate"] = {
                "url": before.get("url"),
                "buttons": before.get("buttons"),
                "forms": before.get("forms"),
                "hidden": before.get("hidden"),
                "scripts": before.get("scripts"),
            }
            try:
                report["js_scan"] = await _scan_scripts(page, script_urls)
            except Exception as exc:
                report["js_scan"] = {"error": str(exc)}

            area = page.locator("textarea").first
            if not await area.count():
                raise RuntimeError("no textarea")
            await area.fill(json.dumps(payload, ensure_ascii=False))
            validate_btn = page.locator(
                'button:has-text("Validate"), input[value*="Validate" i]'
            ).first
            if not await validate_btn.count():
                raise RuntimeError("Validate missing")
            net_before = len(net)
            await bumper_mod.human_click(page, validate_btn)
            await bumper_mod.human_sleep(2.0, 3.0)
            after = await _page_snapshot(page)
            report["after_validate"] = {
                "url": after.get("url"),
                "buttons": after.get("buttons"),
                "forms": after.get("forms"),
                "hidden": after.get("hidden"),
                "result": after.get("result"),
                "body_snip": after.get("body_snip"),
            }
            result = after.get("result")
            if isinstance(result, dict):
                report["classify"] = _classify_result(json.dumps(result), result)
            else:
                report["classify"] = _classify_result(str(result or ""), None)

            # Probe draft URLs discovered in JS only — GET, never commit
            draft_id = None
            if isinstance(result, dict):
                draft_id = result.get("draft_id")
            extra_gets = []
            candidates = []
            if draft_id:
                candidates.extend(
                    [
                        f"https://referralcodes.com/profile/import/agent/draft/{draft_id}",
                        f"https://referralcodes.com/profile/import/agent/{draft_id}",
                        f"https://referralcodes.com/api/agent/import/draft/{draft_id}",
                        f"https://referralcodes.com/api/agent/import/{draft_id}",
                    ]
                )
            for hit in (report.get("js_scan") or {}).get("external") or []:
                for p in hit.get("paths") or []:
                    if "draft" in p.lower() or "commit" in p.lower():
                        if p.startswith("http"):
                            candidates.append(p)
                        else:
                            candidates.append("https://referralcodes.com" + p)
            seen_c = set()
            for url in candidates:
                if url in seen_c:
                    continue
                seen_c.add(url)
                try:
                    resp = await page.request.get(url)
                    extra_gets.append(
                        {
                            "url": url,
                            "status": resp.status,
                            "body": (await resp.text())[:2000],
                        }
                    )
                except Exception as exc:
                    extra_gets.append({"url": url, "error": str(exc)})
            report["draft_gets"] = extra_gets
            report["validate_network"] = net[net_before:]
            report["interesting_network"] = [
                n
                for n in net
                if n.get("method") in {"POST", "PUT", "PATCH"}
                or any(x in (n.get("url") or "") for x in ("import", "draft", "commit", "validate"))
            ][-30:]
            report["commit_clicked"] = False
            report["ok"] = True
        except Exception as exc:
            report["ok"] = False
            report["error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    # Derive semantics from collected evidence only.
    ev = {
        "commit_endpoint": None,
        "commit_payload": None,
        "existing_listing_identifier_found": None,
        "update_semantics_proven": False,
        "duplicate_risk": "UNKNOWN",
        "pc_off_ready_possible": False,
        "support_question_required": True,
    }
    blob = json.dumps(report, ensure_ascii=False).lower()
    after_btns = ((report.get("after_validate") or {}).get("buttons")) or []
    for b in after_btns:
        label = (b.get("label") or "").lower()
        if "commit" in label:
            ev["commit_endpoint"] = b.get("formaction") or b.get("href")
    for rec in report.get("validate_network") or []:
        url = rec.get("url") or ""
        if rec.get("method") in {"POST", "PUT", "PATCH"} and "valid" in url.lower():
            ev.setdefault("validate_endpoint", url)
        if "commit" in url.lower() and rec.get("method") != "GET":
            ev["commit_endpoint"] = url
            ev["commit_payload"] = rec.get("post")
    if "upsert" in blob or "update existing" in blob or "overwrite" in blob:
        ev["update_semantics_proven"] = True
        ev["duplicate_risk"] = "LOW_IF_UPSERT"
        ev["support_question_required"] = False
    elif "duplicate" in blob and "shop" in blob:
        ev["duplicate_risk"] = "STATED_IN_UI"
    result = (report.get("after_validate") or {}).get("result") or {}
    if isinstance(result, dict):
        item0 = ((result.get("items") or [{}])[0]) if result.get("items") else {}
        ev["existing_listing_identifier_found"] = (
            item0.get("referral_id")
            or item0.get("existing_id")
            or item0.get("listing_id")
            or item0.get("id")
        )
        ev["draft_id"] = result.get("draft_id")
        ev["shop_id"] = item0.get("shop_id")
        if ev["existing_listing_identifier_found"] and ev["update_semantics_proven"]:
            ev["pc_off_ready_possible"] = True
            ev["support_question_required"] = False
    report["semantics"] = ev

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "error": report.get("error"),
                "semantics": ev,
                "commit_clicked": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
