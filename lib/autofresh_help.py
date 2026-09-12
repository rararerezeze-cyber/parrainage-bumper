"""French-language operator UX layer for AutoFresh: menu, aide, exemples,
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
    "UNPREPARED": "écriture non préparée",
    "WRITE_PREPARED": "écriture préparée mais non validée",
    "CANARY_READY": "écriture automatique non validée",
    "WRITE_VERIFIED": "écriture testée et vérifiée",
    "AUTH_BLOCKED_GOOGLE": "authentification Google requise",
    "AUTH_BLOCKED_MANUAL": "authentification manuelle requise",
    "MANUAL_ONLY": "mise à jour manuelle uniquement",
    "CANARY_FAILED": "dernier test d'écriture en échec",
}

_ROUTE_FR = {
    "AUTO_ON_SAFE_DIFF": "écriture disponible après confirmation si une différence sûre est détectée",
    "HUMAN_SAVE_REQUIRED": "sauvegarde manuelle requise",
    "NEVER_AUTO_COMMIT": "mise à jour manuelle uniquement",
    "AUTH_BLOCKED_MANUAL": "mise à jour manuelle — authentification requise",
    "CANARY_PENDING_SKIP": "écriture en attente de validation",
    "COOKIE_SESSION_NOT_PC_OFF": "session locale requise",
    "BUMPER_NOT_AUTHORIZED": "mise à jour automatique non autorisée",
    "FUSED_UPDATE_BUMP": "mise à jour intégrée au prochain cycle automatique",
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


def _platform_capability_text(status: str, route: str) -> str:
    if route == "AUTO_ON_SAFE_DIFF":
        return "mise à jour disponible après confirmation si une différence est détectée"
    if route == "FUSED_UPDATE_BUMP":
        return "mise à jour intégrée au prochain cycle automatique si nécessaire"
    if route == "HUMAN_SAVE_REQUIRED":
        return "sauvegarde manuelle requise"
    if route == "NEVER_AUTO_COMMIT":
        return "mise à jour manuelle uniquement"
    if route == "AUTH_BLOCKED_MANUAL":
        return "mise à jour manuelle — authentification requise"
    return status_label_fr(status)


def build_platforms_status(*, program: str | None = None) -> str:
    """User-facing capability table built from live write-status data."""
    data = write_summary()
    rows = {r["platform"]: r for r in data.get("platforms") or []}
    header = "🎯 AUTOFRESH — PLATEFORMES" + (f" — {program.capitalize()}" if program else "")
    lines = [header, f"{len(ALL_PLATFORMS)} plateformes connues"]

    if program:
        mapped_flags = {pid: _is_mapped_for_program(pid, program) for pid in ALL_PLATFORMS}
        mapped_count = sum(1 for v in mapped_flags.values() if v)
        lines.append(
            f"{mapped_count} plateforme{'s' if mapped_count != 1 else ''} "
            f"suivie{'s' if mapped_count != 1 else ''} pour {program.capitalize()}"
        )
        lines.append("")
        for pid in ALL_PLATFORMS:
            row = rows.get(pid) or {}
            label = platform_label_fr(pid)
            if not mapped_flags[pid]:
                lines.append(f"• `{label}` — non suivie pour ce programme")
                continue
            status = row.get("status") or "UNPREPARED"
            route = row.get("route") or ""
            lines.append(
                f"• `{label}` — {_platform_capability_text(status, route)}"
            )
    else:
        verified = data.get("WRITE_VERIFIED") or data.get("write_verified_ratio") or "—"
        lines.append(f"Écriture techniquement vérifiée : {verified} plateformes")
        lines.append("")
        for pid in ALL_PLATFORMS:
            row = rows.get(pid) or {}
            status = row.get("status") or "UNPREPARED"
            route = row.get("route") or ""
            lines.append(
                f"• `{platform_label_fr(pid)}` — "
                f"{_platform_capability_text(status, route)}"
            )

    lines.append("")
    lines.append(
        "🔒 Une modification saisie dans Slack enregistre d'abord la nouvelle valeur. "
        "La mise à jour réelle d'un site n'est proposée qu'aux plateformes compatibles "
        "et demande une confirmation explicite."
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
    return (
        "🤖 AUTOFRESH — MODE D'EMPLOI\n"
        "\n"
        "Les 4 commandes à retenir :\n"
        "\n"
        "1️⃣ Voir si une offre est à jour\n"
        "• /autofresh Kraken statut\n"
        "\n"
        "2️⃣ Voir les valeurs enregistrées\n"
        "• /autofresh Kraken valeurs\n"
        "\n"
        "3️⃣ Modifier une valeur\n"
        "• /autofresh Kraken gain filleul 200 €\n"
        "• /autofresh Kraken code MONCODE\n"
        "• /autofresh Kraken lien https://...\n"
        "Après la commande, AutoFresh te montre ce qui changerait. "
        "Si une écriture réelle est possible, tu dois ensuite appuyer sur "
        "« Confirmer l'écriture ».\n"
        "\n"
        "4️⃣ Vérifier les remontées automatiques\n"
        "• /autofresh bump\n"
        "\n"
        "Commandes utiles en plus :\n"
        "• /autofresh Kraken divergences — voir les différences détectées\n"
        "• /autofresh Kraken plateformes — voir où Kraken est géré\n"
        "• /autofresh plateformes — voir l'état des 7 plateformes\n"
        "• /autofresh exemples — afficher quelques exemples\n"
        "\n"
        "Tu peux remplacer « Kraken » par le nom d'un autre programme suivi.\n"
        "\n"
        "Autres valeurs modifiables : conditions, dépôt minimum, dépense minimum, "
        "minimum de trade, nombre de transactions, délai, expiration, type de "
        "récompense et titre. Le gain parrain peut être enregistré dans AutoFresh, "
        "mais son écriture automatique sur les sites n'est pas prise en charge actuellement.\n"
        "\n"
        "🔒 Une commande de modification n'écrit jamais directement sur un site. "
        "La confirmation séparée reste obligatoire lorsqu'une écriture automatique "
        "est autorisée."
    )


def build_examples() -> str:
    return (
        "🤖 AUTOFRESH — EXEMPLES SIMPLES\n"
        "\n"
        "Consulter :\n"
        "• /autofresh Kraken statut — est-ce que tout est à jour ?\n"
        "• /autofresh Kraken valeurs — quelles valeurs AutoFresh utilise ?\n"
        "• /autofresh Kraken divergences — qu'est-ce qui diffère sur les sites ?\n"
        "• /autofresh bump — où en sont les remontées automatiques ?\n"
        "\n"
        "Modifier :\n"
        "• /autofresh Kraken gain filleul 200 €\n"
        "• /autofresh Kraken code ABC123\n"
        "• /autofresh Kraken lien https://invite.exemple/...\n"
        "\n"
        "Cibler un seul site :\n"
        "• /autofresh Kraken Super-Parrain gain filleul 25 €\n"
        "\n"
        "Supprimer une valeur personnalisée :\n"
        "• /autofresh Kraken supprimer gain filleul\n"
        "\n"
        "Une modification est d'abord préparée. Si AutoFresh peut réellement "
        "mettre un site à jour, un bouton « Confirmer l'écriture » apparaît."
    )


def _format_paris_time(raw: str | None) -> str:
    if not raw:
        return "—"
    from datetime import datetime
    from zoneinfo import ZoneInfo

    dt = datetime.fromisoformat(raw)
    local = dt.astimezone(ZoneInfo("Europe/Paris"))
    return local.strftime("%d/%m/%Y à %H:%M")


def _human_delay(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} h {mins:02d}"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def _build_bump_autres_section() -> str:
    """Read-only Slack status for the persisted randomized schedule."""
    import os
    from datetime import datetime, timezone

    from lib.bump_autres_schedule import fetch_last_run, load_schedule, summarize

    now = datetime.now(timezone.utc)
    schedule = load_schedule()
    if not schedule or schedule.get("period_date") != now.date().isoformat():
        return (
            "*Code-Parrainage + Parrainage.co*\n"
            "• planning du jour : pas encore généré\n"
            "• aucune action déclenchée par cette commande"
        )

    summary = summarize(schedule, now=now)
    lines = ["*Code-Parrainage + Parrainage.co*"]
    lines.append(f"• créneaux aléatoires prévus aujourd'hui : {summary['cycles_planned']}")
    lines.append(
        f"• cycles lancés : {summary['cycles_done']}/{summary['cycles_planned']}"
    )
    lines.append(
        f"• cycles entièrement réussis : "
        f"{summary.get('cycles_completed', 0)}/{summary['cycles_planned']}"
    )
    per_site = summary.get("per_site_completed") or {}
    lines.append(
        "• réussites par site : "
        f"Code-Parrainage {per_site.get('code', 0)}/{summary['cycles_planned']} · "
        f"Parrainage.co {per_site.get('parrainage', 0)}/{summary['cycles_planned']}"
    )
    if summary.get("partial_cycles"):
        lines.append(
            f"• cycles partiels à ne pas compter comme complets : "
            f"{summary['partial_cycles']}"
        )
    if summary.get("retryable_cycles"):
        lines.append(
            f"• récupération sûre planifiée : {summary['retryable_cycles']}"
        )

    next_raw = summary.get("next_planned_at")
    if next_raw:
        planned = datetime.fromisoformat(next_raw)
        if planned <= now:
            lines.append(
                "• prochain créneau : en retard de "
                f"{_human_delay((now - planned).total_seconds())} "
                f"(prévu {_format_paris_time(next_raw)}, heure de Paris)"
            )
            lines.append("• rattrapage : attendu au prochain passage du planificateur")
        else:
            lines.append(
                f"• prochain créneau : {_format_paris_time(next_raw)} "
                "(heure de Paris)"
            )
    else:
        lines.append("• prochain créneau : aucun autre aujourd'hui")

    if summary.get("last_dispatched_at"):
        lines.append(
            f"• dernier cycle lancé : {_format_paris_time(summary['last_dispatched_at'])} "
            "(heure de Paris)"
        )
    else:
        lines.append("• dernier cycle lancé : aucun aujourd'hui")

    if summary.get("any_catchup"):
        lines.append("• retard déjà rattrapé aujourd'hui : oui")

    last_run = None
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        try:
            last_run = fetch_last_run(token)
        except Exception:  # noqa: BLE001
            last_run = None

    if last_run and last_run.get("conclusion") == "failure":
        lines.append("• dernier cycle GitHub : échec — diagnostic requis")
    elif last_run and last_run.get("conclusion") == "success":
        lines.append("• dernier cycle GitHub : terminé sans erreur de workflow")

    return "\n".join(lines)


def _build_super_parrain_bump_section() -> str:
    """Read-only surface of the existing 24h + persistent-jitter logic."""
    from lib.super_parrain_schedule import (
        current_jitter_minutes,
        is_eligible,
        last_super_action_at,
    )

    last_at = last_super_action_at()
    eligible, next_at, _hours_remaining = is_eligible()
    jitter = current_jitter_minutes()

    lines = ["*Super-Parrain*"]
    lines.append(
        f"• dernier cycle réussi : {_format_paris_time(last_at.isoformat()) if last_at else 'aucun'}"
        + (" (heure de Paris)" if last_at else "")
    )
    lines.append(f"• délai minimum de 24 h atteint : {'oui' if eligible else 'non'}")
    lines.append(
        f"• décalage aléatoire de ce cycle : {jitter} min"
        if jitter is not None
        else "• décalage aléatoire : pas encore défini"
    )
    lines.append(
        "• prochaine éligibilité : maintenant"
        if eligible
        else f"• prochaine éligibilité : {_format_paris_time(next_at.isoformat())} (heure de Paris)"
    )
    return "\n".join(lines)


def build_bump_status() -> str:
    """Live '/autofresh bump' reply: randomized slots plus Super-Parrain."""
    return _build_bump_autres_section() + "\n\n" + _build_super_parrain_bump_section()


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
