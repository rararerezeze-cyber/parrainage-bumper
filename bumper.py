"""
=============================================================
  Parrainage Auto-Bumper  —  Version Finale v2
  super-parrain.com | code-parrainage.net | parrainage.co
=============================================================
"""

import asyncio
import os
import base64
import random
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
import httpx

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
    "two_captcha_key": os.environ.get("TWOCAPTCHA_KEY", ""),
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
#  UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

async def human_sleep(a: float = 2.0, b: float = 6.0):
    await asyncio.sleep(random.uniform(a, b))


async def robust_fill(page: Page, selector: str, value: str):
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=15000)
    await locator.click()
    await asyncio.sleep(0.3)
    await locator.fill(value)
    await asyncio.sleep(0.3)


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
        await human_sleep(0.2, 0.8)
        await locator.click()
    except Exception as e:
        log.debug(f"human_click ignoré : {e}")


async def verify_login(page: Page, login_url_fragment: str, name: str) -> bool:
    current = page.url
    log.info(f"  [{name}] URL après login : {current}")
    if login_url_fragment in current:
        log.error(f"  [{name}] ❌ Login échoué — toujours sur la page de login !")
        return False
    log.info(f"  [{name}] ✓ Login réussi !")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  SLIDER CAPTCHA — code-parrainage.net
#  Méthode : screenshot complet du widget → 2Captcha → position X
# ══════════════════════════════════════════════════════════════════════════════

async def solve_slider_captcha(page: Page, captcha_key: str) -> bool:
    """
    Résout le slider CAPTCHA de code-parrainage.net.
    Essaie d'abord via 2Captcha, puis tente un drag aléatoire en fallback.
    Retourne True si réussi.
    """
    try:
        # Attendre que le widget soit chargé
        widget = page.locator('.geetest_widget, .captcha-widget, [class*="slider"], [class*="captcha"]').first
        await widget.wait_for(timeout=8000)
        log.info("  Slider CAPTCHA détecté")
    except PWTimeout:
        log.info("  Pas de slider CAPTCHA visible")
        return True  # Pas de captcha = OK

    # ── Méthode 1 : 2Captcha si clé disponible ──
    if captcha_key:
        try:
            result = await solve_slider_via_2captcha(page, captcha_key)
            if result:
                return True
        except Exception as e:
            log.warning(f"  2Captcha slider échoué : {e}, tentative drag manuel...")

    # ── Méthode 2 : Drag progressif (essai aléatoire dans la zone probable) ──
    return await drag_slider_manual(page)


async def solve_slider_via_2captcha(page: Page, captcha_key: str) -> bool:
    """Envoie le screenshot du captcha à 2Captcha pour obtenir la position X."""
    try:
        # Screenshot du widget entier
        widget = page.locator(
            '.geetest_widget, .captcha-widget, '
            '[class*="slide"], [class*="captcha"], '
            'div:has(> img):has(> [class*="arrow"])'
        ).first

        # Fallback : screenshot de la zone visible
        try:
            img_bytes = await widget.screenshot()
        except Exception:
            img_bytes = await page.screenshot()

        log.info("  📤 Envoi screenshot à 2Captcha...")
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("http://2captcha.com/in.php",
                files={"file": ("captcha.png", img_bytes)},
                data={
                    "key": captcha_key,
                    "method": "post",
                    "textinstructions": "Drag the slider to the correct position. Reply with only the X coordinate number.",
                    "json": 1,
                },
            )
            d = r.json()
            if d.get("status") != 1:
                raise RuntimeError(f"Soumission échouée : {d}")
            task_id = d["request"]

        # Attendre le résultat
        result_text = None
        async with httpx.AsyncClient(timeout=30) as c:
            for _ in range(24):  # ~120s max
                await asyncio.sleep(5)
                r = await c.get("http://2captcha.com/res.php", params={
                    "key": captcha_key, "action": "get", "id": task_id, "json": 1,
                })
                d = r.json()
                if d.get("status") == 1:
                    result_text = d["request"]
                    break
                if d.get("request") != "CAPCHA_NOT_READY":
                    raise RuntimeError(f"Erreur : {d}")

        if not result_text:
            raise RuntimeError("Timeout 2Captcha")

        # Parser la position X
        try:
            x_offset = int(''.join(filter(str.isdigit, result_text.split()[0])))
        except Exception:
            x_offset = 150  # fallback
        log.info(f"  2Captcha → X={x_offset}")

        return await perform_slider_drag(page, x_offset)

    except Exception as e:
        log.warning(f"  2Captcha slider : {e}")
        return False


