"""
=============================================================
  Parrainage Auto-Bumper  —  VERSION FINALE
  super-parrain.com | code-parrainage.net | parrainage.co
  Tourne 24h/24 sur GitHub Actions, zéro intervention
=============================================================
"""

import asyncio
import os
import io
import random
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

log = logging.getLogger("bumper")
log.setLevel("INFO")
log.propagate = False
formatter = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
file_handler = RotatingFileHandler("bumper.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
file_handler.setFormatter(formatter)
if log.hasHandlers():
    log.handlers.clear()
log.addHandler(console_handler)
log.addHandler(file_handler)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

TARGET_SITES = [s.strip() for s in os.environ.get("TARGET_SITES", "").split(",") if s.strip()]

CONFIG = {
    "sites": {
        "super": {
            "url":      "https://www.super-parrain.com",
            "email":    os.environ.get("SUPER_PARRAIN_EMAIL", ""),
            "password": os.environ.get("SUPER_PARRAIN_PASSWORD", ""),
        },
        "code": {
            "url":      "https://code-parrainage.net",
            "email":    os.environ.get("CODE_PARRAINAGE_EMAIL", ""),
            "password": os.environ.get("CODE_PARRAINAGE_PASSWORD", ""),
        },
        "parrainage": {
            "url":      "https://parrainage.co",
            "email":    os.environ.get("PARRAINAGE_CO_EMAIL", ""),
            "password": os.environ.get("PARRAINAGE_CO_PASSWORD", ""),
        },
    },
}

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES HUMAINS
# ══════════════════════════════════════════════════════════════════════════════

async def human_sleep(a: float = 2.0, b: float = 6.0):
    await asyncio.sleep(random.uniform(a, b))


async def robust_fill(page: Page, selector: str, value: str):
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=15000)
    await locator.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await locator.fill(value)
    await asyncio.sleep(random.uniform(0.2, 0.4))


async def human_click(page: Page, locator):
    try:
        await locator.wait_for(state="visible", timeout=15000)
        box = await locator.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + random.randint(2, max(3, int(box["width"] - 2))),
                box["y"] + random.randint(2, max(3, int(box["height"] - 2))),
                steps=random.randint(15, 25),
            )
        await human_sleep(0.2, 0.7)
        await locator.click()
    except Exception as e:
        log.debug(f"human_click ignoré : {e}")


async def verify_login(page: Page, fragment: str, name: str) -> bool:
    current = page.url
    log.info(f"  [{name}] URL après login : {current}")
    if fragment in current:
        log.error(f"  [{name}] ❌ Login échoué")
        return False
    log.info(f"  [{name}] ✓ Login réussi !")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  DRAG HUMAIN AVEC OVERSHOOT
#  Simule un vrai humain : accélère, dépasse légèrement, revient
# ══════════════════════════════════════════════════════════════════════════════

async def human_drag(page: Page, start_x: float, start_y: float, distance: int):
    """
    Drag réaliste avec :
    - Accélération au début
    - Légère hésitation en cours de route (pause microscopique)
    - Overshoot : dépasse de 5-15px puis revient doucement
    - Jitter vertical aléatoire
    - Micro-tremblement à la fin
    """
    overshoot = random.randint(5, 15)
    total_dist = distance + overshoot

    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.15))

    steps = random.randint(40, 60)

    # Phase 1 : aller jusqu'à target + overshoot (ease-in-out)
    for i in range(steps):
        t = i / steps
        # Ease-in-out cubique
        if t < 0.5:
            eased = 4 * t * t * t
        else:
            eased = 1 - (-2 * t + 2) ** 3 / 2

        # Hésitation légère au milieu (30-50% du trajet)
        if 0.3 < t < 0.5 and random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.03, 0.08))

        jitter_y = random.uniform(-2, 2)
        await page.mouse.move(
            start_x + total_dist * eased,
            start_y + jitter_y,
        )
        await asyncio.sleep(random.uniform(0.006, 0.020))

    # Phase 2 : retour depuis overshoot vers target (correction humaine)
    correction_steps = random.randint(8, 15)
    for i in range(correction_steps):
        t = i / correction_steps
        # Ease-out : décélère en arrivant à la bonne position
        pos = (distance + overshoot) - overshoot * (t * (2 - t))
        await page.mouse.move(
            start_x + pos,
            start_y + random.uniform(-0.5, 0.5),
        )
        await asyncio.sleep(random.uniform(0.010, 0.025))

    # Phase 3 : micro-tremblement final (la main qui se stabilise)
    for _ in range(random.randint(2, 5)):
        micro = random.uniform(-1.5, 1.5)
        await page.mouse.move(start_x + distance + micro, start_y + random.uniform(-0.5, 0.5))
        await asyncio.sleep(random.uniform(0.015, 0.030))

    # Position finale exacte
    await page.mouse.move(start_x + distance, start_y)
    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.up()
    await asyncio.sleep(random.uniform(1.0, 2.0))


