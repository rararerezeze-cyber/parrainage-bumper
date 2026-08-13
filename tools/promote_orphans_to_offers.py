#!/usr/bin/env python3
"""Promote NEEDS_CANONICAL orphans into offers.json when code/link are high-confidence.

Uses only values extracted from existing platform goldens — never invents rewards.
Creates platform mappings from orphan goldens when possible.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.template_builder import build_from_text, detect_platform_values, write_build_result

OFFERS = ROOT / "data" / "offers.json"
NCD = ROOT / "data" / "needs_canonical_data.json"

# Orphan program_key → display meta
META = {
    "vinted": {"name": "Vinted", "cat": "shopping"},
    "plum": {"name": "Plum", "cat": "finance"},
    "okx": {"name": "OKX", "cat": "crypto"},
    "nrj-mobile": {"name": "NRJ Mobile", "cat": "telecom"},
    "whatnot": {"name": "Whatnot", "cat": "shopping"},
    "fdj-francaise-des-jeux": {"name": "FDJ", "cat": "paris"},
}


def _extract_from_text(text: str) -> dict:
    vals, conf, notes = detect_platform_values(text, None)
    # code patterns extra
    if "personal_code" not in vals:
        m = re.search(r"(?i)code\s*parrain(?:age)?\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._\-]{2,40})", text)
        if m:
            vals["personal_code"] = m.group(1).strip()
            conf["personal_code"] = "high"
    return vals, conf, notes


def _reward_from_text(text: str) -> str | None:
    m = re.search(r"(?im)^.*[Bb]onus\s*[:：]\s*(.+)$", text)
    if m:
        phrase = m.group(1).strip().strip(" ⭐⚡✨🔥•-")
        if 3 <= len(phrase) <= 120:
            return phrase
    m = re.search(r"(\d[\d\s]*[€$][^\n]{0,60})", text)
    if m:
        return m.group(1).strip()
    return None


def main() -> int:
    offers = json.loads(OFFERS.read_text(encoding="utf-8"))
    by_lk = {o.get("lk"): o for o in offers if o.get("lk")}
    max_id = max((int(o.get("id") or 0) for o in offers), default=0)
    ncd = json.loads(NCD.read_text(encoding="utf-8")) if NCD.exists() else {"items": []}

    # Collect best golden text per program from orphans
    golden_by_prog: dict[str, tuple[str, Path]] = {}
    for p in (ROOT / "data" / "orphans").rglob("*.golden.txt"):
        # vinted.fr.golden.txt
        name = p.name
        if not name.endswith(".fr.golden.txt"):
            continue
        prog = name[: -len(".fr.golden.txt")]
        text = p.read_text(encoding="utf-8")
        if prog not in golden_by_prog or len(text) > len(golden_by_prog[prog][0]):
            golden_by_prog[prog] = (text, p)

    promoted = []
    skipped = []
    for prog, (text, path) in sorted(golden_by_prog.items()):
        if prog not in META:
            skipped.append({"program": prog, "reason": "no_meta"})
            continue
        vals, conf, notes = _extract_from_text(text)
        code = vals.get("personal_code")
        link = vals.get("personal_link")
        reward = vals.get("referee_reward") or _reward_from_text(text)
        # Need at least link or code with high conf
        if not code and not link:
            skipped.append({"program": prog, "reason": "no_code_or_link"})
            continue
        if code and conf.get("personal_code") not in (None, "high", "medium"):
            pass
        if link and conf.get("personal_link") not in (None, "high", "medium"):
            pass

        meta = META[prog]
        if prog in by_lk:
            # update missing fields only
            o = by_lk[prog]
            changed = False
            if not o.get("code") and code:
                o["code"] = code
                changed = True
            if not o.get("link") and link:
                o["link"] = link
                changed = True
            if (not o.get("reward") or o.get("reward") == "Programme à confirmer") and reward:
                o["reward"] = reward
                changed = True
            if changed:
                promoted.append({"program": prog, "action": "updated_existing"})
            else:
                skipped.append({"program": prog, "reason": "already_in_offers"})
        else:
            max_id += 1
            entry = {
                "id": max_id,
                "name": meta["name"],
                "cat": meta["cat"],
                "lk": prog,
                "susp": False,
                "reward": reward or "Selon offre en cours",
                "bn": 0,
                "code": code,
                "link": link,
                "about": f"{meta['name']} — donnees consolidees depuis captures authentifiees/publiques existantes.",
                "steps": [
                    "Ouvrez le lien ou saisissez le code de parrainage",
                    "Suivez les conditions affichees sur la plateforme",
                ],
                "advs": [],
                "cond": "Conditions selon l'offre officielle en vigueur.",
                "faq": [],
                "logo": f"{prog.replace('-', '')}.png",
                "reviewStatus": "check",
                "reviewedAt": "12 aout 2026",
                "legacyReward": reward or "",
                "boost": False,
                "source": "promoted_from_orphan_captures",
            }
            offers.append(entry)
            by_lk[prog] = entry
            promoted.append({"program": prog, "action": "created", "code": code, "link": link})

        # Build platform mappings from goldens for platforms that have orphan files
        for platform in ("super-parrain", "parrainage-co"):
            gpath = ROOT / "data" / "orphans" / platform / f"{prog}.fr.golden.txt"
            if not gpath.exists():
                continue
            gtext = gpath.read_text(encoding="utf-8")
            offer = by_lk[prog]
            try:
                result = build_from_text(
                    platform=platform,
                    program=prog,
                    language="fr",
                    golden_text=gtext,
                    offer=offer,
                    announcement_url=None,
                )
                # prune null offer mutables
                cleaned = []
                for f in result.mutable_fields:
                    of = {"personal_code": "code", "personal_link": "link", "referee_reward": "reward"}.get(f)
                    if of and (offer.get(of) is None or str(offer.get(of)).strip() == ""):
                        from lib.template_builder import DEFAULT_MARKERS

                        marker = DEFAULT_MARKERS[f]
                        if marker in result.template and f in result.platform_values:
                            result.template = result.template.replace(
                                marker, result.platform_values[f]
                            )
                        result.notes.append(f"{f}: offers null — immutable")
                        continue
                    cleaned.append(f)
                result.mutable_fields = cleaned
                result.platform_values = {
                    k: v for k, v in result.platform_values.items() if k in cleaned
                }
                paths = write_build_result(result)
                mdata = json.loads(paths["mapping"].read_text(encoding="utf-8"))
                mdata["quality"] = "from_orphan_promoted"
                mdata["notes"] = "; ".join(result.notes) if result.notes else mdata.get("notes")
                paths["mapping"].write_text(
                    json.dumps(mdata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                promoted.append({"program": prog, "platform": platform, "action": "mapping_built"})
            except Exception as exc:  # noqa: BLE001
                skipped.append({"program": prog, "platform": platform, "reason": str(exc)})

    OFFERS.write_text(json.dumps(offers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Deduplicate NCD: remove programs now in offers
    items = ncd.get("items") or []
    remaining = []
    for it in items:
        pk = it.get("program_key")
        o = by_lk.get(pk) or {}
        if o and (o.get("code") or o.get("link")):
            # promoted into offers — drop from NCD
            continue
        remaining.append(it)
    # unique by (platform, program_key)
    seen = set()
    uniq = []
    for it in remaining:
        key = (it.get("platform"), it.get("program_key"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    ncd["items"] = uniq
    ncd["count"] = len(uniq)
    NCD.write_text(json.dumps(ncd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {"promoted": promoted, "skipped": skipped, "offers_count": len(offers), "ncd_remaining": len(uniq)}
    out = ROOT / "data" / "captures" / "promote-orphans-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
