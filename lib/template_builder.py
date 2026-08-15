"""Construit golden/template/mapping a partir d'un texte d'annonce + offer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.paths import MAPPINGS_DIR, TEMPLATES_DIR, mapping_path, template_path, golden_path

DEFAULT_MARKERS = {
    "personal_code": "{{PERSONAL_CODE}}",
    "personal_link": "{{PERSONAL_LINK}}",
    "referee_reward": "{{REFEREE_REWARD}}",
    "referrer_reward": "{{REFERRER_REWARD}}",
    "conditions": "{{CONDITIONS}}",
}

DEFAULT_OFFER_FIELDS = {
    "personal_code": "code",
    "personal_link": "link",
    "referee_reward": "reward",
    "referrer_reward": "referrer_reward",
    "conditions": "cond",
}

CODE_PATTERNS = [
    re.compile(r"(?i)code\s*parrain\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._\-!]{2,40})"),
    re.compile(r"(?i)code\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._\-!]{2,40})"),
    re.compile(r"(?i)referral\s*code\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._\-!]{2,40})"),
]

LINK_PATTERNS = [
    re.compile(r"https?://[^\s<>\"']+"),
]

REWARD_LINE_PATTERNS = [
    re.compile(r"(?im)^.*bonus\s*[:：]\s*(.+?)(?:\s*[⭐⚡✨🔥]|$)", re.M),
    re.compile(r"(?im)^.*(?:offre|parrainage).{0,40}?(\d[\d\s]*[€$]\s*[^\n]{0,40})", re.M),
]


@dataclass
class BuildResult:
    platform: str
    program: str
    language: str
    golden: str
    template: str
    mutable_fields: list[str]
    platform_values: dict[str, str]
    confidences: dict[str, str] = field(default_factory=dict)
    sync_mode: str = "REVIEW"
    template_status: str = "ready"
    notes: list[str] = field(default_factory=list)
    announcement_url: str | None = None


def _count_non_overlapping(text: str, needle: str) -> int:
    if not needle:
        return 0
    return text.count(needle)


def _replace_once_safe(text: str, old: str, new: str) -> str:
    """Remplace toutes les occurrences exactes (valeur unique attendue)."""
    return text.replace(old, new)


def detect_platform_values(text: str, offer: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Detecte code/lien/bonus dans le texte.

    confidences: high | medium | low
    """
    values: dict[str, str] = {}
    conf: dict[str, str] = {}
    notes: list[str] = []
    offer = offer or {}

    # --- CODE ---
    code = offer.get("code")
    code_s = str(code).strip() if code else ""
    if code_s and _count_non_overlapping(text, code_s) >= 1:
        values["personal_code"] = code_s
        conf["personal_code"] = "high"
    else:
        for pat in CODE_PATTERNS:
            m = pat.search(text)
            if m:
                values["personal_code"] = m.group(1).strip()
                conf["personal_code"] = "high" if not code_s else "medium"
                if code_s and code_s != values["personal_code"]:
                    notes.append(
                        f"code plateforme {values['personal_code']!r} != offers.json {code_s!r}"
                    )
                break

    # --- LINK ---
    link = offer.get("link")
    link_s = str(link).strip() if link else ""
    if link_s and _count_non_overlapping(text, link_s) >= 1:
        values["personal_link"] = link_s
        conf["personal_link"] = "high"
    else:
        # Prefer first non-discord non-platform meta link after "Lien"
        candidates = LINK_PATTERNS[0].findall(text)
        preferred = []
        for c in candidates:
            c = c.rstrip(").,;]")
            low = c.lower()
            if any(x in low for x in ("discord.gg", "super-parrain.com", "facebook.com", "twitter.com")):
                continue
            preferred.append(c)
        if preferred:
            # Prefer link near "Lien" label if possible
            chosen = preferred[0]
            for c in preferred:
                idx = text.find(c)
                window = text[max(0, idx - 40) : idx].lower()
                if "lien" in window or "link" in window or "invite" in window:
                    chosen = c
                    break
            values["personal_link"] = chosen
            conf["personal_link"] = "high" if not link_s else "medium"
            if link_s and link_s != chosen:
                notes.append(f"lien plateforme != offers.json")
        elif link_s:
            notes.append("lien offers.json non trouve dans le texte")

    # --- REWARD ---
    reward = offer.get("reward")
    reward_s = str(reward).strip() if reward else ""
    if reward_s and len(reward_s) >= 3 and _count_non_overlapping(text, reward_s) == 1:
        values["referee_reward"] = reward_s
        conf["referee_reward"] = "high"
    else:
        # Extract "Bonus : XXX" phrase
        for pat in REWARD_LINE_PATTERNS:
            m = pat.search(text)
            if m:
                phrase = m.group(1).strip() if m.lastindex else m.group(0).strip()
                phrase = phrase.strip(" ⭐⚡✨🔥•-")
                # Avoid capturing whole paragraphs
                if 2 <= len(phrase) <= 80 and "\n" not in phrase:
                    values["referee_reward"] = phrase
                    conf["referee_reward"] = "medium"
                    notes.append("bonus extrait du texte plateforme (confiance moyenne)")
                    break
        if "referee_reward" not in values:
            notes.append("bonus non marque (ambigu / non trouve) — texte laisse fixe")

    return values, conf, notes


