"""
Parrainage Auto-Bumper
super-parrain.com | code-parrainage.net | parrainage.co
"""

import asyncio, os, io, random, logging, re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# -- LOGGING ------------------------------------------------------------------
log = logging.getLogger("bumper")
log.setLevel("INFO")
log.propagate = False
fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
for h in [logging.StreamHandler(),
          RotatingFileHandler("bumper.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]:
    h.setFormatter(fmt)
    log.addHandler(h)

# -- CONFIG --------------------------------------------------------------------
TARGET_SITES = [s.strip() for s in os.environ.get("TARGET_SITES", "").split(",") if s.strip()]

CONFIG = {
    "super":      {"url": "https://www.super-parrain.com",
                   "email": os.environ.get("SUPER_PARRAIN_EMAIL", ""),
                   "password": os.environ.get("SUPER_PARRAIN_PASSWORD", "")},
    "code":       {"url": "https://code-parrainage.net",
                   "email": os.environ.get("CODE_PARRAINAGE_EMAIL", ""),
                   "password": os.environ.get("CODE_PARRAINAGE_PASSWORD", "")},
    "parrainage": {"url": "https://parrainage.co",
                   "email": os.environ.get("PARRAINAGE_CO_EMAIL", ""),
                   "password": os.environ.get("PARRAINAGE_CO_PASSWORD", ""),
                   "rm_cookie": os.environ.get("PARRAINAGE_CO_RM_COOKIE", "")},
}

# -- UTILITAIRES ---------------------------------------------------------------
async def human_sleep(a=2.0, b=6.0):
    await asyncio.sleep(random.uniform(a, b))

async def robust_fill(page, selector, value):
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=15000)
    await loc.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await loc.fill(value)
    await asyncio.sleep(random.uniform(0.2, 0.4))

async def human_click(page, locator):
    try:
        await locator.wait_for(state="visible", timeout=15000)
        box = await locator.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + random.randint(2, max(3, int(box["width"] - 2))),
                box["y"] + random.randint(2, max(3, int(box["height"] - 2))),
                steps=random.randint(15, 25))
        await human_sleep(0.2, 0.7)
        await locator.click()
    except Exception as e:
        log.debug(f"human_click ignore : {e}")

async def verify_login(page, fragment, name):
    url = page.url
    log.info(f"  [{name}] URL apres login : {url}")
    if fragment in url:
        log.error(f"  [{name}] Login echoue")
        return False
    log.info(f"  [{name}] Login reussi !")
    return True

# -- DRAG HUMAIN ---------------------------------------------------------------
async def human_drag(page, sx, sy, distance):
    overshoot = random.randint(5, 15)
    total = distance + overshoot
    await page.mouse.move(sx, sy)
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.15))
    steps = random.randint(40, 60)
    for i in range(steps):
        t = i / steps
        eased = 4*t*t*t if t < 0.5 else 1 - (-2*t+2)**3/2
        if 0.3 < t < 0.5 and random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.03, 0.08))
        await page.mouse.move(sx + total*eased, sy + random.uniform(-2, 2))
        await asyncio.sleep(random.uniform(0.006, 0.020))
    for i in range(random.randint(8, 15)):
        t = i / 12
        pos = (distance + overshoot) - overshoot * (t * (2 - t))
        await page.mouse.move(sx + pos, sy + random.uniform(-0.5, 0.5))
        await asyncio.sleep(random.uniform(0.010, 0.025))
    for _ in range(random.randint(2, 5)):
        await page.mouse.move(sx + distance + random.uniform(-1.5, 1.5), sy + random.uniform(-0.5, 0.5))
        await asyncio.sleep(random.uniform(0.015, 0.030))
    await page.mouse.move(sx + distance, sy)
    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.up()
    await asyncio.sleep(random.uniform(1.0, 2.0))

