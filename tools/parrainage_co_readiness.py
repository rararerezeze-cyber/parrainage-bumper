#!/usr/bin/env python3
"""READ-ONLY readiness scan for Parrainage.co (no login, no writes).

Compares inventory, public pages, golden, render structure, and offers drift.
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text
from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template

PROFILE = "https://parrainage.co/user/adrien89"


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    return unescape(text)


def extract_body(html: str) -> str | None:
    m = re.search(r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)", html)
    if m:
        return m.group(1)
    text = html_to_text(html)
    m = re.search(r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)", text)
    if m:
        return m.group(1).strip()
    return None


def norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").split("\n")).strip()


def golden_lines_present(golden: str, haystack: str) -> tuple[bool, list[str]]:
    """True if every non-trivial golden line appears in public HTML/text."""
    missing: list[str] = []
    for line in golden.splitlines():
        s = line.strip()
        if len(s) < 8:
            continue
        if s not in haystack:
            missing.append(s[:80])
    return (not missing, missing)


def main() -> int:
    repo = MappingRepository()
    tpl = TemplateRepository()
    rend = Renderer(OffersRepository())
    refs = sorted(
        [r for r in list_mapping_refs() if r.platform == "parrainage-co"],
        key=lambda r: r.program,
    )

    print("=== PROFILE ===")
    html = fetch_text(PROFILE)
    (ROOT / "data/captures/parrainage-co-public.html").write_text(html, encoding="utf-8")
    offer_paths = list(dict.fromkeys(re.findall(r'href="(/offers/\d+)"', html)))
    print(f"profile_offers_linked={len(offer_paths)}")

    id_to_prog: dict[str, str] = {}
    for r in refs:
        m = repo.load(r.platform, r.program, r.language)
        if m.announcement_url and "/offers/" in m.announcement_url:
            oid = m.announcement_url.rstrip("/").split("/")[-1]
            id_to_prog[oid] = r.program

    profile_ids = {p.rstrip("/").split("/")[-1] for p in offer_paths}
    mapped_ids = set(id_to_prog)
    print(f"mapped_ids={len(mapped_ids)} profile_ids={len(profile_ids)}")
    print(f"on_profile_not_mapped={sorted(profile_ids - mapped_ids)}")
    print(f"mapped_not_on_profile={sorted(mapped_ids - profile_ids)}")

    rows: list[dict] = []
    print("\n=== PER ANNOUNCEMENT PUBLIC REFETCH ===")
    for r in refs:
        m = repo.load(r.platform, r.program, r.language)
        row: dict = {
            "program": r.program,
            "url": m.announcement_url,
            "mutable_fields": list(m.mutable_fields or []),
            "sync_mode": m.sync_mode,
        }
        try:
            golden = tpl.load_golden(r.platform, r.program, r.language)
            row["golden_len"] = len(golden)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"golden:{exc}"
            rows.append(row)
            print(r.program, row)
            continue

        try:
            offer = rend.offers.get_by_slug(r.program)
            variables = rend.build_variables(m, offer=offer)
            template = tpl.load_text(r.platform, r.program, r.language)
            rendered = rend.render(template, m, offer=offer)
            row["render_ok"] = True
            hist = dict(m.platform_values or {})
            extracted = extract_values_via_template(
                template, golden, m.mutable_fields, m.markers
            )
            for k, v in extracted.items():
                hist.setdefault(k, v)
            check = golden
            for field in m.mutable_fields:
                old = hist.get(field)
                new = variables.get(field)
                if old and new is not None and old in check:
                    check = check.replace(old, new, 1)
            row["structure_preserved"] = check == rendered
            changed: dict[str, dict] = {}
            for f in m.mutable_fields:
                old = (m.platform_values or {}).get(f) or hist.get(f)
                new = variables.get(f)
                if old != new and new is not None:
                    changed[f] = {"old": old, "new": new}
            row["changed_fields"] = list(changed.keys())
            row["needs_update"] = bool(changed)
        except Exception as exc:  # noqa: BLE001
            row["render_ok"] = False
            row["render_error"] = str(exc)
            row["structure_preserved"] = None
            row["needs_update"] = None

        if m.announcement_url:
            try:
                ah = fetch_text(m.announcement_url)
                body = extract_body(ah)
                plain = html_to_text(ah)
                hay = ah + "\n" + plain
                row["public_fetch_ok"] = True
                row["public_body_len"] = len(body or "")
                lines_ok, missing = golden_lines_present(golden, hay)
                row["golden_lines_in_public"] = lines_ok
                if missing:
                    row["golden_missing_lines"] = missing[:5]
                # exact body match when extract works; else line-level is authoritative for RO
                if body:
                    row["golden_match_public"] = norm(body) == norm(golden) or lines_ok
                else:
                    row["golden_match_public"] = lines_ok
                    row["public_body_empty"] = body is None
                pv = m.platform_values or {}
                row["public_has_platform_code"] = bool(
                    pv.get("personal_code") and pv["personal_code"] in hay
                )
                row["public_has_platform_link"] = bool(
                    pv.get("personal_link") and pv["personal_link"] in hay
                )
                # Flag private/edit URLs wrongly stored as announcement_url
                if "/account/" in (m.announcement_url or ""):
                    row["bad_announcement_url"] = True
            except Exception as exc:  # noqa: BLE001
                row["public_fetch_ok"] = False
                row["public_error"] = str(exc)

        rows.append(row)
        print(
            f"{r.program:16} render={row.get('render_ok')} "
            f"struct={row.get('structure_preserved')} need={row.get('needs_update')} "
            f"golden_pub={row.get('golden_match_public')} "
            f"fields={row.get('changed_fields') or row.get('render_error')}"
        )

    summary = {
        "platform": "parrainage-co",
        "mode": "READ_ONLY_READINESS",
        "profile_url": PROFILE,
        "mappings": len(refs),
        "profile_offer_links": len(offer_paths),
        "on_profile_not_mapped": sorted(profile_ids - mapped_ids),
        "mapped_not_on_profile": sorted(mapped_ids - profile_ids),
        "render_ok": sum(1 for x in rows if x.get("render_ok")),
        "structure_ok": sum(1 for x in rows if x.get("structure_preserved")),
        "needs_update": sum(1 for x in rows if x.get("needs_update")),
        "golden_match_public": sum(1 for x in rows if x.get("golden_match_public")),
        "public_fetch_ok": sum(1 for x in rows if x.get("public_fetch_ok")),
        "blockers": [
            x
            for x in rows
            if not x.get("render_ok")
            or x.get("structure_preserved") is False
            or not x.get("public_fetch_ok")
            or x.get("bad_announcement_url")
            or x.get("golden_lines_in_public") is False
        ],
        "programs": rows,
        "writer_exists": (ROOT / "platforms/parrainage_co/writer.py").exists(),
        "known_data_gaps": {
            "offers_missing_code": ["boursobank"],
            "offers_missing_link": ["winamax"],
            "bad_announcement_url": ["paypal"],
            "on_profile_unmapped_programs": [
                "vinted (118536)",
                "plum (122884)",
                "okx (123892)",
                "nrj-mobile (125100)",
                "whatnot (125961)",
            ],
        },
        "next_after_super_parrain": [
            "fix paypal announcement_url (currently account/edit of another offer)",
            "fix offers.json: boursobank.code, winamax.link",
            "optionally onboard 5 profile-only offers if in offers.json later",
            "canary Kraken only (same vertical method as Super-Parrain)",
            "post-verify then progressive rollout",
        ],
    }
    out = ROOT / "data/captures/parrainage-co-readiness.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n=== SUMMARY ===")
    for k in (
        "mappings",
        "profile_offer_links",
        "render_ok",
        "structure_ok",
        "needs_update",
        "golden_match_public",
        "public_fetch_ok",
        "on_profile_not_mapped",
        "mapped_not_on_profile",
    ):
        print(f"{k}: {summary[k]}")
    print("blockers:", [b["program"] for b in summary["blockers"]])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
