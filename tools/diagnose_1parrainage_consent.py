#!/usr/bin/env python3
"""Read-only 1Parrainage/Sirdata rendering diagnostic.

This script only opens the public login page.  Its observation phase never
interacts with the page.  An optional post-capture validation may invoke the
production consent helper's exact CMP action, but it still never reads browser
storage, supplies credentials, fills/submits a form, or navigates to an
account/edit URL.  Artifacts contain only public CMP structure and minimized
browser/network diagnostics.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOGIN_URL = "https://www.1parrainage.com/login"
CMP_ENDPOINTS = {
    "cache.consentframework.com": None,
    "choices.consentframework.com": None,
    "api.consentframework.com": None,
    "js.sddan.com": {"/GS.d"},
}
DIAGNOSTIC_SEED = 1001
DEFAULT_WAIT_SECONDS = 30.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_url(url: str) -> str:
    """Keep scheme/host/path only; query/fragment may contain identifiers."""
    try:
        parts = urlsplit(url or "")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "<invalid-url>"


def _is_cmp_url(url: str) -> bool:
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
        allowed_paths = CMP_ENDPOINTS.get(host, "missing")
        if allowed_paths == "missing":
            return False
        return allowed_paths is None or parts.path in allowed_paths
    except Exception:
        return False


def _privacy_signals(url: str) -> dict[str, str]:
    """Retain only non-identifying CMP mode flags needed for geo comparison."""
    try:
        query = parse_qs(urlsplit(url or "").query, keep_blank_values=True)
    except Exception:
        return {}
    signals: dict[str, str] = {}
    for key in ("gdpr", "cmp", "us_privacy"):
        if key not in query:
            continue
        value = (query.get(key) or [""])[0]
        if re.fullmatch(r"[0-9A-Za-z_-]{0,16}", value):
            signals[key] = value
    return signals


def _safe_text(text: str, *, limit: int = 600) -> str:
    value = (text or "")[:limit]
    value = re.sub(r"([?&][^\s=]+)=([^\s&#]+)", r"\1=<redacted>", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<redacted-email>", value)
    return value


_TIMELINE_JS = r"""
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return !!(r.width && r.height) && s.display !== 'none'
      && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const root = document.querySelector('#sd-cmp');
  const buttons = root ? Array.from(root.querySelectorAll(
    'button, [role="button"], input[type="button"], input[type="submit"]'
  )) : [];
  return {
    ready_state: document.readyState,
    cmp_present: !!root,
    cmp_visible: visible(root),
    cmp_button_count: buttons.length,
    visible_button_labels: buttons.filter(visible).map((el) =>
      ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '')
        .trim().replace(/\s+/g, ' ').slice(0, 120)
    ),
    iframe_count: document.querySelectorAll('iframe').length,
  };
}
"""


_DEEP_SNAPSHOT_JS = r"""
() => {
  const clean = (value, limit = 240) => ((value || '') + '')
    .trim().replace(/\s+/g, ' ').slice(0, limit);
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height,
      top: r.top, right: r.right, bottom: r.bottom, left: r.left};
  };
  const style = (el) => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const visible = !!(r.width && r.height) && s.display !== 'none'
      && s.visibility !== 'hidden' && s.opacity !== '0';
    return {
      visible,
      display: s.display,
      visibility: s.visibility,
      opacity: s.opacity,
      position: s.position,
      z_index: s.zIndex,
      pointer_events: s.pointerEvents,
      in_viewport: visible && r.bottom > 0 && r.right > 0
        && r.top < innerHeight && r.left < innerWidth,
    };
  };
  const describeButton = (el, shadowDepth = 0) => {
    const s = style(el);
    return {
      tag: el.tagName,
      text: clean(el.innerText || el.value),
      aria_label: clean(el.getAttribute('aria-label')),
      title: clean(el.getAttribute('title')),
      id: clean(el.id),
      class: clean(el.className),
      type: clean(el.getAttribute('type')),
      disabled: !!el.disabled,
      aria_disabled: clean(el.getAttribute('aria-disabled')),
      enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
      shadow_depth: shadowDepth,
      bounding_box: rect(el),
      ...s,
    };
  };
  const buttonSelector = 'button, [role="button"], input[type="button"], input[type="submit"]';
  const root = document.querySelector('#sd-cmp');
  const rootButtons = root
    ? Array.from(root.querySelectorAll(buttonSelector)).map((el) => describeButton(el))
    : [];

  const shadowRoots = [];
  const shadowButtons = [];
  const visit = (container, depth) => {
    for (const el of Array.from(container.querySelectorAll('*'))) {
      if (!el.shadowRoot) continue;
      shadowRoots.push({
        host_tag: el.tagName,
        host_id: clean(el.id),
        host_class: clean(el.className),
        depth: depth + 1,
        html_head: clean(el.shadowRoot.innerHTML, 2000),
      });
      for (const button of Array.from(el.shadowRoot.querySelectorAll(buttonSelector))) {
        shadowButtons.push(describeButton(button, depth + 1));
      }
      visit(el.shadowRoot, depth + 1);
    }
  };
  visit(document, 0);

  const hints = ['sd-cmp', 'cookie', 'consent', 'sirdata', 'sddan', 'cmp'];
  const overlayNodes = Array.from(document.querySelectorAll('div, aside, section, dialog, iframe'))
    .filter((el) => {
      const blob = clean((el.id || '') + ' ' + (el.className || '') + ' '
        + (el.getAttribute('src') || '') + ' ' + (el.getAttribute('title') || ''), 500).toLowerCase();
      return hints.some((hint) => blob.includes(hint));
    })
    .slice(0, 40)
    .map((el) => ({
      tag: el.tagName,
      id: clean(el.id),
      class: clean(el.className),
      src: clean(el.getAttribute('src')),
      bounding_box: rect(el),
      ...style(el),
    }));

    const scripts = Array.from(document.scripts)
    .map((script) => script.src || '')
    .filter((src) => /consentframework|sirdata|sddan/i.test(src));
  const rootStyle = root ? style(root) : null;
  const rootRect = root ? rect(root) : null;

  return {
    document_url: location.origin + location.pathname,
    title: clean(document.title),
    ready_state: document.readyState,
    environment: {
      user_agent: navigator.userAgent,
      platform: navigator.platform,
      locale: Intl.DateTimeFormat().resolvedOptions().locale,
      language: navigator.language,
      languages: Array.from(navigator.languages || []),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      viewport: {width: innerWidth, height: innerHeight},
      screen: {width: screen.width, height: screen.height},
      device_pixel_ratio: devicePixelRatio,
    },
    login_form: {
      username_present: !!document.querySelector("input#_username, input[name='_username']"),
      username_visible: (() => {
        const el = document.querySelector("input#_username, input[name='_username']");
        return el ? style(el).visible : false;
      })(),
    },
    consent_text_signals: (() => {
      const body = (document.body && document.body.innerText || '').toLowerCase();
      return {
        cookie: body.includes('cookie'),
        accept_fr: body.includes('accepter'),
        accept_en: body.includes('accept'),
        consent: body.includes('consent'),
      };
    })(),
    cmp: {
      present: !!root,
      visible: rootStyle ? rootStyle.visible : false,
      bounding_box: rootRect,
      style: rootStyle,
      html_excerpt: root
        ? root.outerHTML.replace(/data:[^"'\s)]+/g, 'data:<omitted>').slice(0, 20000)
        : null,
      buttons: rootButtons,
      shadow_roots: shadowRoots,
      shadow_buttons: shadowButtons,
      overlay_nodes: overlayNodes,
      scripts,
    },
  };
}
"""


async def _frame_snapshots(page) -> list[dict]:
    snapshots: list[dict] = []
    for index, frame in enumerate(page.frames):
        item: dict = {
            "index": index,
            "name": _safe_text(frame.name or "", limit=160),
            "url": _safe_url(frame.url),
            "is_main": frame == page.main_frame,
        }
        try:
            item["snapshot"] = await frame.evaluate(_DEEP_SNAPSHOT_JS)
            cmp = item["snapshot"].get("cmp", {})
            cmp["scripts"] = [
                {
                    "url": _safe_url(url),
                    "privacy_signals": _privacy_signals(url),
                }
                for url in cmp.get("scripts", [])
                if _is_cmp_url(url)
            ]
        except Exception as exc:  # noqa: BLE001 - evidence must survive inaccessible frames
            item["snapshot_error"] = _safe_text(str(exc))
        snapshots.append(item)
    return snapshots


async def _helper_observation(page) -> dict:
    """Observe the production helper's classification without invoking clicks."""
    from lib.cookie_consent import _scan_consent_ui

    scan = await _scan_consent_ui(page)
    return {
        "banner": bool(scan.get("banner")),
        "accept_labels": [
            button.get("text")
            for _owner, button in scan.get("accept_candidates", [])
        ],
        "settings_or_reject_labels": [
            button.get("text") for button in scan.get("settings_or_reject", [])
        ],
    }


async def run_diagnostic(
    output_dir: Path, *, wait_seconds: float, validate_helper_after_capture: bool = False
) -> dict:
    import bumper as bumper_mod
    from playwright.async_api import async_playwright

    output_dir.mkdir(parents=True, exist_ok=True)
    before_path = output_dir / "1parrainage-consent-before.png"
    after_path = output_dir / "1parrainage-consent-after.png"
    helper_path = output_dir / "1parrainage-consent-after-helper.png"
    network: list[dict] = []
    console: list[dict] = []
    timeline: list[dict] = []
    started = asyncio.get_running_loop().time()

    def elapsed_ms() -> int:
        return round((asyncio.get_running_loop().time() - started) * 1000)

    def on_request_failed(request) -> None:
        if _is_cmp_url(request.url):
            network.append(
                {
                    "at_ms": elapsed_ms(),
                    "event": "request_failed",
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "url": _safe_url(request.url),
                    "privacy_signals": _privacy_signals(request.url),
                    "failure": _safe_text(str(request.failure or "unknown")),
                }
            )

    def on_response(response) -> None:
        if _is_cmp_url(response.url):
            network.append(
                {
                    "at_ms": elapsed_ms(),
                    "event": "response",
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                    "url": _safe_url(response.url),
                    "privacy_signals": _privacy_signals(response.url),
                }
            )

    def on_console(message) -> None:
        text = message.text or ""
        if message.type not in {"error", "warning"}:
            return
        if not any(hint in text.lower() for hint in ("consent", "cmp", "sirdata", "sddan", "cookie")):
            return
        console.append(
            {"at_ms": elapsed_ms(), "type": message.type, "text": _safe_text(text)}
        )

    report: dict = {
        "schema_version": 1,
        "mode": "public_login_cmp_read_only_no_interaction",
        "login_url": LOGIN_URL,
        "started_at": _utc_now(),
        "wait_seconds": wait_seconds,
        "diagnostic_seed": DIAGNOSTIC_SEED,
        "safety": {
            "credentials_loaded": False,
            "storage_read": False,
            "fills": 0,
            "submits": 0,
            "login_or_save_clicks": 0,
            "consent_helper_enabled": validate_helper_after_capture,
            "account_navigation": False,
            "platform_writes": 0,
        },
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--window-size=1280,720",
            ],
        )
        random.seed(DIAGNOSTIC_SEED)
        context = await bumper_mod.new_context(browser)
        page = await context.new_page()
        await page.set_viewport_size({"width": 1280, "height": 720})
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)
        page.on("console", on_console)

        try:
            response = await page.goto(
                LOGIN_URL, wait_until="domcontentloaded", timeout=60000
            )
            report["navigation"] = {
                "status": response.status if response else None,
                "final_url": _safe_url(page.url),
            }
            report["before"] = {
                "at_ms": elapsed_ms(),
                "frames": await _frame_snapshots(page),
                "helper_observation": await _helper_observation(page),
                "screenshot": before_path.name,
            }
            await page.screenshot(path=str(before_path), full_page=True)

            deadline = asyncio.get_running_loop().time() + wait_seconds
            while True:
                try:
                    state = await page.evaluate(_TIMELINE_JS)
                    state["at_ms"] = elapsed_ms()
                    timeline.append(state)
                except Exception as exc:  # noqa: BLE001
                    timeline.append({"at_ms": elapsed_ms(), "error": _safe_text(str(exc))})
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.5, remaining))

            await page.screenshot(path=str(after_path), full_page=True)
            report["after"] = {
                "at_ms": elapsed_ms(),
                "frames": await _frame_snapshots(page),
                "helper_observation": await _helper_observation(page),
                "screenshot": after_path.name,
            }
            if validate_helper_after_capture:
                from lib.cookie_consent import handle_cookie_consent

                try:
                    helper_result = await handle_cookie_consent(page, timeout_s=8.0)
                    await page.screenshot(path=str(helper_path), full_page=True)
                    report["helper_validation"] = {
                        "ok": True,
                        "result": helper_result,
                        "post_helper_observation": await _helper_observation(page),
                        "screenshot": helper_path.name,
                    }
                except Exception as exc:  # noqa: BLE001 - preserve exact fail-closed proof
                    report["helper_validation"] = {
                        "ok": False,
                        "error": _safe_text(str(exc)),
                    }
            report["ok"] = (
                not validate_helper_after_capture
                or bool(report.get("helper_validation", {}).get("ok"))
            )
        except Exception as exc:  # noqa: BLE001
            report["ok"] = False
            report["fatal_error"] = _safe_text(str(exc))
        finally:
            report["timeline"] = timeline
            report["cmp_network"] = network
            report["cmp_network_failures"] = [
                item
                for item in network
                if item.get("event") == "request_failed" or (item.get("status") or 0) >= 400
            ]
            report["relevant_console"] = console
            report["finished_at"] = _utc_now()
            await page.close()
            await context.close()
            await browser.close()

    report_path = output_dir / "1parrainage-consent-diagnostic.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    final_main = next(
        (frame for frame in report.get("after", {}).get("frames", []) if frame.get("is_main")),
        {},
    )
    final_cmp = final_main.get("snapshot", {}).get("cmp", {})
    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "report": str(report_path),
                "cmp_present": final_cmp.get("present"),
                "visible_actions": [
                    button.get("text")
                    for button in final_cmp.get("buttons", [])
                    if button.get("visible")
                ],
                "frame_count": len(report.get("after", {}).get("frames", [])),
                "cmp_network_failure_count": len(report.get("cmp_network_failures", [])),
                "safety": report.get("safety"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--validate-helper-after-capture", action="store_true")
    args = parser.parse_args()
    if args.wait_seconds < 1 or args.wait_seconds > 60:
        parser.error("--wait-seconds must be between 1 and 60")
    report = asyncio.run(
        run_diagnostic(
            args.output_dir,
            wait_seconds=args.wait_seconds,
            validate_helper_after_capture=args.validate_helper_after_capture,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
