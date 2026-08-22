#!/usr/bin/env python3
"""FULL ALL-PROGRAM READ-ONLY inventory audit. No save, no bump, no live write."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.coverage import mapping_quality
from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.operator_overrides import OperatorOverrideStore, resolve_effective_value
from lib.paths import DATA_DIR, TEMPLATES_DIR
from lib.write_status import (
    AUTONOMY_HUMAN_SAVE_REQUIRED,
    AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN,
    STATUS_AUTH_BLOCKED,
    STATUS_AUTH_BLOCKED_MANUAL,
    STATUS_WRITE_VERIFIED,
    load_write_status,
)
from platforms.registry import ALL_PLATFORMS, get_adapter

OUT = DATA_DIR / "captures" / "autofresh-final-readonly-audit.json"

# Brands seen on authenticated / public inventories (do not invent extras).
RCTV_RAW_BRANDS = {
    "paypal": "PayPal",
    "gemini": "Gemini",
    "kraken": "Kraken",
    "stake": "Stake",
    "robinhood": "Robinhood",
    "swissborg": "SwissBorg",
    "wise": "Wise",
    "airbnb": "Airbnb",
    "whatnot": "Whatnot",
    "joko": "Joko",
    "okx": "OKX",
    "bybit": "Bybit",
}

FIELD_OFFER = {
    "personal_code": "code",
    "personal_link": "link",
    "referee_reward": "reward",
}


def _load(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _writer_support(platform: str) -> str:
    adapter = get_adapter(platform)
    cap = getattr(adapter, "capability", "AUTO")
    writer = ROOT / "platforms" / platform.replace("-", "_") / "writer.py"
    # 1parrainage package is oneparrainage
    if platform == "1parrainage":
        writer = ROOT / "platforms" / "oneparrainage" / "writer.py"
    if platform == "referralcode-tv":
        writer = ROOT / "platforms" / "referralcode_tv" / "writer.py"
    if platform == "parrainage-co":
        writer = ROOT / "platforms" / "parrainage_co" / "writer.py"
    if platform == "code-parrainage":
        writer = ROOT / "platforms" / "code_parrainage" / "writer.py"
    if platform == "super-parrain":
        writer = ROOT / "platforms" / "super_parrain" / "writer.py"
    if platform == "referralcodes":
        writer = ROOT / "platforms" / "referralcodes" / "writer.py"
    if platform == "referraldrop":
        writer = ROOT / "platforms" / "referraldrop" / "writer.py"
    has_writer = writer.exists()
    if platform == "referraldrop":
        return "AUTH_BLOCKED"
    if platform == "referralcodes":
        return "MANUAL_WRITE"
    if has_writer:
        return "WRITER_PRESENT"
    if cap == "MANUAL":
        return "MANUAL_WRITE"
    return "NO_WRITER"


def _template_ok(platform: str, program: str, language: str) -> dict:
    base = TEMPLATES_DIR / platform
    golden = base / f"{program}.{language}.golden.txt"
    tmpl = base / f"{program}.{language}.txt"
    meta = base / f"{program}.{language}.meta.json"
    return {
        "golden": golden.exists(),
        "template": tmpl.exists(),
        "meta": meta.exists(),
    }


def _account_inventory() -> dict[str, dict]:
    """Return {platform: {programs:set, count:int, source:str, extra:dict}}."""
    out: dict[str, dict] = {}

    inv1 = _load(DATA_DIR / "captures" / "1parrainage-full-inventory.json") or {}
    p1 = {i["program"] for i in inv1.get("items") or [] if i.get("program")}
    extras = []
    for u in inv1.get("unmapped") or []:
        extras.append(
            {
                "brand": u.get("brand"),
                "program": u.get("slug_guess")
                or ("bitget" if "bitget" in (u.get("brand") or "").lower() else None),
                "offer_id": u.get("offer_id"),
            }
        )
        if extras[-1]["program"]:
            p1.add(extras[-1]["program"])
    out["1parrainage"] = {
        "programs": p1,
        "count": int(inv1.get("blocks_found") or len(p1)),
        "source": "1parrainage-full-inventory.json",
        "duplicates": inv1.get("duplicates") or [],
        "unmapped_brands": extras,
    }

    sp = _load(DATA_DIR / "captures" / "super-parrain-report.json") or {}
    psp = {i["program"] for i in sp.get("items") or [] if i.get("program") and i.get("status") == "ok"}
    alias = {"trade-republic": "traderepublic"}
    for i in sp.get("items") or []:
        url = i.get("url") or ""
        m = re.search(r"/offres/([^/]+)/", url)
        if m:
            slug = re.sub(r"-\d+$", "", m.group(1))
            psp.add(alias.get(slug, slug))
    out["super-parrain"] = {
        "programs": psp,
        "count": len(psp),
        "source": "super-parrain-report.json (ok + URL slugs)",
    }

    pc = _load(DATA_DIR / "captures" / "parrainage-co-capture-report.json") or {}
    ppc = {i["program"] for i in pc.get("captured") or [] if i.get("program")}
    out["parrainage-co"] = {
        "programs": ppc,
        "count": int(pc.get("profile_offer_count") or len(ppc)),
        "source": "parrainage-co-capture-report.json",
    }

    # code-parrainage: no public profile; use mappings as authenticated inventory
    refs = [r for r in list_mapping_refs() if r.platform == "code-parrainage"]
    pcp = {r.program for r in refs}
    out["code-parrainage"] = {
        "programs": pcp,
        "count": len(pcp),
        "source": "platform-mappings (auth inventory; no public profile)",
    }

    raw = (DATA_DIR / "captures" / "referralcode-tv-raw.txt").read_text(encoding="utf-8", errors="replace")
    rctv_progs = set()
    for key, label in RCTV_RAW_BRANDS.items():
        if re.search(rf"\b{re.escape(label)}\b", raw, flags=re.I):
            rctv_progs.add(key)
    edit = _load(DATA_DIR / "captures" / "referralcode-tv-edit-map.json") or {}
    for m in ((edit.get("public") or {}).get("mappings") or []):
        if m.get("program"):
            rctv_progs.add(m["program"])
    auth_live = 23
    snippet = ""
    for page in (edit.get("auth") or {}).get("pages") or []:
        if "My Referral Codes 23" in (page.get("snippet") or ""):
            auth_live = 23
            snippet = "My Referral Codes 23"
            break
    out["referralcode-tv"] = {
        "programs": rctv_progs,
        "count": auth_live,
        "identified": len(rctv_progs),
        "unidentified": max(0, auth_live - len(rctv_progs)),
        "source": "referralcode-tv-raw.txt + edit-map auth (23 live)",
        "auth_snippet": snippet or "My Referral Codes 23",
        "kraken": "KRAKEN_EXISTS" if "kraken" in rctv_progs else "ABSENT",
    }

    rc_map = {r.program for r in list_mapping_refs() if r.platform == "referralcodes"}
    html = DATA_DIR / "captures" / "referralcodes-TheSuperReff.html"
    extra_rc = set()
    if html.exists():
        text = html.read_text(encoding="utf-8", errors="replace")
        for key in list(rc_map) + ["kraken", "paypal", "coinbase", "heetch", "joko", "omio", "unibet", "widilo", "wise", "airbnb"]:
            if re.search(rf"\b{re.escape(key)}\b", text, flags=re.I):
                extra_rc.add(key)
    out["referralcodes"] = {
        "programs": rc_map | extra_rc,
        "count": len(rc_map | extra_rc),
        "source": "mappings + TheSuperReff.html (public; official import path)",
    }

    rd_map = {r.program for r in list_mapping_refs() if r.platform == "referraldrop"}
    out["referraldrop"] = {
        "programs": rd_map,
        "count": len(rd_map),
        "source": "mappings (public profile only; AUTH_BLOCKED_GOOGLE)",
    }
    return out


def _canonical(offers: list[dict], store: OperatorOverrideStore, program: str) -> dict:
    offer = next((o for o in offers if o.get("lk") == program), None)
    out = {}
    for logical, ofield in FIELD_OFFER.items():
        can = offer.get(ofield) if offer else None
        ev = resolve_effective_value(program, logical, platform=None, canonical=can, store=store)
        out[logical] = {"value": ev.value, "source": ev.source}
    return out


def _classify_row(
    *,
    platform: str,
    program: str,
    on_account: bool,
    in_offers: bool,
    mapping: dict | None,
    mapping_q: str | None,
    tmpl: dict,
    writer: str,
    plat_status: dict,
    canon: dict,
) -> str:
    if writer == "AUTH_BLOCKED" or (plat_status.get("status") == "AUTH_BLOCKED_GOOGLE"):
        return "AUTH_BLOCKED"
    if on_account and not in_offers:
        return "ACCOUNT_ONLY"
    if mapping_q == "stale_mapping" or (mapping or {}).get("status") == "NOT_PRESENT_ON_ACCOUNT":
        return "STALE_MAPPING"
    if in_offers and not on_account:
        return "CANONICAL_ONLY"
    if on_account and in_offers and mapping is None:
        return "MISSING_MAPPING"
    if plat_status.get("last_compare_class") == "DOM_BLOCKED" and program == "kraken":
        return "DOM_BLOCKED"

    # Operator-validated identity diffs only (code/link). Never treat native
    # reward wording ($200 vs 200 €) or catalog leftovers as REAL_SAFE_DIFF.
    if mapping and program == "kraken":
        if plat_status.get("last_compare_class") == "NO_SAFE_DIFF" or plat_status.get(
            "content_sync"
        ) == "SYNC_VERIFIED_NO_SAFE_DIFF":
            if writer == "MANUAL_WRITE":
                return "MANUAL_WRITE"
            return "SYNC"
        pv = mapping.get("platform_values") or {}
        diffs = []
        for field in ("personal_code", "personal_link"):
            want = (canon.get(field) or {}).get("value")
            have = pv.get(field)
            src = (canon.get(field) or {}).get("source")
            if (
                want
                and have
                and _norm(str(have)) != _norm(str(want))
                and src == "GLOBAL_OPERATOR_OVERRIDE"
            ):
                diffs.append(field)
        if diffs:
            return "REAL_SAFE_DIFF"
        if plat_status.get("status") == "WRITE_VERIFIED":
            return "SYNC"

    if writer == "MANUAL_WRITE":
        return "MANUAL_WRITE"
    if mapping and tmpl.get("golden") and writer == "WRITER_PRESENT":
        if plat_status.get("content_sync") == "SYNC_VERIFIED_NO_SAFE_DIFF" and program == "kraken":
            return "SYNC"
        return "READY"
    if mapping:
        return "READY"
    if in_offers:
        return "CANONICAL_ONLY"
    return "ACCOUNT_ONLY"


def _remaining_work(plats_meta: dict[str, dict]) -> list[str]:
    """Derive durable gaps from the current authoritative platform status."""
    remaining = []

    one = plats_meta.get("1parrainage") or {}
    if not (
        one.get("status") == STATUS_WRITE_VERIFIED
        and one.get("gh_headless_save") == "PROVEN"
    ):
        remaining.append("1parrainage: unattended GH save proof incomplete")

    super_meta = plats_meta.get("super-parrain") or {}
    if super_meta.get("status") != STATUS_WRITE_VERIFIED:
        remaining.append("super-parrain: content write proof incomplete")
    elif super_meta.get("runtime_mode") != "NORMAL_BUMP":
        remaining.append("super-parrain: normal bump runtime not enabled")

    code = plats_meta.get("code-parrainage") or {}
    if not (
        code.get("status") == STATUS_WRITE_VERIFIED
        and code.get("gh_headless_save") == "PROVEN"
    ):
        remaining.append("code-parrainage: unattended save proof incomplete")

    rcodes = plats_meta.get("referralcodes") or {}
    if rcodes.get("autonomy") == AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN:
        remaining.append(
            "referralcodes: NEVER_AUTO_COMMIT until an official existing-referral update path exists"
        )

    rctv = plats_meta.get("referralcode-tv") or {}
    if (
        rctv.get("autonomy") == AUTONOMY_HUMAN_SAVE_REQUIRED
        or rctv.get("save_requires_captcha")
    ):
        remaining.append("referralcode-tv: HUMAN_SAVE_REQUIRED (CAPTCHA)")

    drop = plats_meta.get("referraldrop") or {}
    if drop.get("status") in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
        remaining.append("referraldrop: AUTH_BLOCKED_MANUAL")

    remaining.append(
        "map ACCOUNT_ONLY programs only after a canonical offer and verified account identity exist"
    )
    return remaining


def run() -> dict:
    offers = OffersRepository().load_all()
    offer_slugs = {o["lk"] for o in offers if o.get("lk")}
    store = OperatorOverrideStore()
    ws = load_write_status()
    plats_meta = ws.get("platforms") or {}
    acc = _account_inventory()

    by_plat_map: dict[str, dict[str, dict]] = defaultdict(dict)
    for ref in list_mapping_refs():
        raw = json.loads(ref.path.read_text(encoding="utf-8"))
        by_plat_map[ref.platform][ref.program] = {
            "ref": ref,
            "raw": raw,
            "quality": mapping_quality(ref.path),
        }

    rows = []
    counts = Counter()
    for platform in ALL_PLATFORMS:
        writer = _writer_support(platform)
        meta = plats_meta.get(platform) or {}
        inv = acc.get(platform) or {"programs": set(), "count": 0}
        account_progs = set(inv.get("programs") or [])
        mapped_progs = set(by_plat_map.get(platform) or {})
        union = sorted(account_progs | mapped_progs | (offer_slugs if False else set()))
        # Classify account ∪ mapped, plus CANONICAL_ONLY for offers not on this account.
        seen = set()
        for program in sorted(account_progs | mapped_progs | offer_slugs):
            on_acc = program in account_progs
            in_off = program in offer_slugs
            mp = (by_plat_map.get(platform) or {}).get(program)
            if not on_acc and not mp and in_off:
                # canonical-only cell
                cls = "AUTH_BLOCKED" if writer == "AUTH_BLOCKED" else "CANONICAL_ONLY"
                if writer == "AUTH_BLOCKED":
                    cls = "AUTH_BLOCKED"
                rows.append(
                    {
                        "platform": platform,
                        "program": program,
                        "class": cls,
                        "on_account": False,
                        "in_offers": True,
                        "mapping": False,
                        "writer": writer,
                    }
                )
                counts[cls] += 1
                seen.add(program)
                continue
            if not on_acc and not mp:
                continue
            ref = mp["ref"] if mp else None
            raw = mp["raw"] if mp else None
            q = mp["quality"] if mp else None
            lang = ref.language if ref else ("en" if platform in {"referralcodes", "referralcode-tv", "referraldrop"} else "fr")
            tmpl = _template_ok(platform, program, lang) if ref else {"golden": False, "template": False, "meta": False}
            canon = _canonical(offers, store, program)
            cls = _classify_row(
                platform=platform,
                program=program,
                on_account=on_acc,
                in_offers=in_off,
                mapping=raw,
                mapping_q=q,
                tmpl=tmpl,
                writer=writer,
                plat_status=meta,
                canon=canon,
            )
            rows.append(
                {
                    "platform": platform,
                    "program": program,
                    "class": cls,
                    "on_account": on_acc,
                    "in_offers": in_off,
                    "mapping": bool(mp),
                    "mapping_quality": q,
                    "template": tmpl,
                    "writer": writer,
                    "edit_url": (raw or {}).get("edit_url") if raw else None,
                }
            )
            counts[cls] += 1
            seen.add(program)

    account_total = sum(int((acc[p].get("count") or 0)) for p in ALL_PLATFORMS)
    fully_mapped = sum(1 for r in rows if r["on_account"] and r["mapping"] and r["class"] not in {"STALE_MAPPING", "ACCOUNT_ONLY"})
    missing = [r for r in rows if r["class"] == "MISSING_MAPPING"]
    stale = [r for r in rows if r["class"] == "STALE_MAPPING"]
    diffs = [r for r in rows if r["class"] == "REAL_SAFE_DIFF"]
    manual = [r for r in rows if r["class"] == "MANUAL_WRITE"]
    blocked = [r for r in rows if r["class"] in {"AUTH_BLOCKED", "DOM_BLOCKED"}]

    write_verified = [
        p for p, m in plats_meta.items() if (m or {}).get("status") == "WRITE_VERIFIED"
    ]
    live_capable = list(ws.get("telegram_live_capable") or [])

    remaining = _remaining_work(plats_meta)

    report = {
        "schema": "AUTOFRESH_FINAL_READONLY_AUDIT",
        "live_write": False,
        "inventories": {
            p: {
                "count": acc[p].get("count"),
                "identified_programs": sorted(acc[p].get("programs") or []),
                "source": acc[p].get("source"),
                **{k: acc[p][k] for k in acc[p] if k not in {"programs", "count", "source"}},
            }
            for p in ALL_PLATFORMS
        },
        "class_counts": dict(counts),
        "rows": rows,
        "missing_mapping": missing,
        "stale_mapping": stale,
        "real_safe_diffs": diffs,
        "summary": {
            "total_account_announcements": account_total,
            "fully_mapped": fully_mapped,
            "missing_mapping": len(missing),
            "stale_mapping": len(stale),
            "real_safe_diffs": len(diffs),
            "manual_only": len(manual),
            "blocked": len(blocked),
            "write_verified_platforms": write_verified,
            "live_capable_platforms": live_capable,
            "write_verified_count": f"{len(write_verified)}/7",
        },
        "remaining_work": remaining,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = run()
    s = report["summary"]
    print("wrote", OUT)
    print("total_account_announcements", s["total_account_announcements"])
    print("fully_mapped", s["fully_mapped"])
    print("missing_mapping", s["missing_mapping"])
    print("stale_mapping", s["stale_mapping"])
    print("real_safe_diffs", s["real_safe_diffs"])
    print("manual_only", s["manual_only"])
    print("blocked", s["blocked"])
    print("class_counts", report["class_counts"])
    print("missing", [(r["platform"], r["program"]) for r in report["missing_mapping"]])
    print("stale", [(r["platform"], r["program"]) for r in report["stale_mapping"]])
    print("diffs", [(r["platform"], r["program"]) for r in report["real_safe_diffs"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