# -- SLIDER CAPTCHA ------------------------------------------------------------
def find_gap_position(bg_bytes, piece_bytes, bg_css_w=280, piece_css_w=63):
    try:
        from PIL import Image
        bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
        bg_w, bg_h = bg.size
        pc_w = Image.open(io.BytesIO(piece_bytes)).size[0]
        pixels = list(bg.getdata())
        white_per_col = {}
        for y in range(bg_h):
            for x in range(bg_w):
                r, g, b = pixels[y * bg_w + x]
                if r > 200 and g > 200 and b > 200:
                    white_per_col[x] = white_per_col.get(x, 0) + 1
        candidates = {x: c for x, c in white_per_col.items()
                      if pc_w + 10 < x < bg_w - 10 and c > 5}
        if not candidates:
            log.warning("  Pas de zone blanche, fallback 117px")
            return 117
        cols = sorted(candidates.keys())
        center = (cols[0] + cols[-1]) // 2
        log.info(f"  PIL white-pixel -> trou x=[{cols[0]},{cols[-1]}] centre={center}px")
        return center
    except Exception as e:
        log.warning(f"  PIL echoue ({e}), fallback 117px")
        return 117

async def solve_slider(page):
    present = False
    for sel in ['div[class*="captcha"]', 'div[class*="slider"]', 'div:has-text("Glissez")']:
        try:
            if await page.locator(sel).first.is_visible():
                present = True
                break
        except Exception:
            pass
    if not present:
        log.info("  Pas de slider CAPTCHA")
        return True

    log.info("  Slider CAPTCHA - resolution en cours...")
    await human_sleep(1, 2)

    # Debug dump
    try:
        html = await page.content()
        for kw in ["glissez", "slider", "captcha"]:
            idx = html.lower().find(kw)
            if idx != -1:
                log.info(f"  HTML[{kw}]: {html[max(0,idx-100):idx+300].replace(chr(10),' ')[:400]}")
                break
        els = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('canvas, div[class]'))
            .filter(e => e.offsetWidth > 0 && (
                e.className.toLowerCase().includes('capt') ||
                e.className.toLowerCase().includes('slid') ||
                e.tagName === 'CANVAS'))
            .map(e => ({tag:e.tagName, cls:e.className, w:e.offsetWidth, h:e.offsetHeight}))
        }""")
        for e in els:
            log.info(f"  {e['tag']} class='{e['cls']}' {e['w']}x{e['h']}")
    except Exception as e:
        log.debug(f"  dump echoue: {e}")

    bg_bytes = piece_bytes = None
    try:
        canvases = page.locator('.slidercaptcha canvas')
        n = await canvases.count()
        if n >= 1:
            bg_bytes = await canvases.nth(0).screenshot()
            log.info(f"  Fond capture ({n} canvas)")
        if n >= 2:
            piece_bytes = await canvases.nth(1).screenshot()
            log.info("  Piece capturee")
    except Exception as e:
        log.debug(f"  capture canvas: {e}")

    if bg_bytes:
        with open("debug_bg.png", "wb") as f: f.write(bg_bytes)
    if piece_bytes:
        with open("debug_piece.png", "wb") as f: f.write(piece_bytes)

    if bg_bytes and piece_bytes:
        target_x = find_gap_position(bg_bytes, piece_bytes)
    else:
        target_x = random.randint(120, 180)
        log.warning(f"  Capture impossible, distance aleatoire: {target_x}px")

    handle = page.locator('div.slider').first
    try:
        await handle.wait_for(state="visible", timeout=5000)
    except Exception:
        log.warning("  Handle introuvable")
        return False

    box = await handle.bounding_box()
    if not box:
        return False

    canvas_box = await page.locator('.slidercaptcha canvas').first.bounding_box()
    canvas_left = canvas_box["x"] if canvas_box else box["x"]
    sx = box["x"] + box["width"] / 2
    sy = box["y"] + box["height"] / 2
    handle_offset = sx - canvas_left
    real_dist = max(5, int(target_x - handle_offset))
    log.info(f"  canvas_left={canvas_left:.0f}, offset={handle_offset:.0f}px -> drag={real_dist}px")

    distances = [real_dist]
    for d in [-10, +10, -20, +20, -30, +30]:
        if real_dist + d > 0:
            distances.append(real_dist + d)

    for dist in distances:
        box = await handle.bounding_box()
        if not box:
            break
        sx = box["x"] + box["width"] / 2
        sy = box["y"] + box["height"] / 2
        log.info(f"  Drag: {dist}px")
        await human_drag(page, sx, sy, dist)
        try:
            val = await page.locator('#captcha_valide, input[name="captcha_valide"]').first.input_value()
            log.info(f"  captcha_valide={val}")
            if val in ("true", "1", "yes", "ok"):
                log.info("  Slider valide !")
                return True
        except Exception:
            pass
        try:
            if not await page.locator('.slidercaptcha').first.is_visible():
                log.info("  Slider resolu (widget disparu) !")
                return True
        except Exception:
            pass
        log.info(f"  Distance {dist}px insuffisante...")
        await human_sleep(1.5, 2.5)

    log.warning("  Slider non resolu, soumission quand meme...")
    return False

# -- RETRY ---------------------------------------------------------------------
async def retry(fn, retries=3, delay=10.0, label=""):
    for attempt in range(1, retries + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt == retries:
                log.error(f"[{label}] Echec definitif ({retries} tentatives): {e}")
                raise
            log.warning(f"[{label}] Tentative {attempt}/{retries}: {e} - retry dans {delay}s")
            await asyncio.sleep(delay)

# -- SUPER-PARRAIN.COM ---------------------------------------------------------
async def bump_super(page):
    cfg = CONFIG["super"]
    name = "super-parrain"
    log.info(f"\n{'-'*50}\n  super-parrain.com\n{'-'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)
        await robust_fill(page, 'input[name="_username"], input[type="email"]', cfg["email"])
        await robust_fill(page, 'input[name="_password"], input[type="password"]', cfg["password"])
        await human_sleep(1, 2)
        await human_click(page, page.locator('input[type="submit"], button:has-text("Connexion"), button[type="submit"]').first)
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)
        if not await verify_login(page, "/login", name):
            await page.screenshot(path="debug_super_login.png")
            raise RuntimeError("Login echoue")

        await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
        await human_sleep(3, 5)

        hrefs = await page.evaluate("""
            () => Array.from(document.querySelectorAll('td:last-child a, a[href*="offre"]'))
                .map(a => a.href)
                .filter(h => h.includes("codes-promo") && h.includes("edit"))
        """)
        edit_urls = list(dict.fromkeys(hrefs))
        log.info(f"  URLs modifier trouvees: {len(edit_urls)}")
        bumped = 0

        for i, url in enumerate(edit_urls):
            try:
                await page.goto(url, wait_until="networkidle")
                await human_sleep(2, 3)
                save_btn = page.locator('button:has-text("Enregistrer"), input[type="submit"], button[type="submit"]').first
                await human_click(page, save_btn)
                await page.wait_for_load_state("networkidle")
                await human_sleep(2, 4)
                bumped += 1
                log.info(f"  Code {i+1} remonte")
            except Exception as e:
                log.debug(f"  Erreur code {i}: {e}")

        log.info(f"  {bumped} code(s) remonte(s)")

        # Sauvegarder heure du run
        with open("last_super_run.txt", "w") as f:
            f.write(datetime.now().isoformat())

    await retry(_do, retries=3, label=name)

# -- CODE-PARRAINAGE.NET -------------------------------------------------------
async def bump_code(page):
    cfg = CONFIG["code"]
    name = "code-parrainage"
    log.info(f"\n{'-'*50}\n  code-parrainage.net\n{'-'*50}")

    async def _do():
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
        await human_sleep(2, 4)
        await robust_fill(page, 'input[type="email"]', cfg["email"])
        await robust_fill(page, 'input[type="password"]', cfg["password"])
        await human_sleep(1, 2)
        await solve_slider(page)
        await asyncio.sleep(random.uniform(0.8, 1.5))
        await human_click(page, page.locator('button:has-text("Se connecter"), button[type="submit"]').first)
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        await human_sleep(3, 5)
        if not await verify_login(page, "/login", name):
            raise RuntimeError("Login echoue")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle")
        await human_sleep(3, 5)
        buttons = page.locator('button:has-text("Actualiser"), a:has-text("Actualiser")')
        count = await buttons.count()
        log.info(f"  Boutons Actualiser: {count}")
        bumped = 0
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible(): continue
                await btn.scroll_into_view_if_needed()
                await human_click(page, btn)
                bumped += 1
                log.info(f"  Actualiser {i+1} clique")
                await human_sleep(2, 5)
            except Exception as e:
                log.debug(f"  Erreur {i}: {e}")
        log.info(f"  {bumped} annonce(s) remontee(s)")

    await retry(_do, retries=3, label=name)

# -- PARRAINAGE.CO -------------------------------------------------------------
async def bump_parrainage(page):
    cfg = CONFIG["parrainage"]
    name = "parrainage_co"
    log.info(f"\n{'-'*50}\n  parrainage.co\n{'-'*50}")

    async def _do():
        rm_cookie = cfg.get("rm_cookie", "")
        if not rm_cookie:
            raise RuntimeError("PARRAINAGE_CO_RM_COOKIE manquant")

        await page.context.add_cookies([{
            "name": "parrainageco_rm",
            "value": rm_cookie.strip(),
            "domain": "parrainage.co",
            "path": "/",
        }])
        log.info("  Cookie remember-me injecte")

        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
        await human_sleep(2, 4)
        await page.screenshot(path="debug_parrainage_login.png")

        if not await verify_login(page, "/login", name):
            raise RuntimeError("Cookie remember-me expire ou invalide")

        # Accepter le confirm() JS automatiquement
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        log.info("  Navigation vers /account/offers/boost-all")
        resp = await page.goto(f"{cfg['url']}/account/offers/boost-all",
                               wait_until="domcontentloaded", timeout=30000)
        log.info(f"  Reponse: {resp.status if resp else '?'} -> {page.url}")
        await human_sleep(2, 4)
        await page.screenshot(path="debug_parrainage_apres.png")
        log.info("  Boost-all effectue")

        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=30000)
        log.info("  Annonces remontees")

    await retry(_do, retries=3, label=name)

# -- MAIN ----------------------------------------------------------------------
HANDLERS = {"super": bump_super, "code": bump_code, "parrainage": bump_parrainage}

async def main():
    to_run = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

    # Verification 24h pour super-parrain (sans sleep, exit immediat)
    if to_run == ["super"]:
        last_file = "last_super_run.txt"
        if os.path.exists(last_file):
            try:
                with open(last_file) as f:
                    last = datetime.fromisoformat(f.read().strip())
                elapsed = (datetime.now() - last).total_seconds() / 3600
                log.info(f"  Dernier run: {last.isoformat()} ({elapsed:.1f}h ecoulees)")
                if elapsed < 24:
                    log.info("  Moins de 24h - run ignore, prochain run demain")
                    return
            except Exception:
                pass
        else:
            log.info("  Premier run super-parrain")

    log.info("=" * 55)
    log.info("  Parrainage Auto-Bumper")
    log.info(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"  Sites: {', '.join(to_run)}")
    log.info("=" * 55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR",
                  "--window-size=1280,800", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
            extra_http_headers={
                "Accept-Language": "fr-FR,fr;q=0.9",
                "sec-ch-ua": '"Google Chrome";v="123", "Not:A-Brand";v="8"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            })
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = {runtime: {}};
        """)

        for site_id in to_run:
            handler = HANDLERS.get(site_id)
            if not handler: continue
            cfg = CONFIG.get(site_id, {})
            if not cfg.get("email") and site_id != "parrainage":
                log.warning(f"  {site_id} - credentials manquants")
                continue
            if site_id == "parrainage" and not cfg.get("rm_cookie"):
                log.warning(f"  parrainage - PARRAINAGE_CO_RM_COOKIE manquant")
                continue
            page = await context.new_page()
            try:
                await handler(page)
            except Exception as e:
                log.error(f"  {site_id} - Erreur: {e}")
            finally:
                await page.close()
                await human_sleep(3, 7)

        await browser.close()

    log.info("\n" + "=" * 55)
    log.info("  Cycle termine !")
    log.info("=" * 55)

if __name__ == "__main__":
    asyncio.run(main())