# ══════════════════════════════════════════════════════════════════════════════
#  SLIDER CAPTCHA — Résolution gratuite (PIL + drag humain)
# ══════════════════════════════════════════════════════════════════════════════

def find_gap_position(bg_bytes: bytes, piece_bytes: bytes) -> int:
    """Analyse PIL pour trouver la position X du trou dans le fond."""
    try:
        from PIL import Image, ImageFilter

        bg    = Image.open(io.BytesIO(bg_bytes)).convert("L")
        piece = Image.open(io.BytesIO(piece_bytes)).convert("L")

        bg_w, bg_h     = bg.size
        piece_w, piece_h = piece.size

        bg_edges = bg.filter(ImageFilter.FIND_EDGES)
        pixels   = list(bg_edges.getdata())

        col_scores = [
            sum(pixels[y * bg_w + x] for y in range(bg_h))
            for x in range(bg_w)
        ]

        best_score = -1
        best_x     = 150
        start      = max(piece_w + 10, 20)

        for x in range(start, bg_w - piece_w):
            score = sum(col_scores[x:x + piece_w])
            if score > best_score:
                best_score = score
                best_x     = x

        log.info(f"  🔍 PIL → trou estimé à X={best_x}px")
        return best_x

    except Exception as e:
        log.warning(f"  PIL échoué ({e}), fallback 150px")
        return 150


async def solve_slider(page: Page) -> bool:
    """Résout le slider CAPTCHA si présent. Retourne True si OK."""

    # Détection du widget
    present = False
    for sel in ['div[class*="captcha"]', 'div[class*="slider"]',
                'div:has-text("Glissez")', '.geetest_widget']:
        try:
            if await page.locator(sel).first.is_visible():
                present = True
                break
        except Exception:
            pass

    if not present:
        log.info("  Pas de slider CAPTCHA")
        return True

    log.info("  🧩 Slider CAPTCHA — résolution en cours...")
    await human_sleep(1, 2)

    # Capture fond et pièce
    bg_bytes, piece_bytes = None, None

    for sel in ['.geetest_canvas_bg', '.captcha-bg', 'canvas', 'img[class*="bg"]']:
        try:
            el = page.locator(sel).first
            if await el.is_visible():
                bg_bytes = await el.screenshot()
                break
        except Exception:
            pass

    for sel in ['.geetest_canvas_slice', '.captcha-piece', 'img[class*="piece"]', 'img[class*="slice"]']:
        try:
            el = page.locator(sel).first
            if await el.is_visible():
                piece_bytes = await el.screenshot()
                break
        except Exception:
            pass

    # Calcul de la distance cible
    if bg_bytes and piece_bytes:
        target_x = find_gap_position(bg_bytes, piece_bytes)
    else:
        target_x = random.randint(120, 180)
        log.warning(f"  Capture impossible, distance aléatoire : {target_x}px")

    # Trouver le handle
    handle = None
    for sel in ['.geetest_slider_button', '.slider-button', '.slider-handle',
                'div[class*="drag"]', 'div[class*="handle"]', '.verify-move-block']:
        try:
            el = page.locator(sel).first
            if await el.is_visible():
                handle = el
                break
        except Exception:
            pass

    if handle is None:
        log.warning("  Handle introuvable")
        return False

    box = await handle.bounding_box()
    if not box:
        return False

    sx = box["x"] + box["width"] / 2
    sy = box["y"] + box["height"] / 2

    # Essais avec distances variées (humainement imparfait)
    distances_to_try = [target_x]
    # Ajouter des variations autour de la cible (comme un humain qui rate un peu)
    for delta in [-15, +15, -30, +30, -45, +45]:
        alt = target_x + delta
        if alt > 0:
            distances_to_try.append(alt)

    for dist in distances_to_try:
        log.info(f"  🖱️  Drag humain : {dist}px (overshoot inclus)")
        await human_drag(page, sx, sy, dist)

        # Vérifier si résolu
        still_there = False
        for sel in ['div[class*="captcha"]', 'div[class*="slider"]', '.geetest_widget']:
            try:
                if await page.locator(sel).first.is_visible():
                    still_there = True
                    break
            except Exception:
                pass

        if not still_there:
            log.info(f"  ✅ Slider résolu avec {dist}px !")
            return True

        # Petite pause avant prochain essai
        await human_sleep(1, 2)

    log.warning("  ⚠️ Slider non résolu, soumission quand même...")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  RETRY
# ══════════════════════════════════════════════════════════════════════════════

