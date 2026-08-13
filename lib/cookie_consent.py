"""Standard cookie-consent click only. No bypass, no reject, no customize.

If a visible banner has no unambiguous Accept button → CONSENT_BLOCKED.
"""
from __future__ import annotations

import re
from typing import Any

ACCEPT_EXACT = {
    "accepter",
    "tout accepter",
    "j'accepte",
    "j’accepte",
    "autoriser",
    "tout autoriser",
    "accept",
    "accept all",
    "accept cookies",
    "allow",
    "allow all",
    "agree",
    "i agree",
    "ok",
}
ACCEPT_PREFIX = (
    "tout accepter",
    "j'accepte",
    "j’accepte",
    "accept all",
    "allow all",
    "accepter les cookies",
    "accepter et continuer",
    "autoriser les cookies",
)
REJECT_OR_SETTINGS = (
    "refus",
    "reject",
    "deny",
    "personnalis",
    "parametr",
    "paramétr",
    "manage",
    "custom",
    "settings",
    "necessaire",
    "nécessaire",
    "essential",
    "plus d",
    "options",
)
CONSENT_HINTS = (
    "cookie",
    "consent",
    "didomi",
    "onetrust",
    "sourcepoint",
    "consentframework",
    "quantcast",
    "axeptio",
    "gdpr",
    "tcf",
    "cmp",
)


class ConsentBlocked(RuntimeError):
    def __init__(self, message: str):
        super().__init__(f"CONSENT_BLOCKED: {message}")


def normalize_label(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def classify_consent_label(text: str) -> str:
    lab = normalize_label(text)
    if not lab:
        return "OTHER"
    if any(x in lab for x in REJECT_OR_SETTINGS) and "tout accepter" not in lab:
        return "REJECT_OR_SETTINGS"
    if lab in ACCEPT_EXACT:
        return "ACCEPT"
    if any(lab.startswith(p) or p in lab for p in ACCEPT_PREFIX):
        return "ACCEPT"
    if lab in {"accepter", "autoriser"} or lab.startswith("accepter ") or lab.startswith("autoriser "):
        if any(x in lab for x in REJECT_OR_SETTINGS):
            return "REJECT_OR_SETTINGS"
        return "ACCEPT"
    return "OTHER"


def pick_accept_button(buttons: list[dict[str, str]]) -> dict[str, str] | None:
    accepts = [b for b in buttons if classify_consent_label(b.get("text") or "") == "ACCEPT"]
    if not accepts:
        return None
    for preferred in ("tout accepter", "accept all", "allow all", "j'accepte", "j’accepte"):
        for b in accepts:
            if preferred in normalize_label(b.get("text") or ""):
                return b
    return accepts[0]


async def _visible_buttons(owner) -> list[dict[str, Any]]:
    try:
        return await owner.evaluate(
            """
            () => Array.from(document.querySelectorAll(
              'button, a[role="button"], input[type="button"], input[type="submit"], [role="button"]'
            )).map((el, i) => {
              const r = el.getBoundingClientRect();
              const st = getComputedStyle(el);
              const visible = !!(r.width && r.height)
                && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
              return {
                index: i,
                text: ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim().slice(0, 80),
                id: el.id || '',
                visible,
              };
            }).filter(b => b.visible && b.text)
            """
        )
    except Exception:
        return []


async def _consent_ui_visible(page) -> bool:
    try:
        hit = await page.evaluate(
            """
            (hints) => {
              const nodes = Array.from(document.querySelectorAll('iframe, div, aside, section, dialog'));
              for (const el of nodes) {
                const blob = ((el.id||'') + ' ' + (el.className||'') + ' ' + (el.src||'') + ' ' + (el.getAttribute('title')||'')).toLowerCase();
                if (!hints.some(h => blob.includes(h))) continue;
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                if (r.width > 40 && r.height > 40 && st.display !== 'none' && st.visibility !== 'hidden')
                  return true;
              }
              const body = (document.body && document.body.innerText || '').toLowerCase();
              return body.includes('cookie') && (body.includes('accepter') || body.includes('accept') || body.includes('consent'));
            }
            """,
            list(CONSENT_HINTS),
        )
        if hit:
            return True
    except Exception:
        pass
    for frame in page.frames:
        url = (frame.url or "").lower()
        if any(h in url for h in CONSENT_HINTS):
            return True
    return False


async def _username_visible(page) -> bool:
    try:
        loc = page.locator("input#_username, input[name='_username']").first
        if await loc.count() == 0:
            return False
        return await loc.is_visible()
    except Exception:
        return False


async def _click_in_owner(owner, button: dict[str, str]) -> bool:
    text = button.get("text") or ""
    loc = owner.locator(
        "button, a[role='button'], input[type='button'], input[type='submit'], [role='button']"
    ).filter(has_text=text)
    try:
        if await loc.count() == 0:
            return False
        target = loc.first
        if not await target.is_visible():
            return False
        await target.click(timeout=3000)
        return True
    except Exception:
        return False


async def handle_cookie_consent(page, *, timeout_s: float = 8.0) -> dict[str, Any]:
    """Click a single standard Accept if a consent banner is visible.

    Returns cookie_consent_handled YES/NO. Raises ConsentBlocked if unsafe.
    """
    import asyncio

    await asyncio.sleep(0.6)
    form_visible = await _username_visible(page)
    banner = await _consent_ui_visible(page)
    owners = [page, *list(page.frames)]
    visible_buttons: list[tuple[Any, dict[str, str]]] = []
    for owner in owners:
        for b in await _visible_buttons(owner):
            visible_buttons.append((owner, b))

    accept_candidates = [
        (owner, b)
        for owner, b in visible_buttons
        if classify_consent_label(b.get("text") or "") == "ACCEPT"
    ]
    settings_or_reject = [
        b
        for _, b in visible_buttons
        if classify_consent_label(b.get("text") or "") == "REJECT_OR_SETTINGS"
    ]

    if not banner and form_visible and not accept_candidates:
        return {
            "cookie_consent_handled": "NO",
            "reason": "no_visible_consent_banner",
            "login_form_visible": True,
        }

    if banner and not accept_candidates:
        raise ConsentBlocked(
            "banner visible but no standard Accepter/Tout accepter/Autoriser button"
            + (f" (saw {[b.get('text') for b in settings_or_reject][:4]})" if settings_or_reject else "")
        )

    if not accept_candidates:
        return {
            "cookie_consent_handled": "NO",
            "reason": "no_accept_button",
            "login_form_visible": form_visible,
        }

    picked = pick_accept_button([b for _, b in accept_candidates])
    if not picked:
        raise ConsentBlocked("ambiguous consent buttons")
    owner = next(o for o, b in accept_candidates if b.get("text") == picked.get("text"))
    clicked = await _click_in_owner(owner, picked)
    if not clicked:
        raise ConsentBlocked(f"could not click standard accept {picked.get('text')!r}")

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.4)
        form_visible = await _username_visible(page)
        banner = await _consent_ui_visible(page)
        if form_visible and not banner:
            break

    form_visible = await _username_visible(page)
    if not form_visible:
        raise ConsentBlocked("overlay still covering #_username after accept")

    return {
        "cookie_consent_handled": "YES",
        "button": picked.get("text"),
        "login_form_visible": True,
        "overlay_gone": not banner,
    }
