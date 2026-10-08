"""Single-shot global refresh with server proof; legacy listing fallback.

Responsive copies of a batch button represent one action. Legacy individual
controls keep stable identities. Neither path replays an uncertain click.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bumper")
BUTTONS = 'button:has-text("Actualiser"), a:has-text("Actualiser")'
# The account now renders two copies (main card + sticky bar) of the same
# batch action. Run 37754057608 clicked the main control successfully, then
# failed waiting for the hidden btn-actualiser-barre copy.
BATCH_BUTTONS = 'button[onclick*="actualiserToutesLesAnnonces"]'


class BatchRefreshBlocked(RuntimeError):
    """Known batch guard failure before a click; repeating cannot fix it."""


async def _batch_state(page):
    controls = page.locator(BATCH_BUTTONS)
    if not await controls.count():
        return None
    states = []
    for index in range(await controls.count()):
        button = controls.nth(index)
        counter = await button.get_attribute("data-compteur")
        listings = await button.get_attribute("data-annonces")
        if not counter or not counter.isdecimal() or not listings or not listings.isdecimal():
            raise BatchRefreshBlocked("Actualisation globale: compteurs absents ou invalides")
        states.append((int(counter), int(listings)))
    if len(set(states)) != 1:
        raise BatchRefreshBlocked("Actualisation globale: copies du bouton incoherentes")
    used, listings = states[0]
    if not 0 <= used <= 5 or listings < 1:
        raise BatchRefreshBlocked("Actualisation globale: quota ou nombre d'annonces invalide")
    return used, listings


async def _bump_all(page, account_url, before, *, click, pause, mark_started):
    used, listings = before
    if used >= 5:
        raise BatchRefreshBlocked("Actualisation globale: quota quotidien epuise (aucun clic)")
    controls = page.locator(BATCH_BUTTONS)
    visible = []
    for index in range(await controls.count()):
        button = controls.nth(index)
        if await button.is_visible() and await button.is_enabled():
            visible.append(button)
    if not visible:
        raise BatchRefreshBlocked("Actualisation globale: aucun bouton visible et actif (aucun clic)")
    # Every copy invokes the same global action: never click a second copy.
    button = visible[0]
    await button.scroll_into_view_if_needed()
    mark_started()
    await click(page, button)
    await pause(2, 5)
    # Read the server again. A successful UI click alone proves nothing.
    await page.goto(account_url, wait_until="networkidle")
    if "/login" in page.url:
        raise RuntimeError("Actualisation globale: session perdue apres action (resultat inconnu)")
    after = await _batch_state(page)
    if after != (used + 1, listings):
        raise RuntimeError("Actualisation globale: compteur serveur non confirme (aucun rejeu)")
    log.info("  Actualisation globale verifiee: %s annonces, 1 clic, compteur %s -> %s/5",
             listings, used, used + 1)
    return listings
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
    batch = await _batch_state(page)
    if batch is not None:
        return await _bump_all(page, account_url, batch, click=click, pause=pause,
                              mark_started=mark_started)
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