def build_from_text(
    *,
    platform: str,
    program: str,
    language: str,
    golden_text: str,
    offer: dict[str, Any] | None,
    announcement_url: str | None = None,
    force_values: dict[str, str] | None = None,
) -> BuildResult:
    golden = golden_text.replace("\r\n", "\n").replace("\r", "\n")
    values, conf, notes = detect_platform_values(golden, offer)
    if force_values:
        values.update(force_values)
        for k in force_values:
            conf[k] = "high"

    mutable: list[str] = []
    template = golden
    # Replace longest values first to avoid partial collisions
    ordered = sorted(values.items(), key=lambda kv: len(kv[1]), reverse=True)
    for field_name, value in ordered:
        if conf.get(field_name) == "low":
            notes.append(f"{field_name}: confiance faible — non marque")
            continue
        if not value or value not in template:
            continue
        # Skip if value appears too many times and is short (risk of over-replace)
        count = template.count(value)
        if count > 3 and len(value) < 8:
            notes.append(f"{field_name}: {count} occurrences courtes — laisse fixe")
            conf[field_name] = "low"
            continue
        marker = DEFAULT_MARKERS[field_name]
        template = _replace_once_safe(template, value, marker)
        mutable.append(field_name)

    # Restore canonical mutable order
    order = ["personal_code", "personal_link", "referee_reward", "referrer_reward", "conditions"]
    mutable = [f for f in order if f in mutable]

    # Round-trip check with platform values
    roundtrip = template
    for field_name in mutable:
        roundtrip = roundtrip.replace(DEFAULT_MARKERS[field_name], values[field_name])
    if roundtrip != golden:
        raise ValueError(
            f"Round-trip template failed for {platform}/{program}.{language}"
        )

    sync_mode = "REVIEW"
    if mutable and all(conf.get(f) == "high" for f in mutable):
        sync_mode = "SAFE_AUTO" if len(mutable) >= 2 else "REVIEW"

    return BuildResult(
        platform=platform,
        program=program,
        language=language,
        golden=golden,
        template=template,
        mutable_fields=mutable,
        platform_values={k: values[k] for k in mutable},
        confidences={k: conf.get(k, "medium") for k in mutable},
        sync_mode=sync_mode,
        template_status="ready" if mutable or golden else "missing_source",
        notes=notes,
        announcement_url=announcement_url,
    )