async def retry(fn, retries: int = 3, delay: float = 10.0, label: str = ""):
    for attempt in range(1, retries + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt == retries:
                log.error(f"❌ [{label}] Échec définitif ({retries} tentatives) : {e}")
                raise
            log.warning(f"⚠️  [{label}] Tentative {attempt}/{retries} : {e} — retry dans {delay}s")
            await asyncio.sleep(delay)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 1 — SUPER-PARRAIN.COM  (1x/jour)
# ══════════════════════════════════════════════════════════════════════════════

async def bump_super(page: Page):
    cfg  = CONFIG["sites"]["super"]
    name = "super-parrain"
    log.info(f"\n{'─'*50}\n  🌐 super-parrain.com\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)
        await robust_fill(page, 'input[type="email"]',    cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)

        await human_click(page, page.locator(
            'button:has-text("Connexion"), button[type="submit"]'
        ).first)

        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login échoué")

        await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
        await human_sleep(3, 5)

        edit_btns = page.locator('a[href*="modifier"], td:last-child a.btn')
        count = await edit_btns.count()
        log.info(f"  Boutons modifier : {count}")
        bumped = 0

        for i in range(count):
            try:
                btn = edit_btns.nth(i)
                if not await btn.is_visible():
                    continue
                await human_click(page, btn)
                await page.wait_for_load_state("networkidle")
                await human_sleep(2, 3)
                await human_click(page, page.locator('button[type="submit"], input[type="submit"]').first)
                await page.wait_for_load_state("networkidle")
                await human_sleep(2, 3)
                bumped += 1
                log.info(f"  🔼 Code {i+1} remonté")
                await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
                await human_sleep(2, 3)
            except Exception as e:
                log.debug(f"  Erreur bouton {i} : {e}")
                await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")

        log.info(f"  🎯 {bumped} code(s) remonté(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 2 — CODE-PARRAINAGE.NET  (5x/jour)
# ══════════════════════════════════════════════════════════════════════════════

async def bump_code(page: Page):
    cfg  = CONFIG["sites"]["code"]
    name = "code-parrainage"
    log.info(f"\n{'─'*50}\n  🌐 code-parrainage.net\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)
        await robust_fill(page, 'input[type="email"]',    cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)

        # Résolution slider gratuite
        await solve_slider(page)
        await asyncio.sleep(random.uniform(0.8, 1.5))

        await human_click(page, page.locator(
            'button:has-text("Se connecter"), button[type="submit"]'
        ).first)

        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login échoué")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle")
        await human_sleep(3, 5)

        buttons = page.locator(
            'button:has-text("Actualiser"), a:has-text("Actualiser")'
        )
        count = await buttons.count()
        log.info(f"  Boutons Actualiser : {count}")
        bumped = 0

        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                bumped += 1
                log.info(f"  🔼 Actualiser {i+1} cliqué")
                await human_sleep(2, 5)
            except Exception as e:
                log.debug(f"  Erreur {i} : {e}")

        log.info(f"  🎯 {bumped} annonce(s) remontée(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 3 — PARRAINAGE.CO  (5x/jour) ✅
# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page):
    cfg  = CONFIG["sites"]["parrainage"]
    name = "parrainage_co"
    log.info(f"\n{'─'*50}\n  🌐 parrainage.co\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded")
        await human_sleep(3, 5)
        await robust_fill(page, 'input[type="email"], input[name="email"], input#email',          cfg["email"])
        await human_sleep(0.5, 1)
        await robust_fill(page, 'input[type="password"], input[name="password"], input#password', cfg["password"])
        await human_sleep(1, 2)

        await human_click(page, page.locator(
            'button:has-text("Connexion"), button[type="submit"]'
        ).first)

        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login échoué")

        await page.goto(f"{cfg['url']}/account/offers", wait_until="networkidle")
        await human_sleep(3, 5)

        buttons = page.locator(
            'button:has-text("Remettre en haut"), a:has-text("Remettre en haut")'
        )
        count = await buttons.count()
        log.info(f"  Boutons Remettre en haut : {count}")
        bumped = 0

        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                bumped += 1
                log.info(f"  🔼 Remettre en haut {i+1} cliqué")
                await human_sleep(2, 5)
            except Exception as e:
                log.debug(f"  Erreur {i} : {e}")

        log.info(f"  🎯 {bumped} annonce(s) remontée(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
    "super":      bump_super,
    "code":       bump_code,
    "parrainage": bump_parrainage,
}

async def main():
    to_run = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

    log.info("═" * 55)
    log.info("  🚀  Parrainage Auto-Bumper  —  VERSION FINALE")
    log.info(f"  🕐  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"  🎯  Sites : {', '.join(to_run)}")
    log.info("═" * 55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--window-size=1280,800",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
        )

        for site_id in to_run:
            handler = HANDLERS.get(site_id)
            if not handler:
                continue
            cfg = CONFIG["sites"].get(site_id, {})
            if not cfg.get("email"):
                log.warning(f"  ⏭️  {site_id} — credentials manquants")
                continue
            page = await context.new_page()
            try:
                await handler(page)
            except Exception as e:
                log.error(f"  ❌ {site_id} — Erreur : {e}")
            finally:
                await page.close()
                await human_sleep(3, 7)

        await browser.close()

    log.info("\n" + "═" * 55)
    log.info("  ✅  Cycle terminé !")
    log.info("═" * 55)


if __name__ == "__main__":
    asyncio.run(main())
