"""
=============================================================
  Parrainage Auto-Bumper  —  Version Finale (Fusion)
  super-parrain.com | code-parrainage.net | parrainage.co
=============================================================
  Meilleur des 2 versions :
  ✅ human_click avec mouvement souris réaliste  (GPT-5)
  ✅ --disable-blink-features anti-détection     (GPT-5)
  ✅ Credentials séparés par site                (Claude)
  ✅ Retry automatique x3                        (Claude)
  ✅ Délais aléatoires partout                   (Claude + GPT-5)
  ✅ Frappe humaine caractère par caractère       (Claude + GPT-5)
  ✅ Drag slider avec courbe smooth-step          (Claude)
  ✅ Async (meilleur pour GitHub Actions)         (Claude)
=============================================================
"""

import asyncio
import os
import base64
import random
import logging
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
import httpx

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bumper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bumper")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (GitHub Secrets → variables d'environnement)
# ══════════════════════════════════════════════════════════════════════════════

# TARGET_SITES = "super", "code", "parrainage"  (séparés par virgule)
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

BUMP_SELECTORS = [
    'button:has-text("Remonter")',
    'button:has-text("Bump")',
    'button:has-text("Up")',
    'button:has-text("Actualiser")',
    'button:has-text("Refresh")',
    'a:has-text("Remonter")',
    'a:has-text("Up")',
    '.btn-bump', '.btn-boost', '.bump-btn', '.btn-up',
]

# ══════════════════════════════════════════════════════════════════════════════
#  COMPORTEMENT HUMAIN
# ══════════════════════════════════════════════════════════════════════════════

async def human_sleep(a: float = 2.0, b: float = 6.0):
    """Pause aléatoire."""
    await asyncio.sleep(random.uniform(a, b))


async def human_type(page: Page, selector: str, text: str):
    """Tape du texte caractère par caractère avec délai aléatoire."""
    el = page.locator(selector).first
    await el.click()
    await el.fill("")
    for char in text:
        await el.press(char)
        await asyncio.sleep(random.uniform(0.06, 0.16))


async def human_click(page: Page, locator):
    """
    Clic humain : déplace la souris vers l'élément en 20 étapes
    puis clique avec une micro-pause. (inspiré GPT-5)
    """
    try:
        box = await locator.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + random.randint(2, int(box["width"]  - 2)),
                box["y"] + random.randint(2, int(box["height"] - 2)),
                steps=20,
            )
        await human_sleep(0.3, 1.2)
        await locator.click()
    except Exception:
        pass


