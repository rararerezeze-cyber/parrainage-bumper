"""
Parrainage Auto-Bumper - VERSION PRO
super-parrain.com | code-parrainage.net | parrainage.co
"""

import asyncio, os, io, json, random, logging, httpx
from datetime import datetime
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# -- LOGGING ------------------------------------------------------------------
log = logging.getLogger("bumper")
log.setLevel("INFO")
log.propagate = False
fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
for h in [logging.StreamHandler(),
          RotatingFileHandler("bumper.log", maxBytes=500_000, backupCount=1, encoding="utf-8")]:
    h.setFormatter(fmt)
    log.addHandler(h)

# -- CONFIG -------------------------------------------------------------------
TARGET_SITES = [s.strip() for s in os.environ.get("TARGET_SITES", "").split(",") if s.strip()]

CONFIG = {
    "super":      {"url": "https://www.super-parrain.com",
                   "email":    os.environ.get("SUPER_PARRAIN_EMAIL", ""),
                   "password": os.environ.get("SUPER_PARRAIN_PASSWORD", "")},
    "code":       {"url": "https://code-parrainage.net",
                   "email":    os.environ.get("CODE_PARRAINAGE_EMAIL", ""),
                   "password": os.environ.get("CODE_PARRAINAGE_PASSWORD", "")},
    "parrainage": {"url": "https://parrainage.co",
                   "email":    os.environ.get("PARRAINAGE_CO_EMAIL", ""),
                   "password": os.environ.get("PARRAINAGE_CO_PASSWORD", ""),
                   "rm_cookie": os.environ.get("PARRAINAGE_CO_RM_COOKIE", "")},
    "referralcode": {"url": "https://www.referralcode.tv",
                     "email":    os.environ.get("REFERRALCODE_EMAIL", ""),
                     "password": os.environ.get("REFERRALCODE_PASSWORD", "")},
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1280, "height": 800},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
]

# -- UTILITAIRES --------------------------------------------------------------
async def human_sleep(a=2.0, b=6.0):
    await asyncio.sleep(random.uniform(a, b))

async def robust_fill(page, selector, value):
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=15000)
    await loc.scroll_into_view_if_needed()
    await loc.click()
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await loc.fill(value)
    await asyncio.sleep(random.uniform(0.2, 0.4))

async def human_click(page, locator):
    try:
        await locator.wait_for(state="visible", timeout=15000)
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + random.randint(2, max(3, int(box["width"] - 2))),
                box["y"] + random.randint(2, max(3, int(box["height"] - 2))),
                steps=random.randint(15, 30))
        await human_sleep(0.2, 0.7)
        await locator.click()
    except Exception as e:
        log.warning(f"human_click echoue: {e}")
        raise

async def smart_fill(page, selectors, value, timeout=8000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.scroll_into_view_if_needed()
            await loc.click()
            await asyncio.sleep(random.uniform(0.3, 0.5))
            await loc.fill(value)
            log.info(f"  smart_fill OK: {sel}")
            return True
        except Exception:
            continue
    for frame in page.frames:
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                await loc.wait_for(state="visible", timeout=3000)
                await loc.click()
                await asyncio.sleep(0.3)
                await loc.fill(value)
                log.info(f"  smart_fill iframe OK: {sel}")
                return True
            except Exception:
                continue
    log.warning(f"  smart_fill: aucun selector trouve")
    return False

async def verify_login(page, fragment, name):
    url = page.url
    if fragment in url:
        log.error(f"  [{name}] Login echoue - URL: {url}")
        return False
    log.info(f"  [{name}] Login OK - {url}")
    return True

async def new_context(browser):
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)
    ctx = await browser.new_context(
        user_agent=ua,
        viewport=vp,
        locale="fr-FR",
        timezone_id="Europe/Paris",
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        extra_http_headers={
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })
    return ctx

# -- CLOUDFLARE ---------------------------------------------------------------
async def wait_cloudflare(page, timeout=30000):
    """Attend que Cloudflare laisse passer et qu'un element interactif soit present."""
    log.info("  Attente Cloudflare...")
    start = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start) * 1000 < timeout:
        try:
            content = await page.content()
            if "Just a moment" in content or "Checking your browser" in content:
                await asyncio.sleep(2)
                continue
            # Verifier qu'un input ou body utile est present
            for sel in ['input[type="email"]', 'input[name="email"]',
                        'input[type="password"]', 'form', 'body']:
                try:
                    await page.wait_for_selector(sel, timeout=2000, state="attached")
                    log.info("  Cloudflare passed")
                    return True
                except Exception:
                    continue
            await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(1)
    log.warning("  Cloudflare timeout - on continue quand meme")
    return False

