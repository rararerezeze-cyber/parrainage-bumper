"""Contenu desire Super-Parrain — logique pure (sans navigateur).

Le bumper appelle get_desired_content / compare avant son Enregistrer unique.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template, structure_preserved_via_markers

# URL path fragment → program slug
URL_PROGRAM_HINTS = [
    (r"kraken", "kraken"),
    (r"coinbase", "coinbase"),
    (r"binance", "binance"),
    (r"revolut", "revolut"),
    (r"trade[-_]?republic|traderepublic", "traderepublic"),
    (r"swissborg", "swissborg"),
    (r"bybit", "bybit"),
    (r"bitstack", "bitstack"),
    (r"deblock", "deblock"),
    (r"gemini", "gemini"),
    (r"ledger", "ledger"),
    (r"paypal", "paypal"),
    (r"wise", "wise"),
    (r"airbnb", "airbnb"),
    (r"boursobank|boursorama", "boursobank"),
    (r"betclic", "betclic"),
    (r"unibet", "unibet"),
    (r"winamax", "winamax"),
    (r"igraal", "igraal"),
    (r"poulpeo", "poulpeo"),
    (r"ebuyclub", "ebuyclub"),
    (r"joko", "joko"),
    (r"widilo", "widilo"),
    (r"heetch", "heetch"),
    (r"omio", "omio"),
    (r"finary", "finary"),
    (r"totalenergies", "totalenergies"),
    (r"acheel", "acheel"),
    (r"lolivier|olivier", "lolivier"),
    (r"robinhood", "robinhood"),
    (r"vinted", "vinted"),
    (r"whatnot", "whatnot"),
    (r"\bokx\b", "okx"),
    (r"plum", "plum"),
    (r"nrj", "nrj-mobile"),
    (r"fdj", "fdj-francaise-des-jeux"),
]


def program_from_edit_url(url: str) -> str | None:
    low = (url or "").lower()
    for pat, slug in URL_PROGRAM_HINTS:
        if re.search(pat, low):
            return slug
    return None


@dataclass
class DesiredContent:
    program: str
    language: str = "fr"
    has_mapping: bool = False
    rendered_body: str | None = None
    variables: dict[str, str | None] = field(default_factory=dict)
    mutable_fields: list[str] = field(default_factory=list)
    # Champs discrets souvent presents sur codes-promo
    code: str | None = None
    link: str | None = None
    title: str | None = None
    reward: str | None = None
    conditions: str | None = None
    structure_preserved: bool = True
    error: str | None = None


@dataclass
class ContentDiff:
    program: str
    needs_update: bool
    changed_fields: dict[str, dict[str, str | None]] = field(default_factory=dict)
    desired: DesiredContent | None = None
    reason: str = ""


def get_desired_content(program: str, language: str = "fr") -> DesiredContent:
    """desired = render(template, offers.json) + champs derives du mapping."""
    out = DesiredContent(program=program, language=language)
    try:
        mapping = MappingRepository().load("super-parrain", program, language)
    except FileNotFoundError:
        out.error = "no_mapping"
        return out
    except Exception as exc:  # noqa: BLE001
        out.error = str(exc)
        return out

    out.has_mapping = True
    out.mutable_fields = list(mapping.mutable_fields)
    try:
        templates = TemplateRepository()
        renderer = Renderer(OffersRepository())
        template = templates.load_text("super-parrain", program, language)
        golden = templates.load_golden("super-parrain", program, language)
        offer = renderer.offers.get_by_slug(program)
        variables = renderer.build_variables(mapping, offer=offer)
        rendered = renderer.render(template, mapping, offer=offer)
        out.variables = variables
        out.rendered_body = rendered
        out.code = variables.get("personal_code")
        out.link = variables.get("personal_link")
        out.reward = variables.get("referee_reward")
        out.conditions = variables.get("conditions")

        # Titre derive si mapping le declare explicitement
        title_tpl = getattr(mapping, "title_template", None)
        # also from raw json via markers convention
        # Prefer offer name + reward if title marker present in mapping notes/meta
        markers = mapping.markers or {}
        # If template first line looks like a title with markers, use first line of rendered
        first = (rendered.split("\n", 1)[0] or "").strip()
        if first and ("€" in first or "offert" in first.lower() or "kraken" in first.lower() or program in first.lower()):
            # short title for codes-promo column (strip heavy emoji spam carefully)
            out.title = first[:120]

        # Structure check vs golden
        hist = dict(mapping.platform_values or {})
        extracted = extract_values_via_template(
            template, golden, mapping.mutable_fields, mapping.markers
        )
        for k, v in extracted.items():
            hist.setdefault(k, v)
        out.structure_preserved = structure_preserved_via_markers(
            template,
            golden,
            rendered,
            mapping.mutable_fields,
            mapping.markers,
            hist,
            variables,
        )
    except KeyError:
        out.error = "program_not_in_offers"
    except Exception as exc:  # noqa: BLE001
        out.error = str(exc)
    return out


def compare_current_to_desired(
    program: str,
    current: dict[str, str | None],
    desired: DesiredContent | None = None,
) -> ContentDiff:
    """Compare valeurs lues sur le formulaire (ou platform_values) vs desired."""
    desired = desired or get_desired_content(program)
    if desired.error or not desired.has_mapping:
        return ContentDiff(
            program=program,
            needs_update=False,
            desired=desired,
            reason=desired.error or "no_mapping",
        )
    if not desired.structure_preserved:
        return ContentDiff(
            program=program,
            needs_update=False,
            desired=desired,
            reason="structure_not_preserved",
        )

    changed: dict[str, dict[str, str | None]] = {}
    pairs = [
        ("personal_code", "code", desired.code),
        ("personal_link", "link", desired.link),
        ("referee_reward", "reward", desired.reward),
        ("title", "title", desired.title),
        ("conditions", "conditions", desired.conditions),
        ("body", "body", desired.rendered_body),
    ]
    for logical, cur_key, new_val in pairs:
        if new_val is None:
            continue
        # only for mutable body fields in mapping, or discrete form fields
        if logical in ("personal_code", "personal_link", "referee_reward", "conditions"):
            if logical not in desired.mutable_fields and logical != "title":
                # title may still update if derived
                if logical != "title":
                    continue
        old_val = current.get(cur_key)
        if old_val is None:
            old_val = current.get(logical)
        if old_val is None:
            continue
        # normalize whitespace for compare
        o = " ".join(str(old_val).split())
        n = " ".join(str(new_val).split())
        if o != n and n:
            # For body: only if substantially different and old looks like our announcement
            if logical == "body" and len(o) < 40:
                continue
            changed[logical] = {"old": old_val, "new": new_val}

    # Also compare against mapping platform_values if form empty
    if not changed and desired.variables:
        for field_name in desired.mutable_fields:
            # no current form value — still signal update needed via platform_values later
            pass

    return ContentDiff(
        program=program,
        needs_update=bool(changed),
        changed_fields=changed,
        desired=desired,
        reason="diff" if changed else "in_sync",
    )


def compare_from_mapping_platform_values(program: str, language: str = "fr") -> ContentDiff:
    """Diff sans formulaire: platform_values (dernier connu) vs offers.json."""
    desired = get_desired_content(program, language)
    if not desired.has_mapping or desired.error:
        return ContentDiff(program=program, needs_update=False, desired=desired, reason=desired.error or "no_mapping")
    try:
        mapping = MappingRepository().load("super-parrain", program, language)
    except Exception as exc:  # noqa: BLE001
        return ContentDiff(program=program, needs_update=False, desired=desired, reason=str(exc))

    current = {
        "code": (mapping.platform_values or {}).get("personal_code"),
        "link": (mapping.platform_values or {}).get("personal_link"),
        "reward": (mapping.platform_values or {}).get("referee_reward"),
        "body": None,
    }
    # Use golden as body current
    try:
        current["body"] = TemplateRepository().load_golden("super-parrain", program, language)
    except Exception:
        pass
    return compare_current_to_desired(program, current, desired)
