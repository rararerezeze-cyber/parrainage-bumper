# “””

# Parrainage Auto-Bumper  —  VERSION FINALE
super-parrain.com | code-parrainage.net | parrainage.co
Tourne 24h/24 sur GitHub Actions, zéro intervention

“””

import asyncio
import os
import io
import random
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# ══════════════════════════════════════════════════════════════════════════════

# LOGGING

# ══════════════════════════════════════════════════════════════════════════════

log = logging.getLogger(“bumper”)
log.setLevel(“INFO”)
log.propagate = False
formatter = logging.Formatter(”%(asctime)s  %(levelname)-8s  %(message)s”, datefmt=”%H:%M:%S”)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
file_handler = RotatingFileHandler(“bumper.log”, maxBytes=1_000_000, backupCount=2, encoding=“utf-8”)
file_handler.setFormatter(formatter)
if log.hasHandlers():
log.handlers.clear()
log.addHandler(console_handler)
log.addHandler(file_handler)

# ══════════════════════════════════════════════════════════════════════════════

# CONFIG

# ══════════════════════════════════════════════════════════════════════════════

TARGET_SITES = [s.strip() for s in os.environ.get(“TARGET_SITES”, “”).split(”,”) if s.strip()]

CONFIG = {
“sites”: {
“super”: {
“url”:      “https://www.super-parrain.com”,
“email”:    os.environ.get(“SUPER_PARRAIN_EMAIL”, “”),
“password”: os.environ.get(“SUPER_PARRAIN_PASSWORD”, “”),
},
“code”: {
“url”:      “https://code-parrainage.net”,
“email”:    os.environ.get(“CODE_PARRAINAGE_EMAIL”, “”),
“password”: os.environ.get(“CODE_PARRAINAGE_PASSWORD”, “”),
},
“parrainage”: {
“url”:      “https://parrainage.co”,
“email”:    os.environ.get(“PARRAINAGE_CO_EMAIL”, “”),
“password”: os.environ.get(“PARRAINAGE_CO_PASSWORD”, “”),
},
},
}

# ══════════════════════════════════════════════════════════════════════════════

# UTILITAIRES HUMAINS

# ══════════════════════════════════════════════════════════════════════════════

async def human_sleep(a: float = 2.0, b: float = 6.0):
await asyncio.sleep(random.uniform(a, b))

async def robust_fill(page: Page, selector: str, value: str):
locator = page.locator(selector).first
await locator.wait_for(state=“visible”, timeout=15000)
await locator.click()
await asyncio.sleep(random.uniform(0.2, 0.5))
await locator.fill(value)
await asyncio.sleep(random.uniform(0.2, 0.4))

async def human_click(page: Page, locator):
try:
await locator.wait_for(state=“visible”, timeout=15000)
box = await locator.bounding_box()
if box:
await page.mouse.move(
box[“x”] + random.randint(2, max(3, int(box[“width”] - 2))),
box[“y”] + random.randint(2, max(3, int(box[“height”] - 2))),
steps=random.randint(15, 25),
)
await human_sleep(0.2, 0.7)
await locator.click()
except Exception as e:
log.debug(f”human_click ignoré : {e}”)

async def verify_login(page: Page, fragment: str, name: str) -> bool:
current = page.url
log.info(f”  [{name}] URL après login : {current}”)
if fragment in current:
log.error(f”  [{name}] ❌ Login échoué”)
return False
log.info(f”  [{name}] ✓ Login réussi !”)
return True

# ══════════════════════════════════════════════════════════════════════════════

# DRAG HUMAIN AVEC OVERSHOOT

# Simule un vrai humain : accélère, dépasse légèrement, revient

# ══════════════════════════════════════════════════════════════════════════════

async def human_drag(page: Page, start_x: float, start_y: float, distance: int):
“””
Drag réaliste avec :
- Accélération au début
- Légère hésitation en cours de route (pause microscopique)
- Overshoot : dépasse de 5-15px puis revient doucement
- Jitter vertical aléatoire
- Micro-tremblement à la fin
“””
overshoot = random.randint(5, 15)
total_dist = distance + overshoot

```
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
```

# ══════════════════════════════════════════════════════════════════════════════

# SLIDER CAPTCHA — Résolution gratuite (PIL + drag humain)

# ══════════════════════════════════════════════════════════════════════════════