# -- DRAG HUMAIN --------------------------------------------------------------
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
    for _ in range(random.randint(2, 4)):
        await page.mouse.move(sx + distance + random.uniform(-1.5, 1.5), sy)
        await asyncio.sleep(random.uniform(0.015, 0.030))
    await page.mouse.move(sx + distance, sy)
    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.up()
    await asyncio.sleep(random.uniform(1.0, 2.0))

# -- SLIDER CAPTCHA -----------------------------------------------------------
def find_gap_position(bg_bytes, piece_bytes):
    try:
        from PIL import Image
        bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
        bg_w, bg_h = bg.size
        pc_w = Image.open(io.BytesIO(piece_bytes)).size[0]
        pixels = list(bg.get_flattened_data())
        white_per_col = {}
        for y in range(bg_h):
            for x in range(bg_w):
                r, g, b = pixels[y * bg_w + x]
                # Le masque du puzzle est blanc/neutre. Exclure les zones
                # naturellement claires mais colorees (ciel, plafond, etc.).
                if r > 200 and g > 200 and b > 200 and max(r, g, b) - min(r, g, b) < 10:
                    white_per_col[x] = white_per_col.get(x, 0) + 1
        starts = range(pc_w + 10, bg_w - pc_w - 9)
        scored = [(sum(white_per_col.get(x, 0)
                       for x in range(start, start + pc_w)), start)
                  for start in starts]
        if not scored:
            return 117
        score, start = max(scored)
        if score < 50:
            return 117
        center = start + pc_w // 2
        log.info(f"  PIL gap x=[{start},{start + pc_w - 1}] centre={center}px score={score}")
        return center
    except Exception as e:
        log.warning(f"  PIL echoue: {e}")
        return 117


def _write_slider_diag(diag: dict) -> None:
    try:
        from pathlib import Path

        path = Path(__file__).resolve().parent / "data" / "captures" / "code-parrainage-slider-diag.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


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
        _write_slider_diag({"present": False, "ok": True, "note": "no slider widget"})
        return True

    log.info("  Slider CAPTCHA en cours...")
    diag = {
        "present": True,
        "canvas_count": None,
        "target_x": None,
        "drag_px": None,
        "captcha_valide": None,
        "widget_gone": None,
        "ok": False,
        "note": "existing solver only — no bypass, no extra attempts on same puzzle",
    }
    await human_sleep(1, 2)

    bg_bytes = piece_bytes = None
    try:
        canvases = page.locator('.slidercaptcha canvas')
        n = await canvases.count()
        if n >= 1: bg_bytes = await canvases.nth(0).screenshot()
        if n >= 2: piece_bytes = await canvases.nth(1).screenshot()
        log.info(f"  {n} canvas captures")
        diag["canvas_count"] = n
    except Exception as e:
        log.debug(f"  canvas: {e}")
        diag["canvas_error"] = str(e)[:200]

    if bg_bytes: open("debug_bg.png", "wb").write(bg_bytes)
    if piece_bytes: open("debug_piece.png", "wb").write(piece_bytes)

    target_x = find_gap_position(bg_bytes, piece_bytes) if (bg_bytes and piece_bytes) else random.randint(120, 180)

    handle = page.locator('div.slider').first
    try:
        await handle.wait_for(state="visible", timeout=5000)
    except Exception:
        log.warning("  Handle introuvable")
        diag["error"] = "handle_not_found"
        _write_slider_diag(diag)
        return False

    box = await handle.bounding_box()
    if not box:
        diag["error"] = "handle_box_missing"
        _write_slider_diag(diag)
        return False

    canvas_box = await page.locator('.slidercaptcha canvas').first.bounding_box()
    canvas_left = canvas_box["x"] if canvas_box else box["x"]
    sx = box["x"] + box["width"] / 2
    sy = box["y"] + box["height"] / 2
    real_dist = max(5, int(target_x - (sx - canvas_left)))
    log.info(f"  drag cible={real_dist}px")
    diag["target_x"] = target_x
    diag["drag_px"] = real_dist

    # Une glissade refusee invalide le puzzle. Les decalages supplementaires
    # sur la meme image ne peuvent plus reussir; le retry recharge un defi neuf.
    distances = [real_dist]
    for dist in distances:
        box = await handle.bounding_box()
        if not box: break
        sx = box["x"] + box["width"] / 2
        sy = box["y"] + box["height"] / 2
        await human_drag(page, sx, sy, dist)
        try:
            val = await page.locator('#captcha_valide, input[name="captcha_valide"]').first.input_value()
            diag["captcha_valide"] = val
            if val in ("true", "1", "yes", "ok"):
                log.info("  Slider OK !")
                diag["ok"] = True
                _write_slider_diag(diag)
                return True
        except Exception:
            pass
        try:
            if not await page.locator('.slidercaptcha').first.is_visible():
                log.info("  Slider OK (widget disparu) !")
                diag["widget_gone"] = True
                diag["ok"] = True
                _write_slider_diag(diag)
                return True
            diag["widget_gone"] = False
        except Exception:
            pass
        await human_sleep(1.5, 2.5)

    log.warning("  Slider non resolu")
    _write_slider_diag(diag)
    return False

