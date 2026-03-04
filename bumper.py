"""
=============================================================
  Parrainage Auto-Bumper  —  Version DEBUG
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
#  COMPORTEMENT HUMAIN
# ══════════════════════════════════════════════════════════════════════════════

async def human_sleep(a: float = 2.0, b: float = 6.0):
    await asyncio.sleep(random.uniform(a, b))


async def human_type(page: Page, selector: str, text: str):
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=15000)
    await locator.click()
    await locator.fill("")
    for char in text:
        await locator.type(char, delay=random.randint(60, 150))


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


async def debug_screenshot(page: Page, name: str):
    """Prend un screenshot et affiche le HTML des boutons trouvés."""
    path = f"debug_{name}.png"
    await page.screenshot(path=path, full_page=True)
    log.info(f"  📸 Screenshot sauvegardé : {path}")

    # Affiche le HTML de toute la page pour voir les boutons disponibles
    html = await page.content()
    # Cherche les mots-clés liés aux boutons de bump
    keywords = ["Actualiser", "Remettre", "Remonter", "Bump", "Up", "btn", "button"]
    for kw in keywords:
        idx = html.lower().find(kw.lower())
        if idx != -1:
            snippet = html[max(0, idx-50):idx+100].replace("\n", " ").strip()
            log.info(f"  🔍 Trouvé '{kw}' dans HTML : ...{snippet}...")


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
        log.info("  🔐 Résolution image CAPTCHA...")
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
                    log.info("  ✅ CAPTCHA résolu !")
                    return d["request"]
                if d.get("request") != "CAPCHA_NOT_READY":
                    raise RuntimeError(f"Erreur résultat : {d}")
        raise TimeoutError("2Captcha timeout")


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 1 — SUPER-PARRAIN.COM
# ══════════════════════════════════════════════════════════════════════════════

async def bump_super(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["super"]
    name = "super-parrain.com"
    log.info(f"\n{'─'*50}\n  🌐 {name}\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep()
        await human_type(page, 'input[type="email"]',    cfg["email"])
        await human_type(page, 'input[type="password"]', cfg["password"])
        await human_sleep()

        try:
            await page.locator('[data-sitekey]').wait_for(timeout=4000)
            sk    = await page.locator('[data-sitekey]').get_attribute("data-sitekey")
            token = await captcha.solve_recaptcha(sk, page.url)
            if token:
                await page.evaluate(
                    'document.getElementById("g-recaptcha-response").innerHTML = arguments[0]', token
                )
        except PWTimeout:
            log.info(f"  [{name}] Pas de reCAPTCHA")

        await human_click(page, page.locator('button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(4, 6)
        log.info(f"  [{name}] ✓ Connecté — URL : {page.url}")

        await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
        await human_sleep(3, 5)
        log.info(f"  [{name}] Page chargée — URL : {page.url}")

        # 📸 DEBUG
        await debug_screenshot(page, "super_parrain")

        # Cherche les boutons crayon (modifier) — icône uniquement, pas de texte
        edit_btns = page.locator('a.btn, button.btn').filter(has=page.locator('i, svg, span'))
        count = await edit_btns.count()
        log.info(f"  [{name}] Boutons trouvés : {count}")

        bumped = 0
        for i in range(count):
            btn = edit_btns.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                await page.wait_for_load_state("networkidle")
                await human_sleep(2, 4)

                save_btn = page.locator(
                    'button[type="submit"], input[type="submit"], '
                    'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
                    'button:has-text("Modifier"), button:has-text("Mettre à jour"), '
                    'button:has-text("Valider")'
                ).first
                await human_click(page, save_btn)
                await page.wait_for_load_state("networkidle")
                await human_sleep(2, 4)
                bumped += 1
                log.info(f"  🔼 [{name}] Code {i+1} remonté")

                await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
                await human_sleep(2, 3)
            except Exception as e:
                log.debug(f"  [{name}] Erreur {i} : {e}")
                await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
                await human_sleep(2, 3)

        log.info(f"  [{name}] 🎯 {bumped} code(s) remonté(s) ✓")

    await retry(_do, retries=1, label=name)  # 1 seul essai en mode debug


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 2 — CODE-PARRAINAGE.NET
# ══════════════════════════════════════════════════════════════════════════════

async def bump_code(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["code"]
    name = "code-parrainage.net"
    log.info(f"\n{'─'*50}\n  🌐 {name}\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep()
        await human_type(page, 'input[type="email"]',    cfg["email"])
        await human_type(page, 'input[type="password"]', cfg["password"])
        await human_sleep()
        await human_click(page, page.locator('button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(4, 6)
        log.info(f"  [{name}] ✓ Connecté — URL : {page.url}")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle")
        await human_sleep(3, 5)
        log.info(f"  [{name}] Page chargée — URL : {page.url}")

        # 📸 DEBUG
        await debug_screenshot(page, "code_parrainage")

        # Compte tous les boutons visibles sur la page
        all_btns = page.locator("button, a.btn, input[type=button], input[type=submit]")
        total = await all_btns.count()
        log.info(f"  [{name}] Total boutons sur la page : {total}")
        for i in range(min(total, 20)):
            btn = all_btns.nth(i)
            try:
                txt = await btn.inner_text()
                log.info(f"  [{name}] Bouton {i} : '{txt.strip()}'")
            except Exception:
                pass

        # Clic sur "Actualiser"
        buttons = page.locator('button:has-text("Actualiser"), a:has-text("Actualiser")')
        count = await buttons.count()
        log.info(f"  [{name}] Boutons Actualiser trouvés : {count}")
        bumped = 0
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                bumped += 1
                log.info(f"  🔼 [{name}] Actualiser cliqué ({i+1})")
                await human_sleep(2, 4)
            except Exception as e:
                log.debug(f"  [{name}] Erreur {i} : {e}")

        log.info(f"  [{name}] 🎯 {bumped} annonce(s) remontée(s) ✓")

    await retry(_do, retries=1, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 3 — PARRAINAGE.CO
# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["parrainage"]
    name = "parrainage.co"
    log.info(f"\n{'─'*50}\n  🌐 {name}\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded")
        await human_sleep(3, 5)
        await human_type(page, 'input[type="email"], input[name="email"], input#email',          cfg["email"])
        await human_sleep()
        await human_type(page, 'input[type="password"], input[name="password"], input#password', cfg["password"])
        await human_sleep()

        try:
            captcha_img = page.locator('img.captcha, img[alt*="captcha" i], #captcha img').first
            await captcha_img.wait_for(timeout=4000)
            text = await captcha.solve_image(await captcha_img.screenshot())
            if text:
                await human_type(page, 'input.captcha-input, input[name="captcha"]', text)
        except PWTimeout:
            log.info(f"  [{name}] Pas d'image CAPTCHA")

        await human_click(page, page.locator('button.login-btn, button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(4, 6)
        log.info(f"  [{name}] ✓ Connecté — URL : {page.url}")

        await page.goto(f"{cfg['url']}/account/offers", wait_until="networkidle")
        await human_sleep(3, 5)
        log.info(f"  [{name}] Page chargée — URL : {page.url}")

        # 📸 DEBUG
        await debug_screenshot(page, "parrainage_co")

        # Liste tous les boutons
        all_btns = page.locator("button, a.btn")
        total = await all_btns.count()
        log.info(f"  [{name}] Total boutons sur la page : {total}")
        for i in range(min(total, 20)):
            btn = all_btns.nth(i)
            try:
                txt = await btn.inner_text()
                log.info(f"  [{name}] Bouton {i} : '{txt.strip()}'")
            except Exception:
                pass

        # Clic sur "Remettre en haut"
        buttons = page.locator('button:has-text("Remettre en haut"), a:has-text("Remettre en haut")')
        count = await buttons.count()
        log.info(f"  [{name}] Boutons 'Remettre en haut' trouvés : {count}")
        bumped = 0
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                bumped += 1
                log.info(f"  🔼 [{name}] Remettre en haut cliqué ({i+1})")
                await human_sleep(2, 4)
            except Exception as e:
                log.debug(f"  [{name}] Erreur {i} : {e}")

        log.info(f"  [{name}] 🎯 {bumped} annonce(s) remontée(s) ✓")

    await retry(_do, retries=1, label=name)


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
    log.info("  🚀  Parrainage Auto-Bumper  —  Version DEBUG")
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
