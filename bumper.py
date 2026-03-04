"""
=============================================================
  Parrainage Auto-Bumper  —  Version Login Fix
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
    """
    Remplit un champ de manière robuste :
    1. Attend que le champ soit visible
    2. Clique dessus
    3. Utilise fill() (le plus fiable pour les mots de passe complexes)
    4. Vérifie que la valeur est bien saisie
    """
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=15000)
    await locator.click()
    await asyncio.sleep(0.3)
    await locator.fill(value)
    await asyncio.sleep(0.3)
    # Vérification
    actual = await locator.input_value()
    if actual != value:
        # Fallback : JavaScript direct
        log.warning(f"  fill() incomplet, tentative JS...")
        await page.evaluate(
            "el => { el.value = arguments[1]; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
            await locator.element_handle(),
            value
        )
    log.info(f"  Champ rempli : {selector[:30]} = {'*' * len(value)}")


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


async def verify_login(page: Page, login_url: str, name: str) -> bool:
    """Vérifie que le login a bien fonctionné en contrôlant l'URL."""
    current = page.url
    log.info(f"  [{name}] URL après login : {current}")
    if login_url in current:
        log.error(f"  [{name}] ❌ Login échoué — toujours sur la page de login !")
        # Screenshot pour debug
        await page.screenshot(path=f"login_failed_{name}.png", full_page=True)
        return False
    log.info(f"  [{name}] ✓ Login réussi !")
    return True


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
#  2CAPTCHA
# ══════════════════════════════════════════════════════════════════════════════