def write_build_result(result: BuildResult) -> dict[str, Path]:
    tpl_dir = TEMPLATES_DIR / result.platform
    tpl_dir.mkdir(parents=True, exist_ok=True)
    MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)

    g_path = golden_path(result.platform, result.program, result.language)
    t_path = template_path(result.platform, result.program, result.language)
    m_path = mapping_path(result.platform, result.program, result.language)
    meta_path = tpl_dir / f"{result.program}.{result.language}.meta.json"

    g_path.write_bytes(result.golden.encode("utf-8"))
    t_path.write_bytes(result.template.encode("utf-8"))

    markers = {k: DEFAULT_MARKERS[k] for k in DEFAULT_MARKERS}
    offer_fields = {k: DEFAULT_OFFER_FIELDS[k] for k in DEFAULT_OFFER_FIELDS if k in result.mutable_fields or k in DEFAULT_OFFER_FIELDS}
    # Keep full marker set for future use; offer_fields for mutable + common
    offer_fields = {
        "personal_code": "code",
        "personal_link": "link",
        "referee_reward": "reward",
        "conditions": "cond",
    }

    mapping = {
        "platform": result.platform,
        "program": result.program,
        "language": result.language,
        "sync_mode": result.sync_mode,
        "template_status": result.template_status,
        "golden_file": f"{result.program}.{result.language}.golden.txt",
        "mutable_fields": result.mutable_fields,
        "markers": markers,
        "offer_fields": offer_fields,
        "announcement_url": result.announcement_url,
        "edit_url": None,
        "platform_values": result.platform_values,
        "confidences": result.confidences,
        "notes": "; ".join(result.notes) if result.notes else None,
        "source_of_truth": {
            "personal_code": "offers_json_then_operator",
            "personal_link": "offers_json_then_operator",
            "referee_reward": "offers_json_then_official",
            "announcement_text": "platform_target_only",
        },
    }
    m_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta = {
        "platform": result.platform,
        "program": result.program,
        "language": result.language,
        "status": result.template_status,
        "role": "platform_target_historical_text_only",
        "not_business_source_of_truth": True,
        "announcement_url": result.announcement_url,
        "mutable_fields": result.mutable_fields,
        "platform_values": result.platform_values,
        "confidences": result.confidences,
        "notes": result.notes,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"golden": g_path, "template": t_path, "mapping": m_path, "meta": meta_path}


MUTABLE_FIELD_ORDER = ["personal_code", "personal_link", "referee_reward", "referrer_reward", "conditions"]

# Once a real value exists for one of these, a fresh read-only capture may
# never silently replace it -- only fill it in if it was previously empty.
# Real incident: capture_oneparrainage() called write_build_result() (via
# this same module) unconditionally on every run, which always emits a
# fixed schema with edit_url=None and no memory of anything previously
# learned -- destroying manually-verified evidence for all 31 1parrainage
# programs it touched in one pass (2026-08-15, commit 83f22bca), including
# the real WRITE_VERIFIED edit_url for kraken.
MAPPING_PROTECT_ONCE_SET_FIELDS = (
    "edit_url",
    "platform_offer_id",
    "occurrences",
    "occurrence_count",
    "edit_url_source",
    "edit_url_learned_at",
    "sync_mode",
    "quality",
    "announcement_url",
)
MAPPING_NESTED_MERGE_FIELDS = ("platform_values", "confidences")


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _divergence(field: str, existing_value: Any, fresh_value: Any) -> dict[str, Any]:
    return {"field": field, "existing": existing_value, "fresh": fresh_value}