def find_gap_position(bg_bytes: bytes, piece_bytes: bytes,
bg_css_w: int = 280, piece_css_w: int = 63) -> int:
“””
Trouve le centre du trou blanc dans le fond du slider.
Le trou = zone de pixels blancs (R>200, G>200, B>200).
Retourne la position CSS du centre du trou (pour le drag).
“””
try:
from PIL import Image

```
    bg    = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    piece = Image.open(io.BytesIO(piece_bytes)).convert("RGB")

    bg_w, bg_h = bg.size
    pc_w, pc_h = piece.size

    pixels = list(bg.getdata())

    # Compter les pixels blancs par colonne (R>200 ET G>200 ET B>200)
    white_per_col = {}
    for y in range(bg_h):
        for x in range(bg_w):
            r, g, b = pixels[y * bg_w + x]
            if r > 200 and g > 200 and b > 200:
                white_per_col[x] = white_per_col.get(x, 0) + 1

    # Ignorer les bords et la zone de la pièce initiale (x < pc_w + 10)
    # et le bord droit (x > bg_w - 10)
    candidates = {
        x: cnt for x, cnt in white_per_col.items()
        if pc_w + 10 < x < bg_w - 10 and cnt > 5
    }

    if not candidates:
        log.warning("  Pas de zone blanche trouvée, fallback 117px")
        return 117

    # Trouver le cluster principal : plage continue de colonnes blanches
    sorted_cols = sorted(candidates.keys())
    x_min = sorted_cols[0]
    x_max = sorted_cols[-1]
    center = (x_min + x_max) // 2

    log.info(f"  🔍 PIL white-pixel → trou x=[{x_min},{x_max}] centre={center}px CSS")
    return center

except Exception as e:
    log.warning(f"  PIL échoué ({e}), fallback 117px")
    return 117
```

async def solve_slider(page: Page) -> bool:
“”“Résout le slider CAPTCHA si présent. Retourne True si OK.”””

