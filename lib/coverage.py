"""Matrice programme x plateforme + rapport de couverture V1."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.paths import DATA_DIR, MAPPINGS_DIR
from platforms.registry import ALL_PLATFORMS, platform_capability


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_profiles() -> dict[str, Any]:
    data = _load_json(DATA_DIR / "platform-profiles.json") or {}
    return data.get("platforms") or {}


def load_needs_canonical() -> list[dict[str, Any]]:
    data = _load_json(DATA_DIR / "needs_canonical_data.json") or {}
    return list(data.get("items") or [])


def mapping_quality(mapping_path: Path) -> str:
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown"
    status = str(data.get("status") or "").upper()
    q = (data.get("quality") or "").lower()
    if status in {
        "NOT_PRESENT_ON_ACCOUNT",
        "STALE_MAPPING",
        "NOT_ON_ACCOUNT",
        "NOT_ON_PUBLIC_PROFILE",
    }:
        return "stale_mapping"
    if data.get("write_eligible") is False and "stale" in q:
        return "stale_mapping"
    if q in {"full_edit", "auth_edit_refetch", "public_refetch", "from_orphan_promoted", "auth_orphan_promoted"}:
        return "captured"
    if "truncat" in q or "partial" in q or "list_preview" in q or "capture_partial" in q:
        return "capture_partial"
    if data.get("template_status") == "missing_source":
        return "missing_source"
    if data.get("sync_mode") in {"manual_review_required", "MANUAL"}:
        return "manual_write"
    return "captured"


def build_matrix() -> dict[str, Any]:
    offers = OffersRepository().load_all()
    programs = sorted({o.get("lk") for o in offers if o.get("lk")})
    ncd = load_needs_canonical()
    orphan_keys = sorted({i.get("program_key") for i in ncd if i.get("program_key")})

    # All program keys known
    all_programs = sorted(set(programs) | set(orphan_keys))

    # Index mappings: platform -> program -> meta
    by_pp: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for ref in list_mapping_refs():
        q = mapping_quality(ref.path)
        by_pp[ref.platform][ref.program] = {
            "language": ref.language,
            "path": str(ref.path.as_posix()),
            "quality": q,
            "status": q,
        }

    profiles = load_profiles()
    platform_rows = []
    cell_status = Counter()

    for pid in ALL_PLATFORMS:
        cap = platform_capability(pid)
        prof = profiles.get(pid) or {}
        auth_status = prof.get("auth_status")
        mapped = by_pp.get(pid) or {}
        partial = sum(1 for v in mapped.values() if v["quality"] == "capture_partial")
        full = sum(1 for v in mapped.values() if v["quality"] == "captured")
        manual = sum(1 for v in mapped.values() if v["quality"] == "manual_write")
        inventory = full + manual  # inventaire fiable (y compris MANUAL_WRITE)

        if auth_status == "AUTH_BLOCKED_GOOGLE":
            read_state = "AUTH_BLOCKED" if inventory == 0 else "READ_PUBLIC_ONLY"
        elif full > 0 and partial == 0:
            read_state = "READ_OK"
        elif inventory > 0 and partial == 0 and full == 0:
            read_state = "READ_OK"  # mappings presents (ex. MANUAL inventory)
        elif full + partial + manual > 0:
            read_state = "CAPTURE_PARTIAL" if partial else "READ_OK"
        elif cap == "CAPTURE_PENDING":
            read_state = "CAPTURE_PENDING"
        elif cap == "MANUAL":
            read_state = "MANUAL"
        else:
            read_state = "NO_MAPPINGS"

        # Write capability (conservative)
        if pid == "super-parrain":
            write_state = "WRITE_PREPARED_COOLDOWN"
        elif pid == "parrainage-co" and full > 0:
            write_state = "WRITE_TODO"
        elif pid in {"referralcodes"}:
            write_state = "MANUAL_WRITE"  # prefer official import
        elif pid == "referraldrop":
            write_state = "AUTH_BLOCKED"
        elif pid == "referralcode-tv":
            write_state = "CAPTURE_PENDING" if full == 0 else "MANUAL_WRITE"
        elif pid == "code-parrainage":
            write_state = "CAPTURE_PARTIAL" if partial or full else "CAPTURE_PENDING"
        elif pid == "1parrainage":
            write_state = "WRITE_TODO" if full > 0 else "CAPTURE_PENDING"
        else:
            write_state = "UNKNOWN"

        row = {
            "platform": pid,
            "capability": cap,
            "read": read_state,
            "write": write_state,
            "mapped_count": len(mapped),
            "full_quality": full,
            "partial_quality": partial,
            "manual_quality": manual,
            "inventory_quality": inventory,
            "profile_url": prof.get("profile_url"),
            "auth_status": auth_status,
        }
        platform_rows.append(row)

        for prog in all_programs:
            if prog in mapped:
                st = mapped[prog]["quality"]
            elif prog in orphan_keys and pid == "super-parrain":
                # orphan captured as NCD on super-parrain only for now
                st = "needs_canonical_data"
            elif auth_status == "AUTH_BLOCKED_GOOGLE" and prog not in mapped:
                st = "auth_blocked"
            elif cap == "CAPTURE_PENDING" and not mapped:
                st = "capture_pending"
            elif cap == "MANUAL" and not mapped:
                st = "manual"
            else:
                st = "missing_on_platform"
            cell_status[st] += 1

    # Coverage numbers
    total_mappings = sum(r["mapped_count"] for r in platform_rows)
    usable_read = sum(
        1
        for r in platform_rows
        if r["read"] in {"READ_OK", "CAPTURE_PARTIAL"}
    )
    writers_functional = sum(
        1 for r in platform_rows if r["write"] in {"WRITE_PREPARED_COOLDOWN"}
    )

    report = {
        "version": 1,
        "programs_in_offers": len(programs),
        "orphan_programs": len(orphan_keys),
        "programs_total_known": len(all_programs),
        "platforms": len(ALL_PLATFORMS),
        "mapped_pairs": total_mappings,
        "platforms_usable_read": usable_read,
        "writers_functional": writers_functional,
        "cell_status_counts": dict(cell_status),
        "platform_rows": platform_rows,
        "orphan_keys": orphan_keys,
        "programs": all_programs,
        "mappings_by_platform": {k: list(v.keys()) for k, v in by_pp.items()},
    }
    return report


def write_coverage_report() -> Path:
    report = build_matrix()
    out = DATA_DIR / "coverage-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Human summary
    txt = DATA_DIR / "coverage-report.txt"
    lines = [
        "=== AUTOFRESH COVERAGE REPORT ===",
        f"Programs in offers.json: {report['programs_in_offers']}",
        f"Orphan programs (needs_canonical_data): {report['orphan_programs']}",
        f"Programs total known: {report['programs_total_known']}",
        f"Platforms: {report['platforms']}",
        f"Mapped pairs (program x platform): {report['mapped_pairs']}",
        f"Platforms usable (READ): {report['platforms_usable_read']}",
        f"Writers functional: {report['writers_functional']}",
        "",
        "Per platform:",
    ]
    for r in report["platform_rows"]:
        lines.append(
            f"  {r['platform']:18} READ={r['read']:18} WRITE={r['write']:22} "
            f"mapped={r['mapped_count']:3} full={r['full_quality']} partial={r['partial_quality']}"
        )
    lines.append("")
    lines.append("Cell status counts:")
    for k, v in sorted(report["cell_status_counts"].items()):
        lines.append(f"  {k}: {v}")
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def print_coverage() -> None:
    path = write_coverage_report()
    print((DATA_DIR / "coverage-report.txt").read_text(encoding="utf-8"))
    print(f"[json] {path}")