async def click_all_bump_buttons(page: Page, site_name: str) -> int:
    """Clique sur tous les boutons de bump visibles sur la page."""
    count = 0
    for sel in BUMP_SELECTORS:
        for btn in await page.locator(sel).all():
            try:
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                count += 1
                log.info(f"  🔼 [{site_name}] Bouton cliqué ({sel})")
                await human_sleep(2.0, 5.0)
            except Exception:
                pass
    return count


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
            log.warning("  ⚠️  2Captcha non configuré")
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

    async def solve_slider(self, piece: bytes, bg: bytes) -> int:
        if not self.enabled:
            return 0
        log.info("  🔐 Résolution slider CAPTCHA...")
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.BASE}/in.php", data={
                "key": self.key, "method": "base64",
                "body":            base64.b64encode(piece).decode(),
                "imginstructions": base64.b64encode(bg).decode(),
                "json": 1,
            })
            d = r.json()
            if d.get("status") != 1:
                raise RuntimeError(f"Soumission échouée : {d}")
        result = await self._poll(d["request"])
        try:
            return int(result.split("=")[-1])
        except Exception:
            return int(result)

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
#  HANDLER 1 — SUPER-PARRAIN.COM  (1x/jour)
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

        # reCAPTCHA v2 si présent
        try:
            await page.locator('[data-sitekey]').wait_for(timeout=4000)
            sk    = await page.locator('[data-sitekey]').get_attribute("data-sitekey")
            token = await captcha.solve_recaptcha(sk, page.url)
            if token:
                await page.evaluate(
                    'document.getElementById("g-recaptcha-response").innerHTML = arguments[0]',
                    token
                )
        except PWTimeout:
            log.info(f"  [{name}] Pas de reCAPTCHA")

        await human_click(page, page.locator('button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(5, 8)
        log.info(f"  [{name}] ✓ Connecté")

        await page.goto(f"{cfg['url']}/mes-annonces", wait_until="networkidle")
        await human_sleep(3, 5)

        n = await click_all_bump_buttons(page, name)
        log.info(f"  [{name}] 🎯 {n} code(s) remonté(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 2 — CODE-PARRAINAGE.NET  (5x/jour)
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

        # Slider CAPTCHA si présent
        try:
            piece_el = page.locator('.captcha-piece, .slider-piece').first
            bg_el    = page.locator('.captcha-bg, .slider-bg').first
            await piece_el.wait_for(timeout=4000)

            x_pos  = await captcha.solve_slider(
                await piece_el.screenshot(),
                await bg_el.screenshot()
            )
            handle = page.locator('.slider-handle, .captcha-handle').first
            box    = await handle.bounding_box()
            sx     = box["x"] + box["width"]  / 2
            sy     = box["y"] + box["height"] / 2

            await page.mouse.move(sx, sy)
            await page.mouse.down()
            steps = random.randint(25, 40)
            for i in range(steps):
                t       = i / steps
                eased   = t * t * (3 - 2 * t)          # smooth-step
                await page.mouse.move(
                    sx + x_pos * eased,
                    sy + random.uniform(-1.5, 1.5),     # micro-jitter
                )
                await asyncio.sleep(random.uniform(0.01, 0.03))
            await page.mouse.up()
            await human_sleep(1, 2)

        except PWTimeout:
            log.info(f"  [{name}] Pas de slider CAPTCHA")

        await human_click(page, page.locator('button[type="submit"]').first)
        await page.wait_for_load_state("networkidle")
        await human_sleep(5, 8)
        log.info(f"  [{name}] ✓ Connecté")

        await page.goto(f"{cfg['url']}/mes-annonces", wait_until="networkidle")
        await human_sleep(3, 5)

        n = await click_all_bump_buttons(page, name)
        log.info(f"  [{name}] 🎯 {n} annonce(s) remontée(s) ✓")

    await retry(_do, retries=3, label=name)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER 3 — PARRAINAGE.CO  (5x/jour)
# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page, captcha: TwoCaptcha):
    cfg  = CONFIG["sites"]["parrainage"]
    name = "parrainage.co"
    log.info(f"\n{'─'*50}\n  🌐 {name}\n{'─'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep()

        await human_type(page, 'input[type="email"]',    cfg["email"])
        await human_type(page, 'input[type="password"]', cfg["password"])
        await human_sleep()

        # Image CAPTCHA si présent
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
        await human_sleep(5, 8)
        log.info(f"  [{name}] ✓ Connecté")

        await page.goto(f"{cfg['url']}/account/offers", wait_until="networkidle")
        await human_sleep(3, 5)

        n = await click_all_bump_buttons(page, name)
        log.info(f"  [{name}] 🎯 {n} annonce(s) remontée(s) ✓")

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
    captcha = TwoCaptcha(CONFIG["two_captcha_key"])
    to_run  = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

    log.info("═" * 55)
    log.info("  🚀  Parrainage Auto-Bumper  —  Version Finale")
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
                "--disable-blink-features=AutomationControlled",  # ← anti-détection (GPT-5)
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
                log.warning(f"  ⚠️  Site inconnu ignoré : '{site_id}'")
                continue

            cfg = CONFIG["sites"].get(site_id, {})
            if not cfg.get("email"):
                log.warning(f"  ⏭️  {site_id} — credentials manquants, ignoré")
                continue

            page = await context.new_page()
            try:
                await handler(page, captcha)
            except Exception as e:
                log.error(f"  ❌ {site_id} — Erreur finale : {e}")
            finally:
                await page.close()
                await human_sleep(3, 7)  # pause entre sites

        await browser.close()

    log.info("\n" + "═" * 55)
    log.info("  ✅  Cycle terminé !")
    log.info("═" * 55)


if __name__ == "__main__":
    asyncio.run(main())