async def drag_slider_manual(page: Page) -> bool:
    """Tente plusieurs drags progressifs jusqu'à réussite."""
    log.info("  Tentative drag slider manuel...")

    # Différentes distances à essayer
    distances = [120, 180, 100, 140, 200, 80, 160, 220]

    for dist in distances:
        try:
            success = await perform_slider_drag(page, dist)
            await asyncio.sleep(1.5)

            # Vérifier si le captcha est passé (disparition du widget ou changement d'état)
            still_visible = await page.locator(
                '[class*="captcha"], [class*="slider"], .geetest_widget'
            ).first.is_visible()

            if not still_visible:
                log.info(f"  ✅ Slider résolu avec distance {dist}px !")
                return True

            log.debug(f"  Distance {dist}px insuffisante, on réessaie...")
            await asyncio.sleep(1)

        except Exception as e:
            log.debug(f"  Drag {dist}px échoué : {e}")

    log.warning("  ⚠️ Slider non résolu après toutes les tentatives")
    return False


async def perform_slider_drag(page: Page, x_offset: int) -> bool:
    """Effectue le drag du slider avec mouvement humain."""
    try:
        # Cherche le bouton/handle du slider
        handle = page.locator(
            '.geetest_slider_button, .slider-button, .slider-handle, '
            '[class*="drag"], [class*="handle"], '
            'div[style*="cursor"] > div, '
            'div:has(> svg):not(:has(img))'
        ).first

        try:
            await handle.wait_for(timeout=5000)
        except PWTimeout:
            # Fallback : cherche la flèche →
            handle = page.locator('div:has-text("→"), div:has-text("➜"), [class*="arrow"]').first
            await handle.wait_for(timeout=3000)

        box = await handle.bounding_box()
        if not box:
            return False

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        log.info(f"  Drag depuis ({start_x:.0f}, {start_y:.0f}) vers +{x_offset}px")

        # Mouvement humain avec accélération/décélération
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.3)
        await page.mouse.down()
        await asyncio.sleep(0.1)

        steps = random.randint(30, 50)
        for i in range(steps):
            t = i / steps
            # Ease out : accélère puis ralentit
            eased = t * (2 - t)
            jitter_y = random.uniform(-1, 1)
            await page.mouse.move(
                start_x + x_offset * eased,
                start_y + jitter_y,
            )
            await asyncio.sleep(random.uniform(0.008, 0.025))

        # Position finale exacte
        await page.mouse.move(start_x + x_offset, start_y)
        await asyncio.sleep(0.3)
        await page.mouse.up()
        await asyncio.sleep(1)
        return True

    except Exception as e:
        log.debug(f"  perform_slider_drag : {e}")
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

async def bump_super(page: Page, captcha_key: str):
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

        edit_btns = page.locator('a[href*="modifier"], a[href*="edit"], td:last-child a.btn')
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

                save = page.locator('button[type="submit"], input[type="submit"]').first
                await human_click(page, save)
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
#  Login avec slider CAPTCHA obligatoire
# ══════════════════════════════════════════════════════════════════════════════

async def bump_code(page: Page, captcha_key: str):
    cfg  = CONFIG["sites"]["code"]
    name = "code-parrainage"
    log.info(f"\n{'─'*50}\n  🌐 code-parrainage.net\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)

        await robust_fill(page, 'input[type="email"]',    cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)

        # ✅ Résoudre le slider CAPTCHA avant de soumettre
        slider_ok = await solve_slider_captcha(page, captcha_key)
        if not slider_ok:
            log.warning("  Slider non résolu, tentative de soumission quand même...")

        await asyncio.sleep(1)

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
            'button:has-text("Actualiser"), a:has-text("Actualiser"), '
            'button:has-text("Mettre à jour"), a:has-text("Mettre à jour")'
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
                await human_sleep(2, 4)
            except Exception as e:
                log.debug(f"  Erreur {i} : {e}")

        log.info(f"  🎯 {bumped} annonce(s) remontée(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 3 — PARRAINAGE.CO  (5x/jour) ✅ FONCTIONNE
# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page, captcha_key: str):
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
                await human_sleep(2, 4)
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
    captcha_key = CONFIG["two_captcha_key"]
    to_run = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

    log.info("═" * 55)
    log.info("  🚀  Parrainage Auto-Bumper  —  v2 Final")
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
                await handler(page, captcha_key)
            except Exception as e:
                log.error(f"  ❌ {site_id} — Erreur : {e}")
            finally:
                await page.close()
                await human_sleep(3, 5)

        await browser.close()

    log.info("\n" + "═" * 55)
    log.info("  ✅  Cycle terminé !")
    log.info("═" * 55)


if __name__ == "__main__":
    asyncio.run(main())
