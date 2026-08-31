"""French-language Telegram UX layer for Autofresh: menu, aide, exemples,
plateformes, and friendly clarification for ambiguous field words.

This module never touches OperatorOverrideStore and never triggers a writer
-- everything here is pure text generation from already-computed, read-only
backend state (lib.write_status.summary(), ALL_PLATFORMS). The platform
capability table is built from real status data every call, not a frozen
copy, so it cannot silently go stale as writers evolve.

Global meta-commands (no program token) recognized here: "Autofresh",
"Autofresh aide", "Autofresh commandes", "Aide Autofresh", "Autofresh
exemples", "Autofresh plateformes", "Autofresh bump" (bump_autres.yml
last/next run + recent failures, live from the GitHub Actions API -- the
one meta-command here that is NOT purely local/offline). Per-program verbs
("Kraken statut", "Kraken divergences", "Kraken plateformes") are
recognized in tools/telegram_update.py's parse_message() and reuse this
module only for their French status-label translation.
"""
from __future__ import annotations

import unicodedata

from lib.paths import MAPPINGS_DIR
from lib.write_status import ALL_PLATFORMS, STATUS_WRITE_VERIFIED, summary as write_summary

TOPIC_MENU = "menu"
TOPIC_EXEMPLES = "exemples"
TOPIC_PLATEFORMES = "plateformes"
TOPIC_BUMP = "bump"