def _all_divergences(existing: dict[str, Any], fresh: dict[str, Any]) -> list[dict[str, Any]]:
    """Every field (including nested platform_values/confidences sub-keys)
    where `existing` already has a real value and `fresh` proposes a
    genuinely different one. Used both by the WRITE_VERIFIED freeze branch
    and the normal merge branch so a real site change is never invisible,
    even on a frozen record -- it just never gets auto-applied.
    """
    out: list[dict[str, Any]] = []
    for nested_key in MAPPING_NESTED_MERGE_FIELDS:
        old_nested = existing.get(nested_key) or {}
        new_nested = fresh.get(nested_key) or {}
        for k, v in new_nested.items():
            old_v = old_nested.get(k)
            if _present(old_v) and _present(v) and old_v != v:
                out.append(_divergence(f"{nested_key}.{k}", old_v, v))
    for key in MAPPING_PROTECT_ONCE_SET_FIELDS:
        old_v = existing.get(key)
        new_v = fresh.get(key)
        if _present(old_v) and _present(new_v) and old_v != new_v:
            out.append(_divergence(key, old_v, new_v))
    old_notes = existing.get("notes")
    new_notes = fresh.get("notes")
    if _present(old_notes) and _present(new_notes) and old_notes != new_notes:
        out.append(_divergence("notes", old_notes, new_notes))
    return out