# -- RETRY --------------------------------------------------------------------
async def retry(fn, retries=3, delay=10.0, label=""):
    for attempt in range(1, retries + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt == retries:
                log.error(f"[{label}] Echec ({retries} tentatives): {e}")
                raise
            log.warning(f"[{label}] Tentative {attempt}/{retries}: {e} - retry {delay}s")
            await asyncio.sleep(delay)

# -- SUPER-PARRAIN.COM --------------------------------------------------------
async def run_super(browser):
    cfg = CONFIG["super"]
    name = "super-parrain"
    log.info(f"\n--- super-parrain.com ---")
    ctx = await new_context(browser)

    async def _do():
        page = await ctx.new_page()
        try:
            await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
            await human_sleep(2, 4)
            await robust_fill(page, 'input[name="_username"], input[type="email"]', cfg["email"])
            await robust_fill(page, 'input[name="_password"], input[type="password"]', cfg["password"])
            await human_sleep(1, 2)
            await human_click(page, page.locator(
                'input[type="submit"], button:has-text("Connexion"), button[type="submit"]').first)
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
                () => Array.from(document.querySelectorAll('td:last-child a, a[href*="edit"]'))
                    .map(a => a.href).filter(h => h.includes("codes-promo") && h.includes("edit"))
            """)
            edit_urls = list(dict.fromkeys(hrefs))
            log.info(f"  {len(edit_urls)} codes a remonter")
            if not edit_urls:
                raise RuntimeError("Aucun code trouve: page ou selecteurs probablement modifies")

            # Autofresh: enrichit la sauvegarde historique (update+remonte = 1 clic)
            # Desactiveable: AUTOFRESH_SUPER=0
            # Canary (defaut): AUTOFRESH_MODE=canary + AUTOFRESH_CANARY_PROGRAMS=kraken
            #   → seul Kraken recoit un prefill; les autres = BUMP_ONLY
            from lib.super_parrain_policy import autofresh_enabled, policy_snapshot

            autofresh_on = autofresh_enabled()
            policy = policy_snapshot()
            autofresh_stats = {
                "updated": 0,
                "bump_only": 0,
                "failed_prefill": 0,
                "canary_skipped": 0,
                "details": [],
                "policy": policy,
                "canary_post_verify": [],
            }
            canary_content_failed = False

            bumped = 0
            for i, url in enumerate(edit_urls):
                try:
                    await page.goto(url, wait_until="networkidle")
                    await human_sleep(2, 3)

                    info = None
                    # --- Autofresh PRE-CHECK + prefill (optionnel, canary-gated) ---
                    if autofresh_on and not canary_content_failed:
                        try:
                            from platforms.super_parrain.prefill import prepare_before_save
                            info = await prepare_before_save(page, url)
                            autofresh_stats["details"].append(info)
                            if info.get("fields_filled"):
                                autofresh_stats["updated"] += 1
                            else:
                                autofresh_stats["bump_only"] += 1
                                if info.get("reason") == "bump_only_not_canary":
                                    autofresh_stats["canary_skipped"] += 1
                        except Exception as e:
                            autofresh_stats["failed_prefill"] += 1
                            log.warning(f"  Autofresh prefill ignore ({i+1}): {e}")
                    elif autofresh_on and canary_content_failed:
                        autofresh_stats["bump_only"] += 1
                        autofresh_stats["details"].append(
                            {
                                "edit_url": url,
                                "skipped": True,
                                "reason": "autofresh_stopped_after_canary_fail",
                            }
                        )

                    # --- UN SEUL Enregistrer = update eventuel + remontee ---
                    # One bounded, same-page retry on a transient click
                    # timeout before giving up on this listing. Regression
                    # observed 2026-08-18: ~38/39 listings timed out waiting
                    # for this exact button in one run while one succeeded,
                    # consistent with transient slow rendering rather than a
                    # broken selector (the button IS present -- it just
                    # doesn't always appear within human_click's 15s window
                    # under rapid sequential navigation). No page reload here
                    # -- reloading would drop any Autofresh-prefilled content
                    # (canary program) sitting unsaved in the form; only the
                    # click itself is retried, on the same page state.
                    try:
                        await human_click(page, page.locator(
                            'button:has-text("Enregistrer"), input[type="submit"], button[type="submit"]').first)
                    except Exception:
                        log.warning(f"  Enregistrer echoue ({i+1}), retry unique (meme page)")
                        await human_sleep(3, 5)
                        await human_click(page, page.locator(
                            'button:has-text("Enregistrer"), input[type="submit"], button[type="submit"]').first)
                    await page.wait_for_load_state("networkidle")
                    await human_sleep(2, 4)
                    bumped += 1
                    filled = (info or {}).get("fields_filled") or []
                    tag = " (contenu+remonte)" if filled else " (remonte)"
                    log.info(f"  Code {i+1}/{len(edit_urls)} enregistre{tag}")

                    # --- Post-verify canary: re-fetch public apres save avec contenu ---
                    if filled and info and info.get("program"):
                        try:
                            await asyncio.sleep(2)
                            from lib.super_parrain_post_verify import verify_public_program

                            pv = verify_public_program(
                                info["program"],
                                filled_fields=list(filled),
                            )
                            autofresh_stats["canary_post_verify"].append(pv.to_dict())
                            if pv.post_match:
                                log.info(
                                    f"  Canary post-verify [{info['program']}]: "
                                    f"post_match=true exact={pv.exact_body_match}"
                                )
                            else:
                                log.error(
                                    f"  Canary post-verify [{info['program']}]: "
                                    f"post_match=FALSE — STOP Autofresh content "
                                    f"({pv.error})"
                                )
                                canary_content_failed = True
                                os.environ["AUTOFRESH_STOP"] = "1"
                        except Exception as e:
                            log.error(f"  Canary post-verify echec: {e}")
                            autofresh_stats["canary_post_verify"].append(
                                {
                                    "program": info.get("program"),
                                    "ok": False,
                                    "post_match": False,
                                    "error": str(e),
                                }
                            )
                            canary_content_failed = True
                            os.environ["AUTOFRESH_STOP"] = "1"
                except Exception as e:
                    log.debug(f"  Erreur code {i}: {e}")
                    # Stop on hard anti-bot signals
                    msg = str(e).lower()
                    if any(x in msg for x in ("403", "429", "captcha", "challenge", "blocked")):
                        log.error(f"  Stop Super-Parrain: signal anti-bot/DOM — {e}")
                        break

            log.info(f"  {bumped}/{len(edit_urls)} code(s) enregistres (1 save/code)")
            if autofresh_on:
                log.info(
                    f"  Autofresh: updated={autofresh_stats['updated']} "
                    f"bump_only={autofresh_stats['bump_only']} "
                    f"canary_skipped={autofresh_stats['canary_skipped']} "
                    f"prefill_fail={autofresh_stats['failed_prefill']} "
                    f"policy={policy}"
                )
                try:
                    import json
                    from pathlib import Path
                    out = Path("data/captures/super-parrain-last-cycle.json")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    # Canary verdict
                    canary_verdict = None
                    for pv in autofresh_stats.get("canary_post_verify") or []:
                        canary_verdict = {
                            "program": pv.get("program"),
                            "post_match": pv.get("post_match"),
                            "exact_body_match": pv.get("exact_body_match"),
                            "ok": pv.get("ok"),
                            "error": pv.get("error"),
                        }
                    out.write_text(
                        json.dumps(
                            {
                                "mode": "fused_bumper_canary"
                                if policy.get("mode") == "canary"
                                else "fused_bumper",
                                "total_edit_urls": len(edit_urls),
                                "saves": bumped,
                                "autofresh": autofresh_stats,
                                "canary_verdict": canary_verdict,
                                "canary_content_failed": canary_content_failed,
                                "max_saves_target": len(edit_urls),
                                "write_status": (
                                    "WRITE_VERIFIED"
                                    if canary_verdict and canary_verdict.get("post_match")
                                    else (
                                        "CANARY_FAILED"
                                        if canary_content_failed
                                        else (
                                            "CANARY_PENDING"
                                            if policy.get("mode") == "canary"
                                            else "FUSED_OK"
                                        )
                                    )
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            if bumped != len(edit_urls):
                raise RuntimeError(f"Remontee incomplete: {bumped}/{len(edit_urls)}")
            with open("last_super_run.txt", "w") as f:
                f.write(datetime.now().isoformat())
        finally:
            await page.close()

    try:
        await retry(_do, retries=3, label=name)
    finally:
        await ctx.close()

# -- CODE-PARRAINAGE.NET ------------------------------------------------------
async def run_code(browser):
    cfg = CONFIG["code"]
    name = "code-parrainage"
    log.info(f"\n--- code-parrainage.net ---")
    ctx = await new_context(browser)

    async def _do():
        page = await ctx.new_page()
        try:
            await page.goto(f"{cfg['url']}/login", wait_until="networkidle")
            await human_sleep(2, 4)
            await robust_fill(page, 'input[type="email"]', cfg["email"])
            await robust_fill(page, 'input[type="password"]', cfg["password"])
            await human_sleep(1, 2)
            if not await solve_slider(page):
                raise RuntimeError("Slider CAPTCHA non resolu")
            await asyncio.sleep(random.uniform(0.8, 1.5))
            await human_click(page, page.locator(
                'button:has-text("Se connecter"), button[type="submit"]').first)
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
            log.info(f"  {count} boutons Actualiser")
            if count == 0:
                log.info("  Aucune annonce disponible a actualiser pour le moment")
                return
            bumped = 0
            # Le DOM peut retirer un bouton apres son clic. Parcourir depuis la
            # fin empeche les suppressions de decaler les indices restants.
            for progress, i in enumerate(range(count - 1, -1, -1), start=1):
                btn = buttons.nth(i)
                try:
                    if not await btn.is_visible(): continue
                    await btn.scroll_into_view_if_needed()
                    await human_click(page, btn)
                    bumped += 1
                    log.info(f"  Actualiser {progress}/{count}")
                    await human_sleep(2, 5)
                except Exception as e:
                    log.warning(f"  Erreur bouton index={i}: {e}")
            log.info(f"  {bumped} annonces remontees")
            if bumped != count:
                raise RuntimeError(f"Remontee incomplete: {bumped}/{count}")
        finally:
            await page.close()

    try:
        await retry(_do, retries=5, delay=5.0, label=name)
    finally:
        await ctx.close()

# -- PARRAINAGE.CO - LOGIN ROBUSTE --------------------------------------------

async def solve_turnstile(page):
    """Resout Cloudflare Turnstile via 2captcha API."""
    api_key = os.environ.get("TWOCAPTCHA_KEY", "")
    if not api_key:
        log.warning("  TWOCAPTCHA_KEY manquant - skip Turnstile")
        return None

    # Recuperer sitekey
    sitekey = None
    try:
        sitekey = await page.evaluate("""
            () => {
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const iframe = document.querySelector('iframe[src*="turnstile"]');
                if (iframe) {
                    const m = iframe.src.match(/sitekey=([^&]+)/);
                    if (m) return m[1];
                }
                return null;
            }
        """)
    except Exception as e:
        log.debug(f"  sitekey eval: {e}")

    if not sitekey:
        log.info("  Pas de Turnstile detecte")
        return None

    log.info(f"  Turnstile detecte - sitekey={sitekey[:20]}...")
    page_url = page.url

    async with httpx.AsyncClient(timeout=30) as client:
        # Soumettre le captcha
        log.info("  Envoi a 2captcha...")
        try:
            r = await client.post("https://2captcha.com/in.php", data={
                "key": api_key,
                "method": "turnstile",
                "sitekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            })
            data = r.json()
        except Exception as e:
            log.warning(f"  2captcha in.php erreur: {e}")
            return None

        if data.get("status") != 1:
            log.warning(f"  2captcha rejet: {data}")
            return None

        captcha_id = data.get("request")
        log.info(f"  2captcha ID: {captcha_id} - attente resolution...")

        # Polling toutes les 5s max 120s
        for _ in range(24):
            await asyncio.sleep(5)
            try:
                r2 = await client.get("https://2captcha.com/res.php", params={
                    "key": api_key,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                })
                data2 = r2.json()
            except Exception as e:
                log.debug(f"  polling erreur: {e}")
                continue

            if data2.get("status") == 1:
                token = data2.get("request")
                log.info("  Captcha resolu !")
                return token
            if data2.get("request") != "CAPCHA_NOT_READY":
                log.warning(f"  2captcha erreur: {data2}")
                return None

        log.warning("  2captcha timeout")
        return None

async def inject_turnstile_token(page, token):
    """Injecte le token Turnstile et declenche les callbacks."""
    try:
        injected = await page.evaluate("""
            (token) => {
                let ok = false;

                // 1. Remplir tous les champs cf-turnstile-response
                document.querySelectorAll(
                    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[name="g-recaptcha-response"]'
                ).forEach(f => { f.value = token; ok = true; });

                // 2. Declencher callback du widget si present
                const widget = document.querySelector('[data-sitekey]');
                if (widget) {
                    const cbName = widget.getAttribute('data-callback');
                    if (cbName && typeof window[cbName] === 'function') {
                        try { window[cbName](token); ok = true; } catch(e) {}
                    }
                }

                // 3. Callback global Turnstile
                if (window.turnstile) {
                    try {
                        const widgets = document.querySelectorAll('[data-sitekey]');
                        widgets.forEach(w => {
                            const cb = w.getAttribute('data-callback');
                            if (cb && typeof window[cb] === 'function') {
                                window[cb](token);
                                ok = true;
                            }
                        });
                    } catch(e) {}
                }

                return ok;
            }
        """, token)
        log.info(f"  Injection token OK (injected={injected})")
        return True
    except Exception as e:
        log.warning(f"  Injection token erreur: {e}")
        return False

async def smart_login_parrainage(page, email, password):
    EMAIL_SEL  = ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="mail"]']
    PASS_SEL   = ['input[type="password"]', 'input[name="password"]']
    SUBMIT_SEL = ['button[type="submit"]', 'button:has-text("Connexion")', 'input[type="submit"]']

    for attempt in range(3):
        log.info(f"  Login tentative {attempt+1}/3")
        await page.screenshot(path="debug_parrainage_avant.png")

        # Attendre Cloudflare + chargement complet
        await wait_cloudflare(page)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(2)

        # Attendre qu'un input soit present
        found = False
        for sel in EMAIL_SEL:
            try:
                await page.wait_for_selector(sel, timeout=10000, state="visible")
                found = True
                break
            except Exception:
                continue
        if not found:
            for frame in page.frames:
                for sel in EMAIL_SEL:
                    try:
                        await frame.wait_for_selector(sel, timeout=3000, state="visible")
                        found = True
                        break
                    except Exception:
                        continue
                if found: break

        if not found:
            log.warning(f"  Tentative {attempt+1}: inputs invisibles - reload")
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(4)
            continue

        ok_email = await smart_fill(page, EMAIL_SEL, email)
        await human_sleep(0.5, 1)
        ok_pass  = await smart_fill(page, PASS_SEL, password)
        await human_sleep(1, 2)

        if not ok_email or not ok_pass:
            log.warning(f"  Tentative {attempt+1}: remplissage incomplet - reload")
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(4)
            continue

        # Resoudre Turnstile avant submit
        token = await solve_turnstile(page)
        if token:
            await inject_turnstile_token(page, token)
            await asyncio.sleep(0.5)
            # Soumettre le formulaire via JS immediatement apres injection
            submitted_js = await page.evaluate("""
                () => {
                    const form = document.querySelector('form');
                    if (form) { form.submit(); return true; }
                    return false;
                }
            """)
            if submitted_js:
                log.info("  Submit JS OK")
            else:
                # Fallback clic bouton
                for sel in SUBMIT_SEL:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=5000)
                        await btn.click()
                        log.info(f"  Submit click: {sel}")
                        break
                    except Exception:
                        continue
        else:
            # Pas de Turnstile - clic normal
            for sel in SUBMIT_SEL:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=5000)
                    await human_click(page, btn)
                    log.info(f"  Submit: {sel}")
                    break
                except Exception:
                    continue

        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(2)
        await page.screenshot(path="debug_parrainage_apres.png")

        if "/login" not in page.url:
            log.info("  Login success")
            return True

        log.warning(f"  Tentative {attempt+1} echouee - URL: {page.url}")
        await page.goto(page.url.split("?")[0], wait_until="domcontentloaded")
        await asyncio.sleep(4)

    log.error("  Retry login: echec apres 3 tentatives")
    return False

# -- PARRAINAGE.CO ------------------------------------------------------------
async def run_parrainage(browser):
    cfg = CONFIG["parrainage"]
    name = "parrainage_co"
    log.info(f"\n--- parrainage.co ---")
    ctx = await new_context(browser)

    async def _do():
        rm_cookie = cfg.get("rm_cookie", "")
        email     = cfg.get("email", "")
        password  = cfg.get("password", "")

        page = await ctx.new_page()
        try:
            # Cookie en priorite
            if rm_cookie:
                await ctx.add_cookies([{
                    "name": "parrainageco_rm",
                    "value": rm_cookie.strip(),
                    "domain": "parrainage.co",
                    "path": "/",
                }])
                log.info("  Cookie injecte")

            await page.goto(f"{cfg['url']}/account/offers",
                            wait_until="domcontentloaded", timeout=60000)
            await human_sleep(2, 4)

            if "/login" in page.url:
                log.info("  Cookie invalide -> login auto")
                if not email or not password:
                    raise RuntimeError("Cookie expire ET credentials manquants")
                await page.goto(f"{cfg['url']}/account/login",
                                wait_until="domcontentloaded", timeout=60000)
                await human_sleep(2, 3)
                ok = await smart_login_parrainage(page, email, password)
                if not ok:
                    raise RuntimeError("Login echoue")
            else:
                log.info("  Cookie valid")

            # Boost-all
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            resp = await page.goto(f"{cfg['url']}/account/offers/boost-all",
                                   wait_until="domcontentloaded", timeout=30000)
            log.info(f"  boost-all -> {resp.status if resp else '?'} {page.url}")
            if not resp or not (200 <= resp.status < 400):
                raise RuntimeError(f"boost-all HTTP {resp.status if resp else 'sans reponse'}")
            if "/login" in page.url:
                raise RuntimeError("Session expiree pendant boost-all")
            await human_sleep(2, 4)
            log.info("  Boost success")
        finally:
            await page.close()

    try:
        await retry(_do, retries=2, label=name)
    finally:
        await ctx.close()


async def run_referralcode(browser):
    cfg = CONFIG["referralcode"]
    name = "referralcode"
    log.info(f"\n--- referralcode.tv ---")
    ctx = await new_context(browser)

    async def _do():
        page = await ctx.new_page()
        try:
            await page.goto(f"{cfg['url']}/login/", wait_until="networkidle", timeout=60000)
            await human_sleep(2, 4)
            await page.screenshot(path="debug_referralcode_login.png")

            EMAIL_SEL = ['input[type="email"]', 'input[name="email"]',
                         'input[placeholder*="mail" i]', 'input[name="username"]']
            ok_email = await smart_fill(page, EMAIL_SEL, cfg["email"], timeout=15000)
            if not ok_email:
                await page.screenshot(path="debug_referralcode_login.png")
                raise RuntimeError("Champ email introuvable")
            await human_sleep(0.5, 1)
            PASS_SEL = ['input[type="password"]', 'input[name="password"]']
            await smart_fill(page, PASS_SEL, cfg["password"], timeout=10000)
            await human_sleep(1, 2)

            await human_click(page, page.locator(
                'button:has-text("SIGN IN"), button[type="submit"], input[type="submit"]').first)

            try:
                await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            except Exception:
                pass
            try:
                # "networkidle" here hung the full 30s default timeout and
                # killed the login 3 retries in a row, repeatedly (GH runs
                # 31717355757, 31793636589, 31815661689, 31866504330 —
                # always "Timeout 30000ms exceeded" on this exact line,
                # always right after email+password were already filled and
                # submitted successfully). The same automated login DID
                # succeed at least once with the same networkidle wait
                # (data/captures/referralcode-tv-edit-map.json, login="ok",
                # 2026-08-12/13) before it started failing this way, which
                # points at a flaky background request (analytics/chat
                # widget) that intermittently never lets the page reach true
                # network idle -- not an auth/CAPTCHA block, which would
                # fail every time, not intermittently. The URL check just
                # below is what actually matters; domcontentloaded reaches
                # that safely, with a short bounded timeout so a stalled
                # background request can never hard-fail the login again.
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await human_sleep(2, 4)

            if "/login" in page.url:
                await page.screenshot(path="debug_referralcode_login.png")
                raise RuntimeError("Login echoue")
            log.info(f"  Login OK - {page.url}")

            # Aller sur la page listings
            try:
                await page.goto(f"{cfg['url']}/my-account/?tab=listings", wait_until="networkidle", timeout=45000)
            except Exception:
                await page.goto(f"{cfg['url']}/my-account/?tab=listings", wait_until="domcontentloaded", timeout=45000)
            await human_sleep(2, 4)

            # Cliquer le bouton Boost UNE SEULE FOIS par run
            # (le site autorise 5x/jour, on en fait 1 par execution du workflow)
            try:
                btn = page.locator('button#cliccami').first
                await btn.wait_for(state="visible", timeout=8000)
                page_text = await page.inner_text("body")
                if "can click 0" in page_text:
                    log.info("  Limite journaliere deja atteinte")
                else:
                    await btn.scroll_into_view_if_needed()
                    await human_click(page, btn)
                    log.info("  Boost effectue")
                    await human_sleep(3, 6)
            except Exception as e:
                log.warning(f"  Boost echoue: {e}")
        finally:
            await page.close()

    try:
        await retry(_do, retries=3, label=name)
    finally:
        await ctx.close()

# -- MAIN ---------------------------------------------------------------------
RUNNERS = {"super": run_super, "code": run_code, "parrainage": run_parrainage, "referralcode": run_referralcode}

async def main():
    to_run = TARGET_SITES if TARGET_SITES else list(RUNNERS.keys())
    random.shuffle(to_run)

    # Verification 24h super-parrain
    if to_run == ["super"] or (len(to_run) == 1 and "super" in to_run):
        # Defense in depth: while content canary is not WRITE_VERIFIED, do not
        # consume the 24h slot with a historical bump-only pass.
        try:
            from lib.super_parrain_schedule import (
                is_super_parrain_canary_pending,
                super_parrain_runtime_mode,
            )

            if is_super_parrain_canary_pending():
                log.info(
                    "  CANARY_PENDING (%s) — bumper blocked; "
                    "content canary owns next eligible slot (no last_super_run write)",
                    super_parrain_runtime_mode(),
                )
                return
        except Exception as e:
            log.warning("  CANARY_PENDING check failed (%s) — fail-safe: skip super bumper", e)
            return

        last_file = "last_super_run.txt"
        if os.path.exists(last_file):
            try:
                with open(last_file) as f:
                    last = datetime.fromisoformat(f.read().strip())
                elapsed = (datetime.now() - last).total_seconds() / 3600
                log.info(f"  Dernier run super: {elapsed:.1f}h")
                if elapsed < 24:
                    log.info("  < 24h - run ignore")
                    return
            except Exception:
                pass

    log.info("=" * 50)
    log.info(f"  Parrainage Auto-Bumper - {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    log.info(f"  Sites: {', '.join(to_run)}")
    log.info("=" * 50)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ])

        failures = []
        executed = 0
        for site_id in to_run:
            runner = RUNNERS.get(site_id)
            if not runner: continue
            cfg = CONFIG.get(site_id, {})
            if site_id != "parrainage" and not cfg.get("email"):
                log.warning(f"  {site_id} - credentials manquants")
                continue
            if site_id == "parrainage" and not cfg.get("rm_cookie") and not cfg.get("email"):
                log.warning(f"  parrainage - RM_COOKIE et credentials manquants")
                continue
            if site_id != "parrainage" and site_id != "super" and site_id != "code" and not cfg.get("email"):
                log.warning(f"  {site_id} - credentials manquants")
                continue
            try:
                executed += 1
                await runner(browser)
            except Exception as e:
                log.error(f"  {site_id} - Erreur: {e}")
                failures.append(f"{site_id}: {e}")
            await human_sleep(2, 5)

        await browser.close()

    log.info("\n" + "=" * 50)
    log.info("  Cycle termine !")
    log.info("=" * 50)
    if executed == 0:
        raise RuntimeError("Aucun site execute: verifier TARGET_SITES et les secrets")
    if failures:
        raise RuntimeError("Echec de site(s): " + " | ".join(failures))

if __name__ == "__main__":
    asyncio.run(main())