```
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

# ── DEBUG : dump le HTML complet du captcha pour trouver les sélecteurs ──
try:
    full_html = await page.content()
    # Trouver la zone autour de "Glissez" ou "slider" ou "captcha"
    for keyword in ["glissez", "slider", "captcha", "drag", "verify", "puzzle"]:
        idx = full_html.lower().find(keyword)
        if idx != -1:
            snippet = full_html[max(0, idx-200):idx+500].replace("\n", " ").strip()
            log.info(f"  🔎 HTML autour de '{keyword}' : {snippet[:600]}")
            break
    # Lister aussi tous les éléments avec leurs classes
    elements = await page.evaluate("""() => {
        const els = document.querySelectorAll('img, canvas, div[class], button[class]');
        return Array.from(els).slice(0, 60).map(el => ({
            tag: el.tagName,
            cls: el.className,
            id: el.id,
            w: el.offsetWidth,
            h: el.offsetHeight,
            visible: el.offsetWidth > 0 && el.offsetHeight > 0
        })).filter(e => e.visible && (
            e.cls.toLowerCase().includes('capt') ||
            e.cls.toLowerCase().includes('slid') ||
            e.cls.toLowerCase().includes('drag') ||
            e.cls.toLowerCase().includes('verif') ||
            e.cls.toLowerCase().includes('puzzl') ||
            e.tag === 'CANVAS' ||
            (e.tag === 'IMG' && e.w > 50)
        ));
    }""")
    for el in elements:
        log.info(f"  🔎 {el['tag']} | class='{el['cls']}' | id='{el['id']}' | {el['w']}x{el['h']}")
except Exception as e:
    log.debug(f"  HTML dump échoué : {e}")
# ── FIN DEBUG ──

# ── Sélecteurs exacts code-parrainage.net ──
bg_bytes, piece_bytes = None, None

try:
    canvases = page.locator('.slidercaptcha canvas')
    n = await canvases.count()
    if n >= 1:
        bg_bytes = await canvases.nth(0).screenshot()
        log.info(f"  Fond capturé ({n} canvas trouvés)")
    if n >= 2:
        piece_bytes = await canvases.nth(1).screenshot()
        log.info("  Pièce capturée")
except Exception as e:
    log.debug(f"  Capture canvas : {e}")

# Sauvegarder les images pour debug
if bg_bytes:
    import base64
    log.info(f"  BG_B64:{base64.b64encode(bg_bytes).decode()[:200]}")
    with open("debug_bg.png", "wb") as f: f.write(bg_bytes)
if piece_bytes:
    import base64
    log.info(f"  PIECE_B64:{base64.b64encode(piece_bytes).decode()[:200]}")
    with open("debug_piece.png", "wb") as f: f.write(piece_bytes)

# Calcul de la distance cible (canvas CSS = 280px, pièce CSS = 63px)
if bg_bytes and piece_bytes:
    target_x = find_gap_position(bg_bytes, piece_bytes, bg_css_w=280, piece_css_w=63)
else:
    target_x = random.randint(120, 180)
    log.warning(f"  Capture impossible, distance aléatoire : {target_x}px")

# Handle : div.slider (40x40)
handle = page.locator('div.slider').first
try:
    await handle.wait_for(state="visible", timeout=5000)
except Exception:
    log.warning("  Handle introuvable")
    return False

box = await handle.bounding_box()
if not box:
    return False

sx = box["x"] + box["width"] / 2
sy = box["y"] + box["height"] / 2

# Récupérer la position du canvas pour calculer l'offset correct
canvas_box = await page.locator('.slidercaptcha canvas').first.bounding_box()
canvas_left = canvas_box["x"] if canvas_box else (sx - box["width"] / 2)

# Distance réelle = position du trou dans le canvas - offset du handle dans le container
# Le handle (40px) part du bord gauche du container, son centre est à +20px
handle_offset = sx - canvas_left  # offset du centre du handle depuis le bord gauche du canvas
real_dist = max(5, int(target_x - handle_offset))
log.info(f"  canvas_left={canvas_left:.0f}, handle_center={sx:.0f}, offset={handle_offset:.0f}px → drag={real_dist}px")

# Essais autour de la distance calculée
distances_to_try = [real_dist]
for delta in [-10, +10, -20, +20, -30, +30]:
    alt = real_dist + delta
    if alt > 0:
        distances_to_try.append(alt)

for dist in distances_to_try:
    # Re-fetch la position du handle à chaque essai (reset après chaque drag raté)
    box = await handle.bounding_box()
    if not box:
        log.warning("  Handle disparu")
        break
    sx = box["x"] + box["width"] / 2
    sy = box["y"] + box["height"] / 2

    log.info(f"  🖱️  Drag humain : {dist}px (handle à x={sx:.0f})")
    await human_drag(page, sx, sy, dist)

    # Vérifier via le champ caché captcha_valide (true=OK, false=raté)
    try:
        val = await page.locator('#captcha_valide, input[name="captcha_valide"]').first.input_value()
        log.info(f"  captcha_valide={val}")
        if val in ("true", "1", "yes", "ok"):
            log.info(f"  ✅ Slider validé !")
            return True
    except Exception:
        pass

    # Vérifier aussi si le widget a disparu
    try:
        still_there = await page.locator('.slidercaptcha').first.is_visible()
        if not still_there:
            log.info(f"  ✅ Slider résolu (widget disparu) !")
            return True
    except Exception:
        pass

    log.info(f"  Distance {dist}px insuffisante, prochain essai...")
    await human_sleep(1.5, 2.5)

log.warning("  ⚠️ Slider non résolu, soumission quand même...")
return False
```

# ══════════════════════════════════════════════════════════════════════════════

# RETRY

# ══════════════════════════════════════════════════════════════════════════════

async def retry(fn, retries: int = 3, delay: float = 10.0, label: str = “”):
for attempt in range(1, retries + 1):
try:
return await fn()
except Exception as e:
if attempt == retries:
log.error(f”❌ [{label}] Échec définitif ({retries} tentatives) : {e}”)
raise
log.warning(f”⚠️  [{label}] Tentative {attempt}/{retries} : {e} — retry dans {delay}s”)
await asyncio.sleep(delay)

# ══════════════════════════════════════════════════════════════════════════════

# HANDLER 1 — SUPER-PARRAIN.COM  (1x/jour)

# ══════════════════════════════════════════════════════════════════════════════

async def bump_super(page: Page):
cfg  = CONFIG[“sites”][“super”]
name = “super-parrain”
log.info(f”\n{‘─’*50}\n  🌐 super-parrain.com\n{‘─’*50}”)