def _fold(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace -- for meta-command
    matching only. Deliberately not used for FIELD_ALIASES lookups (those
    already carry explicit accented/unaccented entries and must not change
    behavior here).
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


_GLOBAL_META: dict[str, str] = {
    "autofresh": TOPIC_MENU,
    "autofresh aide": TOPIC_MENU,
    "aide autofresh": TOPIC_MENU,
    "autofresh commandes": TOPIC_MENU,
    "autofresh menu": TOPIC_MENU,
    "autofresh help": TOPIC_MENU,
    "autofresh exemples": TOPIC_EXEMPLES,
    "autofresh exemple": TOPIC_EXEMPLES,
    "autofresh plateformes": TOPIC_PLATEFORMES,
    "autofresh plateforme": TOPIC_PLATEFORMES,
    "autofresh bump": TOPIC_BUMP,
    "autofresh bumps": TOPIC_BUMP,
    "bump statut": TOPIC_BUMP,
    "etat bump": TOPIC_BUMP,
    "etat des bumps": TOPIC_BUMP,
}


def detect_meta_command(raw: str) -> str | None:
    """Return TOPIC_MENU / TOPIC_EXEMPLES / TOPIC_PLATEFORMES, or None.

    Only matches whole-message global commands (no program token) -- never
    intercepts a real "<program> ..." message, so it can never shadow an
    existing verb or field.
    """
    return _GLOBAL_META.get(_fold(raw))


# Bare, genuinely ambiguous words -- NOT already in
# lib.operator_overrides.FIELD_ALIASES -- that could mean either reward
# side. Adding "reward" here would be a behavior change (it is already a
# resolved alias for referee_reward); this only covers words that today
# hit the technical "unknown_field" error path.
_AMBIGUOUS_FIELD_WORDS = {"recompense", "bonus", "gain"}


def ambiguous_field_reply(word: str) -> str | None:
    """French clarifying question for a bare ambiguous reward word, or None
    if *word* is not one of the known-ambiguous bare terms.
    """
    if _fold(word) not in _AMBIGUOUS_FIELD_WORDS:
        return None
    return (
        "Tu veux consulter ou modifier quelle récompense ?\n"
        "• Gain filleul\n"
        "• Gain parrain\n"
        "Exemple : Kraken gain filleul 200 €"
    )


_STATUS_FR = {
    "UNPREPARED": "non préparé",
    "WRITE_PREPARED": "préparé (écriture jamais testée)",
    "CANARY_READY": "test d'écriture en cours (canary)",
    "WRITE_VERIFIED": "écriture vérifiée ✅",
    "AUTH_BLOCKED_GOOGLE": "bloqué (authentification Google)",
    "AUTH_BLOCKED_MANUAL": "bloqué (authentification manuelle requise)",
    "MANUAL_ONLY": "manuel uniquement",
    "CANARY_FAILED": "échec du dernier test",
}

_ROUTE_FR = {
    "AUTO_ON_SAFE_DIFF": "auto si différence sûre détectée",
    "HUMAN_SAVE_REQUIRED": "sauvegarde manuelle requise",
    "NEVER_AUTO_COMMIT": "jamais automatique",
    "AUTH_BLOCKED_MANUAL": "bloqué (authentification)",
    "CANARY_PENDING_SKIP": "en attente de validation",
    "COOKIE_SESSION_NOT_PC_OFF": "session cookie non automatisable",
    "BUMPER_NOT_AUTHORIZED": "relance historique non autorisée",
    "FUSED_UPDATE_BUMP": "relance historique autorisée",
}

_PLATFORM_LABEL_FR = {
    "super-parrain": "Super-Parrain",
    "parrainage-co": "Parrainage.co",
    "code-parrainage": "Code-Parrainage",
    "1parrainage": "1Parrainage",
    "referralcodes": "ReferralCodes",
    "referralcode-tv": "ReferralCode.tv",
    "referraldrop": "ReferralDrop",
}


def platform_label_fr(platform_id: str) -> str:
    return _PLATFORM_LABEL_FR.get(platform_id, platform_id)


def status_label_fr(status: str) -> str:
    return _STATUS_FR.get(status, status)


def route_label_fr(route: str) -> str:
    return _ROUTE_FR.get(route, route)


def _is_mapped_for_program(platform_id: str, program: str) -> bool:
    """A real curated mapping file exists for (platform, program), any
    language suffix (super-parrain/1parrainage/... use .fr., referralcodes/
    referralcode-tv/referraldrop use .en.).
    """
    try:
        return any(MAPPINGS_DIR.glob(f"{platform_id}.{program}.*.json"))
    except Exception:
        return False


def build_platforms_status(*, program: str | None = None) -> str:
    """Real per-platform capability table, translated to French.

    Pulls live from lib.write_status.summary() (+ a real mapping-file check
    when *program* is given) every call -- never a cached or hardcoded
    copy, so it reflects whatever writers/mappings actually exist today.

    Deliberately keeps three distinct concepts separate rather than
    collapsing them into one ambiguous count:
      - "connue"  : one of the 7 platforms Autofresh knows about at all.
      - "mappée"  : (program-specific only) a curated mapping file exists
        for THIS program on that platform -- says nothing about whether a
        write has ever been verified there.
      - écriture  : the real write-readiness ladder (UNPREPARED ...
        WRITE_VERIFIED / AUTH_BLOCKED...), independent of "mappée".
    """
    data = write_summary()
    rows = {r["platform"]: r for r in data.get("platforms") or []}
    header = "🎯 AUTOFRESH — PLATEFORMES" + (f" ({program.capitalize()})" if program else "")
    lines = [header, f"{len(ALL_PLATFORMS)} plateformes connues"]

    if program:
        mapped_flags = {pid: _is_mapped_for_program(pid, program) for pid in ALL_PLATFORMS}
        mapped_count = sum(1 for v in mapped_flags.values() if v)
        unmapped = [pid for pid, v in mapped_flags.items() if not v]
        lines.append(f"{mapped_count} {'mappée' if mapped_count == 1 else 'mappées'} pour {program.capitalize()}")
        if unmapped:
            details = ", ".join(
                f"{platform_label_fr(pid)} — {status_label_fr(rows.get(pid, {}).get('status') or 'UNPREPARED')}"
                for pid in unmapped
            )
            noun = "non mappée" if len(unmapped) == 1 else "non mappées"
            lines.append(f"{len(unmapped)} {noun} : {details}")
        lines.append("")
        for pid in ALL_PLATFORMS:
            row = rows.get(pid) or {}
            status = row.get("status") or "UNPREPARED"
            mapped_label = "mappée" if mapped_flags[pid] else "non mappée"
            lines.append(
                f"• {platform_label_fr(pid)} — {mapped_label} · {status_label_fr(status)}"
            )
    else:
        lines.append(f"Écriture vérifiée : {data.get('WRITE_VERIFIED')}")
        lines.append("")
        for pid in ALL_PLATFORMS:
            row = rows.get(pid) or {}
            status = row.get("status") or "UNPREPARED"
            route = row.get("route") or ""
            lines.append(
                f"• {platform_label_fr(pid)} — {status_label_fr(status)}"
                + (f" · {route_label_fr(route)}" if route else "")
            )

    lines.append("")
    lines.append(
        "🔴 Aucune commande Telegram ne déclenche une écriture live instantanée "
        "aujourd'hui : toute écriture réelle passe par le pipeline planifié/vérifié "
        "(jamais en synchrone depuis Telegram — run_writers=false par défaut)."
    )
    return "\n".join(lines)


def _gain_parrain_caveat() -> str:
    # Real audit finding (2026-08-16): across all 7 platforms' captured
    # published content, no program has ever shown a distinct
    # referrer-side reward value -- every observed listing pays a single
    # shared bonus, or none is captured separately at all. The override is
    # genuinely storable (Telegram/status/precedence all work), but no
    # writer today has a mutable_field to carry it onto a live listing.
    # See PILOTABLE_FIELDS / DEFAULT_OFFER_FIELDS in lib/operator_overrides.py
    # and lib/template_builder.py for the scaffold this refers to.
    return "enregistrer un override gain parrain ; écriture plateforme non prise en charge actuellement"


def build_main_menu() -> str:
    gain_parrain_note = _gain_parrain_caveat()
    return (
        "🤖 AUTOFRESH\n"
        "\n"
        "📊 CONSULTATION  🟢 lecture seule\n"
        "• <Programme> statut\n"
        "• <Programme> overrides\n"
        "• <Programme> divergences\n"
        "• <Programme> plateformes\n"
        "\n"
        "✏️ MODIFICATIONS  🟠 enregistre un override\n"
        "• <Programme> code <code>\n"
        "• <Programme> lien <url>\n"
        "• <Programme> gain filleul <valeur>\n"
        f"• <Programme> gain parrain <valeur> — {gain_parrain_note}\n"
        "• <Programme> conditions <texte>\n"
        "\n"
        "🎯 PAR PLATEFORME  🟠\n"
        "• <Programme> Super-Parrain statut\n"
        "• <Programme> Super-Parrain gain filleul <valeur>\n"
        "• <Programme> 1Parrainage statut\n"
        "  (idem pour Parrainage.co / Code-Parrainage / ReferralCode.tv / "
        "ReferralCodes / ReferralDrop)\n"
        "\n"
        "🧹 OVERRIDES  🟠\n"
        "• <Programme> supprimer code\n"
        "• <Programme> supprimer lien\n"
        "• <Programme> supprimer gain filleul\n"
        "• <Programme> supprimer conditions\n"
        "\n"
        "ℹ️ AIDE\n"
        "• Autofresh — ce menu\n"
        "• Autofresh exemples — quelques exemples concrets\n"
        "• Autofresh plateformes — état réel des 7 plateformes\n"
        "• Autofresh bump — statut du bump Code-Parrainage/Parrainage.co\n"
        "\n"
        "Variantes acceptées : statut/status, gain filleul/récompense filleul, "
        "gain parrain/récompense parrain, lien/link, supprimer/retirer/effacer.\n"
        "\n"
        "🔴 Aucune commande ne déclenche une écriture live instantanée aujourd'hui "
        "— chaque écriture réelle passe par le pipeline planifié/vérifié."
    )


def build_examples() -> str:
    # French first everywhere ("statut", never "status") -- English verbs
    # stay accepted as aliases in the parser, but visible documentation is
    # French-only.
    return (
        "🤖 AUTOFRESH — EXEMPLES\n"
        "\n"
        "🟢 Kraken statut\n"
        "🟢 Kraken overrides\n"
        "🟢 Kraken divergences\n"
        "🟢 Autofresh plateformes\n"
        "🟠 Kraken gain filleul 200 €\n"
        "🟠 Kraken lien https://invite.kraken.com/XXXX\n"
        "🟠 Kraken Super-Parrain gain filleul 25 €\n"
        "🟠 Kraken supprimer override gain filleul\n"
    )


def build_bump_status() -> str:
    """Live 'Autofresh bump' Slack/Telegram reply: last run, next expected
    run, and any recent failures for bump_autres.yml (Code-Parrainage /
    Parrainage.co). Reads the real GitHub Actions run history via
    lib.bump_watch -- never a cached or committed snapshot, so it cannot
    silently go stale (2026-08-31 finding: a stale/missing status here is
    exactly what let two missed scheduled runs go unnoticed)."""
    import os
    from datetime import datetime, timezone

    from lib.bump_watch import fetch_recent_runs, format_status_fr, summarize_status

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return (
            "Bump Code-Parrainage / Parrainage.co : statut indisponible "
            "(GITHUB_TOKEN absent dans cet environnement)."
        )
    try:
        runs = fetch_recent_runs(token)
    except Exception as exc:  # noqa: BLE001
        return f"Bump Code-Parrainage / Parrainage.co : statut indisponible (erreur GitHub API : {exc})."
    status = summarize_status(runs, now=datetime.now(timezone.utc))
    return format_status_fr(status, now=datetime.now(timezone.utc))


def build_topic(topic: str, *, program: str | None = None) -> str:
    if topic == TOPIC_EXEMPLES:
        return build_examples()
    if topic == TOPIC_PLATEFORMES:
        return build_platforms_status(program=program)
    if topic == TOPIC_BUMP:
        return build_bump_status()
    return build_main_menu()


def is_write_verified_platform(platform_id: str) -> bool:
    data = write_summary()
    rows = {r["platform"]: r for r in data.get("platforms") or []}
    return (rows.get(platform_id) or {}).get("status") == STATUS_WRITE_VERIFIED
