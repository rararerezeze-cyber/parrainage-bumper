"""ReferralCodes.com Agent Import — official schema + payload builder.

Source of truth (public docs): https://referralcodes.com/agents
Import UI: https://referralcodes.com/profile/import/agent

JSON schema (version 1.0):
  {
    "version": "1.0",
    "items": [
      {
        "shop": "brand name or domain",      # required
        "discount": "reward for new user",   # required
        "url": "referral link (optional)",
        "code": "referral code (optional)",
        "description": "optional notes"
      }
    ]
  }
Rules:
  - each item MUST include shop + discount
  - each item MUST include at least one of url or code
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.operator_overrides import apply_effective_to_offer
from lib.renderer import MappingRepository

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0"
IMPORT_UI = "https://referralcodes.com/profile/import/agent"
DOCS_URL = "https://referralcodes.com/agents"

# Map Autofresh program slug → Agent Import "shop" label (brand or domain)
SHOP_ALIASES: dict[str, str] = {
    "kraken": "kraken",
    "coinbase": "coinbase",
    "revolut": "revolut",
    "binance": "binance",
    "swissborg": "swissborg",
    "bybit": "bybit",
    "paypal": "paypal",
    "airbnb": "airbnb",
    "ledger": "ledger",
    "gemini": "gemini",
    "bitstack": "bitstack",
    "deblock": "deblock",
    "robinhood": "robinhood",
    "igraal": "igraal",
    "poulpeo": "poulpeo",
    "ebuyclub": "ebuyclub",
    "joko": "joko",
    "widilo": "widilo",
    "betclic": "betclic",
    "unibet": "unibet",
    "winamax": "winamax",
    "heetch": "heetch",
    "omio": "omio",
    "traderepublic": "trade republic",
    "totalenergies": "totalenergies",
    "boursobank": "boursobank",
    "acheel": "acheel",
}


@dataclass
class ItemValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    item: dict[str, Any] = field(default_factory=dict)


@dataclass
class PayloadValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    item_results: list[ItemValidation] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def validate_item(item: dict[str, Any]) -> ItemValidation:
    errors: list[str] = []
    shop = (item.get("shop") or "").strip() if item.get("shop") is not None else ""
    discount = (
        (item.get("discount") or "").strip() if item.get("discount") is not None else ""
    )
    url = item.get("url")
    code = item.get("code")
    if url is not None and not str(url).strip():
        url = None
    if code is not None and not str(code).strip():
        code = None
    if not shop:
        errors.append("shop_required")
    if not discount:
        errors.append("discount_required")
    if not url and not code:
        errors.append("url_or_code_required")
    if url and not re.match(r"^https?://", str(url), re.I):
        errors.append("url_must_be_http(s)")
    # FAQ: no bit.ly / intermediate networks
    if url:
        low = str(url).lower()
        if any(x in low for x in ("bit.ly", "t.co/", "tinyurl", "cutt.ly")):
            errors.append("url_looks_shortened_or_indirect")
    clean = {
        "shop": shop or None,
        "discount": discount or None,
        "url": str(url).strip() if url else None,
        "code": str(code).strip() if code else None,
        "description": item.get("description"),
    }
    return ItemValidation(ok=not errors, errors=errors, item=clean)


def validate_payload(payload: dict[str, Any]) -> PayloadValidation:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return PayloadValidation(ok=False, errors=["payload_not_object"])
    ver = str(payload.get("version") or "")
    if ver != SCHEMA_VERSION:
        errors.append(f"version_must_be_{SCHEMA_VERSION}_got_{ver!r}")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items_must_be_non_empty_list")
        return PayloadValidation(ok=False, errors=errors, payload=payload)
    item_results = [validate_item(it if isinstance(it, dict) else {}) for it in items]
    for i, r in enumerate(item_results):
        if not r.ok:
            errors.append(f"item[{i}]:{','.join(r.errors)}")
    clean = {
        "version": SCHEMA_VERSION,
        "items": [r.item for r in item_results],
    }
    return PayloadValidation(
        ok=not errors, errors=errors, item_results=item_results, payload=clean
    )


def offer_to_item(program: str, offer: dict[str, Any]) -> dict[str, Any]:
    shop = SHOP_ALIASES.get(program, program.replace("-", " "))
    discount = (offer.get("reward") or offer.get("bonus") or "").strip() or None
    url = (offer.get("link") or "").strip() or None
    code = (offer.get("code") or "").strip() or None
    return {
        "shop": shop,
        "discount": discount,
        "url": url,
        "code": code,
        "description": f"Autofresh canary/import for {program}",
    }


def build_import_payload(
    programs: list[str] | None = None,
    *,
    platform_filter: str = "referralcodes",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build Agent Import JSON from offers + operator overrides + mapping inventory."""
    offers = OffersRepository()
    repo = MappingRepository()
    refs = [r for r in list_mapping_refs() if r.platform == platform_filter]
    if programs:
        want = {p.lower() for p in programs}
        refs = [r for r in refs if r.program.lower() in want]
    # Also allow explicit program list even without mapping
    if programs and not refs:
        refs = []  # type: ignore
        meta_rows = []
        items = []
        for prog in programs:
            try:
                offer = apply_effective_to_offer(
                    offers.get_by_slug(prog), platform=platform_filter
                )
            except Exception as exc:  # noqa: BLE001
                meta_rows.append({"program": prog, "status": "error", "error": str(exc)})
                continue
            item = offer_to_item(prog, offer)
            v = validate_item(item)
            meta_rows.append(
                {
                    "program": prog,
                    "status": "ok" if v.ok else "invalid",
                    "errors": v.errors,
                    "item": v.item,
                    "source": "offers_only",
                }
            )
            if v.ok:
                items.append(v.item)
        return {"version": SCHEMA_VERSION, "items": items}, meta_rows

    meta_rows = []
    items = []
    for ref in refs:
        try:
            m = repo.load(ref.platform, ref.program, ref.language)
            offer = apply_effective_to_offer(
                offers.get_by_slug(ref.program), platform=platform_filter
            )
        except Exception as exc:  # noqa: BLE001
            meta_rows.append(
                {"program": ref.program, "status": "error", "error": str(exc)}
            )
            continue
        item = offer_to_item(ref.program, offer)
        # Prefer platform_values for code/link if offers empty
        pv = m.platform_values or {}
        if not item.get("code") and pv.get("personal_code"):
            item["code"] = pv["personal_code"]
        if not item.get("url") and pv.get("personal_link"):
            item["url"] = pv["personal_link"]
        if not item.get("discount") and pv.get("referee_reward"):
            item["discount"] = pv["referee_reward"]
        v = validate_item(item)
        meta_rows.append(
            {
                "program": ref.program,
                "status": "ok" if v.ok else "invalid",
                "errors": v.errors,
                "item": v.item,
                "announcement_url": m.announcement_url,
                "source": "mapping+offers",
            }
        )
        if v.ok:
            items.append(v.item)
    return {"version": SCHEMA_VERSION, "items": items}, meta_rows