class TwoCaptcha:
    BASE = "http://2captcha.com"

    def __init__(self, key: str):
        self.key     = key
        self.enabled = bool(key)

    async def solve_recaptcha(self, site_key: str, page_url: str) -> str:
        if not self.enabled:
            return ""
        log.info("  🔐 Résolution reCAPTCHA v2...")
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(f"{self.BASE}/in.php", params={
                "key": self.key, "method": "userrecaptcha",
                "googlekey": site_key, "pageurl": page_url, "json": 1,
            })
            d = r.json()
            if d.get("status") != 1:
                raise RuntimeError(f"Soumission échouée : {d}")
        return await self._poll(d["request"])

    async def solve_image(self, image_bytes: bytes) -> str:
        if not self.enabled:
            return ""
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.BASE}/in.php",
                files={"file": ("captcha.png", image_bytes)},
                data={"key": self.key, "method": "post", "json": 1},
            )
            d = r.json()
            if d.get("status") != 1:
                raise RuntimeError(f"Soumission échouée : {d}")
        return await self._poll(d["request"])

    async def _poll(self, task_id: str, timeout: int = 120) -> str:
        async with httpx.AsyncClient(timeout=30) as c:
            for _ in range(timeout // 5):
                await human_sleep(5, 7)
                r = await c.get(f"{self.BASE}/res.php", params={
                    "key": self.key, "action": "get", "id": task_id, "json": 1,
                })
                d = r.json()
                if d.get("status") == 1:
                    return d["request"]
                if d.get("request") != "CAPCHA_NOT_READY":
                    raise RuntimeError(f"Erreur résultat : {d}")
        raise TimeoutError("2Captcha timeout")


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 1 — SUPER-PARRAIN.COM
# ══════════════════════════════════════════════════════════════════════════════

async def bump_super(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["super"]
    name = "super_parrain"
    log.info(f"\n{'─'*50}\n  🌐 super-parrain.com\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)

        await robust_fill(page, 'input[type="email"]',    cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)

        try:
            await page.locator('[data-sitekey]').wait_for(timeout=4000)
            sk    = await page.locator('[data-sitekey]').get_attribute("data-sitekey")
            token = await captcha.solve_recaptcha(sk, page.url)
            if token:
                await page.evaluate(
                    'document.getElementById("g-recaptcha-response").innerHTML = arguments[0]', token
                )
        except PWTimeout:
            log.info(f"  Pas de reCAPTCHA")

        await human_click(page, page.locator('button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login échoué")

        await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
        await human_sleep(3, 5)

        # Cliquer chaque bouton crayon et sauvegarder
        edit_btns = page.locator('a[href*="modifier"], a[href*="edit"], td:last-child a, .btn-warning')
        count = await edit_btns.count()
        log.info(f"  Boutons modifier trouvés : {count}")
        bumped = 0

        for i in range(count):
            try:
                btn = edit_btns.nth(i)
                if not await btn.is_visible():
                    continue
                href = await btn.get_attribute("href") or ""
                log.info(f"  Bouton {i} : href={href}")
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

    await retry(_do, retries=3, label="super-parrain")


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 2 — CODE-PARRAINAGE.NET
# ══════════════════════════════════════════════════════════════════════════════

async def bump_code(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["code"]
    name = "code_parrainage"
    log.info(f"\n{'─'*50}\n  🌐 code-parrainage.net\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)

        # Log la page pour debug
        log.info(f"  URL login : {page.url}")

        await robust_fill(page, 'input[type="email"]',    cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)

        # Cherche le bouton submit avec plusieurs sélecteurs
        submit = page.locator(
            'button[type="submit"], '
            'input[type="submit"], '
            'button:has-text("Se connecter"), '
            'button:has-text("Connexion"), '
            'button:has-text("Login")'
        ).first
        log.info(f"  Clic sur submit...")
        await human_click(page, submit)

        # Attend la redirection
        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login échoué")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle")
        await human_sleep(3, 5)
        log.info(f"  URL moncompte : {page.url}")

        # Liste tous les boutons pour debug
        all_btns = page.locator("button, a.btn, input[type=submit]")
        total = await all_btns.count()
        log.info(f"  Total boutons : {total}")
        for i in range(min(total, 15)):
            try:
                txt = await all_btns.nth(i).inner_text()
                log.info(f"  Bouton {i} : '{txt.strip()[:50]}'")
            except Exception:
                pass

        buttons = page.locator(
            'button:has-text("Actualiser"), '
            'a:has-text("Actualiser"), '
            'button:has-text("Mettre à jour"), '
            'a:has-text("Mettre à jour")'
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

    await retry(_do, retries=3, label="code-parrainage")


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 3 — PARRAINAGE.CO
# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["parrainage"]
    name = "parrainage_co"
    log.info(f"\n{'─'*50}\n  🌐 parrainage.co\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded")
        await human_sleep(3, 5)
        log.info(f"  URL : {page.url}")

        await robust_fill(page, 'input[type="email"], input[name="email"], input#email',          cfg["email"])
        await human_sleep(0.5, 1)
        await robust_fill(page, 'input[type="password"], input[name="password"], input#password', cfg["password"])
        await human_sleep(1, 2)

        # CAPTCHA image si présent
        try:
            captcha_img = page.locator('img.captcha, img[alt*="captcha" i], #captcha img').first
            await captcha_img.wait_for(timeout=4000)
            text = await captcha.solve_image(await captcha_img.screenshot())
            if text:
                await robust_fill(page, 'input.captcha-input, input[name="captcha"]', text)
        except PWTimeout:
            log.info(f"  Pas d'image CAPTCHA")

        submit = page.locator(
            'button[type="submit"], '
            'input[type="submit"], '
            'button:has-text("Connexion"), '
            'button:has-text("Se connecter"), '
            'button.login-btn'
        ).first
        await human_click(page, submit)

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
        log.info(f"  URL offers : {page.url}")

        # Debug boutons
        all_btns = page.locator("button, a.btn")
        total = await all_btns.count()
        log.info(f"  Total boutons : {total}")
        for i in range(min(total, 15)):
            try:
                txt = await all_btns.nth(i).inner_text()
                log.info(f"  Bouton {i} : '{txt.strip()[:50]}'")
            except Exception:
                pass

        buttons = page.locator(
            'button:has-text("Remettre en haut"), '
            'a:has-text("Remettre en haut"), '
            'button:has-text("Remonter"), '
            'a:has-text("Remonter")'
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

    await retry(_do, retries=3, label="parrainage.co")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
    "super":      bump_super,
    "code":       bump_code,
    "parrainage": bump_parrainage,
}

async def main():
    captcha = TwoCaptcha(CONFIG["two_captcha_key"])
    to_run  = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

    log.info("═" * 55)
    log.info("  🚀  Parrainage Auto-Bumper  —  Login Fix")
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
                await handler(page, captcha)
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