```
async def _do():
    await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
    await human_sleep(2, 4)
    await robust_fill(page, 'input[name="_username"], input[type="email"]', cfg["email"])
    await robust_fill(page, 'input[name="_password"], input[type="password"]', cfg["password"])
    await human_sleep(1, 2)

    await human_click(page, page.locator(
        'input[type="submit"], button:has-text("Connexion"), button[type="submit"]'
    ).first)

    try:
        await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        pass
    await page.wait_for_load_state("networkidle")
    await human_sleep(3, 5)

    if not await verify_login(page, "/login", name):
        await page.screenshot(path="debug_super_login.png")
        btns = await page.evaluate("Array.from(document.querySelectorAll('button, input[type=submit]')).map(b => (b.textContent || b.value).trim())")
        for b in btns: log.info(f"  Bouton: {b}")
        inputs = await page.evaluate("Array.from(document.querySelectorAll('input')).map(i => i.type + ':' + i.name)")
        for i in inputs: log.info(f"  Input: {i}")
        raise RuntimeError("Login échoué")

    await page.goto(f"{cfg['url']}/tableau-de-bord/codes-promo", wait_until="networkidle")
    await human_sleep(3, 5)

    # Récupérer les URLs des boutons modifier (crayon rouge)
    await page.wait_for_load_state("networkidle")
    hrefs = await page.evaluate("""
        () => Array.from(document.querySelectorAll(
            'td:last-child a, a[href*="offre"], a[href*="codes-promo/"] '
        )).map(a => a.href).filter(h => h.includes("codes-promo"))
    """)
    # Dédoublonner et filtrer les URLs valides
    edit_urls = list(dict.fromkeys([h for h in hrefs if "/codes-promo/" in h and "slug" not in h or "offre" in h]))
    log.info(f"  URLs modifier trouvées : {len(edit_urls)}")
    for u in edit_urls[:3]: log.info(f"  → {u}")
    bumped = 0

    for i, url in enumerate(edit_urls):
        try:
            await page.goto(url, wait_until="networkidle")
            await human_sleep(2, 3)
            save_btn = page.locator(
                'button:has-text("Enregistrer"), input[type="submit"], button[type="submit"]'
            ).first
            await human_click(page, save_btn)
            await page.wait_for_load_state("networkidle")
            await human_sleep(2, 4)
            bumped += 1
            log.info(f"  🔼 Code {i+1} remonté")
        except Exception as e:
            log.debug(f"  Erreur code {i} : {e}")

    log.info(f"  🎯 {bumped} code(s) remonté(s) ✓")

await retry(_do, retries=3, label=name)
```

# ══════════════════════════════════════════════════════════════════════════════

# HANDLER 2 — CODE-PARRAINAGE.NET  (5x/jour)

# ══════════════════════════════════════════════════════════════════════════════

async def bump_code(page: Page):
cfg  = CONFIG[“sites”][“code”]
name = “code-parrainage”
log.info(f”\n{‘─’*50}\n  🌐 code-parrainage.net\n{‘─’*50}”)

```
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
```

# ══════════════════════════════════════════════════════════════════════════════

# HANDLER 3 — PARRAINAGE.CO  (5x/jour) ✅

# ══════════════════════════════════════════════════════════════════════════════

async def bump_parrainage(page: Page):
cfg  = CONFIG[“sites”][“parrainage”]
name = “parrainage_co”
log.info(f”\n{‘─’*50}\n  🌐 parrainage.co\n{‘─’*50}”)