def payload_to_csv(payload: dict[str, Any]) -> str:
    """CSV mirror of JSON fields (header + rows)."""
    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=["shop", "discount", "url", "code", "description"],
        extrasaction="ignore",
    )
    w.writeheader()
    for it in payload.get("items") or []:
        w.writerow(
            {
                "shop": it.get("shop") or "",
                "discount": it.get("discount") or "",
                "url": it.get("url") or "",
                "code": it.get("code") or "",
                "description": it.get("description") or "",
            }
        )
    return buf.getvalue()


def write_artifacts(
    payload: dict[str, Any],
    meta: list[dict[str, Any]],
    *,
    stem: str = "referralcodes-agent-import",
) -> dict[str, str]:
    out_dir = ROOT / "data" / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    validation = validate_payload(payload)
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    report_path = out_dir / f"{stem}-report.json"
    json_path.write_text(
        json.dumps(validation.payload if validation.ok else payload, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    csv_path.write_text(payload_to_csv(payload), encoding="utf-8")
    report = {
        "docs": DOCS_URL,
        "import_ui": IMPORT_UI,
        "schema_version": SCHEMA_VERSION,
        "validation_ok": validation.ok,
        "validation_errors": validation.errors,
        "item_count": len(payload.get("items") or []),
        "valid_items": sum(1 for m in meta if m.get("status") == "ok"),
        "meta": meta,
        "workflow": [
            "1. Review generated JSON/CSV",
            "2. Login with REFERRALCODES_EMAIL/PASSWORD",
            f"3. Open {IMPORT_UI}",
            "4. Paste JSON → Validate (read #agent-import-result)",
            "5. Commit only if validation succeeds",
            "6. Reread public profile → mark WRITE_VERIFIED if post_match",
        ],
        "paths": {"json": str(json_path.name), "csv": str(csv_path.name)},
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "report": str(report_path),
        "validation_ok": validation.ok,
    }
