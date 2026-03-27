"""
Parrainage Auto-Bumper ULTIMATE
super-parrain.com | code-parrainage.net | parrainage.co
"""

import asyncio, os, io, random, logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright

# ---------------- LOGGING ----------------

log = logging.getLogger("bumper")
log.setLevel(logging.INFO)
log.propagate = False

fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")

for h in [
    logging.StreamHandler(),
    RotatingFileHandler("bumper.log", maxBytes=1_000_000, backupCount=2)
]:
    h.setFormatter(fmt)
    log.addHandler(h)

# ---------------- CONFIG ----------------

TARGET_SITES = [s.strip() for s in os.environ.get("TARGET_SITES", "").split(",") if s.strip()]

CONFIG = {
    "super": {
        "url": "https://www.super-parrain.com",
        "email": os.getenv("SUPER_PARRAIN_EMAIL"),
        "password": os.getenv("SUPER_PARRAIN_PASSWORD")
    },
    "code": {
        "url": "https://code-parrainage.net",
        "email": os.getenv("CODE_PARRAINAGE_EMAIL"),
        "password": os.getenv("CODE_PARRAINAGE_PASSWORD")
    },
    "parrainage": {
        "url": "https://parrainage.co",
        "rm_cookie": os.getenv("PARRAINAGE_CO_RM_COOKIE")
    }
}

# ---------------- HUMAN ----------------

async def human_sleep(a=1.5, b=4.5):
    await asyncio.sleep(random.uniform(a, b))

async def human_click(locator):
    try:
        await locator.wait_for(timeout=10000)
        await asyncio.sleep(random.uniform(0.2, 0.6))
        await locator.click(delay=random.randint(50, 150))
    except:
        pass

async def human_type(locator, text):
    await locator.click()
    for char in text:
        await locator.type(char, delay=random.randint(50, 120))

# ---------------- STEALTH ----------------

async def create_browser():
    pw = await async_playwright().start()

    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage"
        ]
    )

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="fr-FR",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36"
    )

    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr']});
    """)

    return pw, browser, context

# ---------------- RETRY ----------------

async def retry(fn, name):
    for i in range(3):
        try:
            return await fn()
        except Exception as e:
            log.warning(f"{name} retry {i+1}/3 -> {e}")
            await asyncio.sleep(5)
    log.error(f"{name} FAILED")

# ---------------- SUPER ----------------

async def bump_super(page):
    cfg = CONFIG["super"]

    async def run():
        await page.goto(cfg["url"] + "/login")
        await human_sleep()

        await human_type(page.locator("input[type=email]"), cfg["email"])
        await human_type(page.locator("input[type=password]"), cfg["password"])

        await human_click(page.locator("button[type=submit]"))

        await page.wait_for_load_state("networkidle")
        await human_sleep()

        await page.goto(cfg["url"] + "/tableau-de-bord/codes-promo")

        buttons = page.locator("a[href*='edit']")
        count = await buttons.count()

        for i in range(count):
            await buttons.nth(i).click()
            await human_sleep()
            await human_click(page.locator("button[type=submit]"))

        log.info(f"SUPER done ({count})")

    await retry(run, "SUPER")

# ---------------- CODE ----------------

async def bump_code(page):
    cfg = CONFIG["code"]

    async def run():
        await page.goto(cfg["url"] + "/login")
        await human_sleep()

        await human_type(page.locator("input[type=email]"), cfg["email"])
        await human_type(page.locator("input[type=password]"), cfg["password"])

        await human_click(page.locator("button[type=submit]"))

        await page.wait_for_load_state("networkidle")
        await human_sleep()

        await page.goto(cfg["url"] + "/moncompte")

        buttons = page.locator("text=Actualiser")
        count = await buttons.count()

        for i in range(count):
            await human_click(buttons.nth(i))
            await human_sleep()

        log.info(f"CODE done ({count})")

    await retry(run, "CODE")

# ---------------- PARRAINAGE ----------------

async def bump_parrainage(page):
    cfg = CONFIG["parrainage"]

    async def run():
        await page.context.add_cookies([{
            "name": "parrainageco_rm",
            "value": cfg["rm_cookie"],
            "domain": "parrainage.co",
            "path": "/"
        }])

        await page.goto(cfg["url"] + "/account/offers")
        await human_sleep()

        await page.goto(cfg["url"] + "/account/offers/boost-all")
        await human_sleep()

        log.info("PARRAINAGE done")

    await retry(run, "PARRAINAGE")

# ---------------- MAIN ----------------

async def main():
    sites = TARGET_SITES or ["super", "code", "parrainage"]

    log.info("START BUMPER")
    log.info(f"Sites: {sites}")

    pw, browser, context = await create_browser()

    for site in sites:
        page = await context.new_page()

        try:
            if site == "super":
                await bump_super(page)
            elif site == "code":
                await bump_code(page)
            elif site == "parrainage":
                await bump_parrainage(page)

        except Exception as e:
            log.error(f"{site} crash -> {e}")

        await page.close()
        await human_sleep()

    await browser.close()
    await pw.stop()

    log.info("DONE")

if __name__ == "__main__":
    asyncio.run(main())