```
async def _do():
    await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded")
    await human_sleep(3, 5)
    await robust_fill(page, 'input[type="email"], input[name="email"], input#email',          cfg["email"])
    await human_sleep(0.5, 1)
    await robust_fill(page, 'input[type="password"], input[name="password"], input#password', cfg["password"])
    await human_sleep(1, 2)

    await human_click(page, page.locator(
        'input[type="submit"], input[name="loginSubmit"], '
        'button:has-text("Connexion"), button[type="submit"]'
    ).first)

    try:
        await page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        pass
    await page.wait_for_load_state("networkidle")
    await human_sleep(3, 5)

    if not await verify_login(page, "/login", name):
        await page.screenshot(path="debug_parrainage_login.png")
        btns = await page.evaluate("Array.from(document.querySelectorAll('button, input[type=submit]')).map(b => b.textContent.trim())")
        for b in btns:
            log.info(f"  Bouton: {b}")
        inputs = await page.evaluate("Array.from(document.querySelectorAll('input')).map(i => i.type + ':' + i.name + '=' + i.value.slice(0,3))")
        for i in inputs:
            log.info(f"  Input: {i}")
        raise RuntimeError("Login échoué")

    await page.goto(f"{cfg['url']}/account/offers", wait_until="networkidle")
    await human_sleep(3, 5)

    bumped = 0
    max_attempts = 50  # sécurité anti-boucle infinie

    for attempt in range(max_attempts):
        # Recharger les boutons à chaque itération (page mise à jour après chaque clic)
        buttons = page.locator(
            'button:has-text("Remettre en haut"), a:has-text("Remettre en haut")'
        )
        count = await buttons.count()
        if attempt == 0:
            log.info(f"  Boutons Remettre en haut : {count}")

        if count == 0:
            break

        # Toujours cliquer le PREMIER bouton visible (les index se réorganisent)
        btn = buttons.first
        try:
            if not await btn.is_visible():
                break
            await btn.scroll_into_view_if_needed()
            await human_click(page, btn)
            bumped += 1
            log.info(f"  🔼 Remettre en haut {bumped} cliqué")
            await human_sleep(2, 5)
            # Recharger la page pour voir les boutons mis à jour
            await page.goto(f"{cfg['url']}/account/offers", wait_until="networkidle")
            await human_sleep(2, 3)
        except Exception as e:
            log.debug(f"  Erreur tentative {attempt} : {e}")
            break

    log.info(f"  🎯 {bumped} annonce(s) remontée(s) ✓")

await retry(_do, retries=3, label=name)
```

# ══════════════════════════════════════════════════════════════════════════════

# MAIN

# ══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
“super”:      bump_super,
“code”:       bump_code,
“parrainage”: bump_parrainage,
}

async def main():
to_run = TARGET_SITES if TARGET_SITES else list(HANDLERS.keys())

```
# ── Délai aléatoire anti-détection ──────────────────────────
# Super-parrain : délai 0-3h (1x/jour, heure très variable)
# Autres sites  : délai 0-60min (5x/jour, heure variable)
# ── Délai aléatoire ──────────────────────────────────────────
if to_run == ["super"]:
    # Super-parrain : vérifier que 24h se sont écoulées depuis le dernier run
    last_run_file = "last_super_run.txt"
    min_interval_h = 24
    now = datetime.now()

    if os.path.exists(last_run_file):
        with open(last_run_file, "r") as f:
            last_str = f.read().strip()
        try:
            last_run = datetime.fromisoformat(last_str)
            elapsed_h = (now - last_run).total_seconds() / 3600
            log.info(f"  ⏱️  Dernier run : {last_str} ({elapsed_h:.1f}h écoulées)")
            if elapsed_h < min_interval_h:
                wait_h = min_interval_h - elapsed_h + random.uniform(0.5, 2)
                log.info(f"  ⏳  Moins de 24h écoulées — attente {wait_h:.1f}h supplémentaires")
                await asyncio.sleep(wait_h * 3600)
            else:
                # 24h+ écoulées : délai aléatoire court (0-30min) juste pour varier
                delay_min = random.randint(0, 30)
                log.info(f"  ⏳  Délai aléatoire : {delay_min} min")
                await asyncio.sleep(delay_min * 60)
        except Exception:
            pass
    else:
        log.info("  ℹ️  Premier run super-parrain")

else:
    # Autres sites : délai aléatoire 0-60min
    delay_min = random.randint(0, 60)
    log.info(f"  ⏳  Délai aléatoire : {delay_min} min avant démarrage...")
    await asyncio.sleep(delay_min * 60)

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

# Sauvegarder l'heure du run pour super-parrain
if to_run == ["super"]:
    with open("last_super_run.txt", "w") as f:
        f.write(datetime.now().isoformat())
    log.info(f"  💾  Heure sauvegardée : {datetime.now().strftime('%H:%M:%S')}")

log.info("\n" + "═" * 55)
log.info("  ✅  Cycle terminé !")
log.info("═" * 55)
```

if **name** == “**main**”:
asyncio.run(main())