def merge_conservative_mapping_update(
    existing: dict[str, Any] | None, fresh: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge a freshly-derived mapping onto an existing one without ever
    downgrading already-curated evidence, and without ever silently
    discarding a genuine new observation either.

    Five explicit stages (A-E), matching how a real site change is
    expected to reach the curated mapping:
      A. `existing` -- the curated, protected mapping (this function's
         first argument). Stays the active mapping this call returns.
      B. `fresh` -- a new read-only observation (this function's second
         argument). Never written to the curated mapping by this function
         alone.
      C. Divergence detection -- see `_all_divergences()`: any protected
         field where `existing` already has a value and `fresh` proposes a
         genuinely different one is reported in `report["divergences"]`,
         never silently dropped. The caller (capture_oneparrainage()) is
         expected to persist these via lib.mapping_candidates so they stay
         visible across runs, independent of the curated mapping file.
      D. Validation -- a human/operator reviewing
         lib.mapping_candidates.list_pending_candidates(), not performed by
         this function.
      E. Promotion -- lib.mapping_candidates.promote_candidate() writes the
         candidate's value into the curated mapping deliberately. This
         function is never called as part of that path; promotion edits
         the mapping file directly, on purpose, outside any capture run.

    Merge rules, in order:
    1. If `existing.notes` already contains a "WRITE_VERIFIED" marker, the
       entire record is a real, proven write -- return it completely
       unchanged (stage A wins outright). Divergences (stage C) are still
       computed and reported, just never auto-applied.
    2. `platform_values` / `confidences` merge key by key: an already-
       present sub-value wins; a fresh sub-value is only adopted for a key
       that was previously missing (enrichment, never replacement).
       `mutable_fields` is then recomputed from the merged platform_values
       so it never disagrees with what was actually kept.
    3. `MAPPING_PROTECT_ONCE_SET_FIELDS` (edit_url, platform_offer_id,
       occurrences, occurrence_count, edit_url_source, edit_url_learned_at,
       sync_mode, quality, announcement_url): keep the existing value
       whenever one is already present; only adopt the fresh value if the
       field was previously empty.
    4. `notes`: never silently replace existing non-empty notes.
    5. Everything else (platform/program/language identity, golden_file,
       template_status, markers, offer_fields, source_of_truth) is safe to
       refresh normally -- these are either identity fields or fixed
       boilerplate, never curated evidence.

    Returns (merged, report). report has:
      "enriched": fields filled in that were previously missing.
      "kept_existing": fields where a fresh value was proposed but the
        existing one was preserved instead (field names only, for quick
        logging -- may include fields where fresh had nothing new to say).
      "divergences": [{"field", "existing", "fresh"}, ...] -- the subset of
        kept_existing fields where fresh's value is a real, different,
        non-empty observation. This is what stage C candidate tracking
        should persist; never empty when a genuine site change was seen,
        regardless of whether the record is WRITE_VERIFIED-frozen.
    """
    report: dict[str, Any] = {"enriched": [], "kept_existing": [], "divergences": []}
    if not existing:
        report["enriched"] = sorted(k for k, v in fresh.items() if _present(v))
        return dict(fresh), report

    if "WRITE_VERIFIED" in str(existing.get("notes") or ""):
        report["kept_existing"] = ["*ALL* (WRITE_VERIFIED record frozen)"]
        report["divergences"] = _all_divergences(existing, fresh)
        return dict(existing), report

    report["divergences"] = _all_divergences(existing, fresh)
    merged = dict(existing)

    for nested_key in MAPPING_NESTED_MERGE_FIELDS:
        old_nested = dict(existing.get(nested_key) or {})
        new_nested = dict(fresh.get(nested_key) or {})
        merged_nested = dict(old_nested)
        for k, v in new_nested.items():
            if _present(old_nested.get(k)):
                if old_nested.get(k) != v:
                    report["kept_existing"].append(f"{nested_key}.{k}")
            elif _present(v):
                merged_nested[k] = v
                report["enriched"].append(f"{nested_key}.{k}")
        merged[nested_key] = merged_nested

    merged["mutable_fields"] = [
        f for f in MUTABLE_FIELD_ORDER if f in merged.get("platform_values") or {}
    ]

    for key in MAPPING_PROTECT_ONCE_SET_FIELDS:
        old_v = existing.get(key)
        new_v = fresh.get(key)
        if _present(old_v):
            merged[key] = old_v
            if new_v != old_v:
                report["kept_existing"].append(key)
        elif _present(new_v):
            merged[key] = new_v
            report["enriched"].append(key)

    if _present(existing.get("notes")):
        merged["notes"] = existing.get("notes")
        if fresh.get("notes") and fresh.get("notes") != existing.get("notes"):
            report["kept_existing"].append("notes")
    elif fresh.get("notes"):
        merged["notes"] = fresh.get("notes")
        report["enriched"].append("notes")

    handled = set(MAPPING_NESTED_MERGE_FIELDS) | set(MAPPING_PROTECT_ONCE_SET_FIELDS) | {
        "notes",
        "mutable_fields",
    }
    for key, new_v in fresh.items():
        if key in handled:
            continue
        merged[key] = new_v

    return merged, report


def extract_values_via_template(
    template: str,
    golden: str,
    mutable_fields: list[str],
    markers: dict[str, str],
) -> dict[str, str]:
    """Recupere les valeurs historiques en comparant template et golden (marqueurs uniques)."""
    values: dict[str, str] = {}
    # Strategy: for each marker, find left/right context in template and extract middle from golden
    for field_name in mutable_fields:
        marker = markers.get(field_name)
        if not marker or marker not in template:
            continue
        parts = template.split(marker)
        if len(parts) != 2:
            # multi-occurrence: skip reverse extract
            continue
        left, right = parts
        # Find left suffix and right prefix as anchors
        left_anchor = left[-40:] if len(left) > 40 else left
        right_anchor = right[:40] if len(right) > 40 else right
        start = 0
        if left_anchor:
            idx = golden.find(left_anchor)
            if idx < 0:
                continue
            start = idx + len(left_anchor)
        end = len(golden)
        if right_anchor:
            idx = golden.find(right_anchor, start)
            if idx < 0:
                continue
            end = idx
        values[field_name] = golden[start:end]
    return values


def structure_preserved_via_markers(
    template: str,
    historical: str,
    rendered: str,
    mutable_fields: list[str],
    markers: dict[str, str],
    hist_vals: dict[str, str | None],
    new_vals: dict[str, str | None],
) -> bool:
    """True iff markers sit on historical spans and render only substitutes those spans.

    Safe for repeated identical tokens (\"200 €\") and for short tokens (\"30\")
    that must not be globally replaced inside \"300€\".
    """
    old_fill = template
    new_fill = template
    for field_name in mutable_fields:
        marker = markers.get(field_name)
        if not marker:
            return False
        old = hist_vals.get(field_name)
        new = new_vals.get(field_name)
        if old is None or new is None:
            return False
        if marker not in old_fill:
            return False
        old_fill = old_fill.replace(marker, str(old))
        new_fill = new_fill.replace(marker, str(new))
    return old_fill == historical and new_fill == rendered
