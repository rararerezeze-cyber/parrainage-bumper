"""Single-shot Code-Parrainage clicks, bound to the initial listing identities.

Locators are reacquired after each read-only account reload. Never use a
shrinking/reordered locator index as a listing identity, or replay a click.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bumper")
BUTTONS = 'button:has-text("Actualiser"), a:has-text("Actualiser")'
IDENTITY = """el => {
  const href = el.getAttribute('href');
  if (href && href !== '#' && !href.startsWith('javascript:')) return ['href', href];
  if (el.id) return ['id', el.id];
  const action = el.getAttribute('onclick');
  if (action) return ['onclick', action];
  const ids = ['data-id', 'data-offer-id', 'data-listing-id', 'data-annonce-id'];
  for (const key of ids) if (el.hasAttribute(key)) return [key, el.getAttribute(key)];
  return null;
}"""


async def _find_button(page, identity):
    buttons = page.locator(BUTTONS)
    found = []
    for i in range(await buttons.count()):
        btn = buttons.nth(i)
        if await btn.evaluate(IDENTITY) == identity:
            found.append(btn)
    if len(found) != 1:
        raise RuntimeError("Actualiser: identite absente ou ambigue apres relecture")
    btn = found[0]
    await btn.wait_for(state="visible", timeout=10000)
    if not await btn.is_enabled():
        raise RuntimeError("Actualiser: bouton desactive apres relecture")
    return btn


async def bump_listings(page, account_url, *, click, pause, mark_started) -> int:
    buttons = page.locator(BUTTONS)
    count = await buttons.count()
    log.info("  %s boutons Actualiser", count)
    if not count:
        log.info("  Aucune annonce disponible a actualiser pour le moment")
        return 0
    identities = []
    for i in range(count):
        identity = await buttons.nth(i).evaluate(IDENTITY)
        if not identity or identity in identities:
            raise RuntimeError("Actualiser: identite stable unique introuvable (aucun clic)")
        identities.append(identity)
    bumped = 0
    for identity in identities:
        # The previous click can hide other controls or navigate elsewhere.
        # Reopen the account, then match the originally observed listing.
        if bumped:
            await page.goto(account_url, wait_until="networkidle")
            await pause(2, 4)
            if "/login" in page.url:
                raise RuntimeError("Actualiser: session perdue apres action")
        btn = await _find_button(page, identity)
        await btn.scroll_into_view_if_needed()
        mark_started()
        await click(page, btn)
        bumped += 1
        log.info("  Actualiser %s/%s", bumped, count)
        await pause(2, 5)
    log.info("  %s annonces remontees", bumped)
    return bumped
