"""Politique authentifiee / anti-ban Autofresh.

Principe: pour construire un writer fiable, on voit les vraies pages
compte/edition — pas reconstruire depuis le profil public seul.

Session
-------
1 plateforme = 1 session authentifiee par cycle
  login une fois
  → inventaire / pages edit
  → traitement sequentiel (concurrence 1)
  → post-verify
  → fin session

Jamais login/logout par annonce.
Reutiliser cookies/session pendant le run uniquement (memoire process).
Ne jamais ecrire storage-state / cookies auth dans repo, artifacts, logs.

Hierarchie des sources
----------------------
Ce que le compte peut editer (writer / mapping) :
  1. page compte / edition authentifiee
  2. API / import / interface officielle
  3. donnees internes projet (mappings, offers.json)

Verification du publie (post-verify / inventaire public) :
  page publique / profil

Exception
---------
Si une plateforme a une API/import officiel (ex. ReferralCodes.com Agent Import),
preferer cette voie a Playwright login.

CAPTCHA / challenge
-------------------
CAPTCHA != forcement mauvais credential.
Sur challenge inhabituel: stop propre, pas de boucle login, pas de retry agressif,
pas de contournement, etat REVIEW/BLOCKED.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class AuthFailureKind(str, Enum):
    INVALID_CREDENTIALS = "invalid_credentials"
    EXPIRED_SESSION = "expired_session"
    EXPECTED_LOGIN_FLOW = "expected_login_flow"
    CAPTCHA_OR_ANTIBOT = "captcha_or_antibot_challenge"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


# Ordre vertical plateformes (1 DONE avant la suivante)
VERTICAL_ORDER = (
    "super-parrain",
    "parrainage-co",
    "code-parrainage",
    "1parrainage",
    "referralcodes",  # prefer official import/API
    "referralcode-tv",
    "referraldrop",
)

# Plateformes ou l'interface officielle prime sur Playwright
PREFER_OFFICIAL_IMPORT = frozenset({"referralcodes"})

# Interdit de persister ces chemins dans artifacts/repo
FORBIDDEN_AUTH_ARTIFACT_GLOBS = (
    "**/storage-state*.json",
    "**/*cookies*.json",
    "**/*auth*state*.json",
    "**/playwright/.auth/**",
)


def session_rules() -> dict[str, Any]:
    return {
        "logins_per_platform_per_cycle": 1,
        "reuse_browser_context": True,
        "sequential_announcements": True,
        "max_concurrency": 1,
        "persist_storage_state_to_repo": False,
        "persist_storage_state_to_artifacts": False,
        "login_logout_per_announcement": False,
    }


def source_hierarchy() -> dict[str, list[str]]:
    return {
        "what_account_can_edit": [
            "authenticated_account_or_edit_page",
            "official_api_or_import",
            "internal_project_data",
        ],
        "published_result_verify": [
            "public_page",
        ],
    }


def target_cycle_flow() -> list[str]:
    return [
        "AUTH_once",
        "CAPTURE_from_account",
        "MAPPING",
        "DIFF",
        "WRITE_if_needed",
        "POST_VERIFY_public_or_auth",
        "SESSION_END",
    ]


def prefer_official_import(platform: str) -> bool:
    return (platform or "").strip().lower() in PREFER_OFFICIAL_IMPORT


def classify_auth_failure(message: str, *, status_code: int | None = None) -> AuthFailureKind:
    """Classifie un echec auth/anti-bot sans confondre CAPTCHA et mauvais mdp."""
    msg = (message or "").lower()
    if status_code in (429,) or any(x in msg for x in ("429", "rate limit", "too many requests", "throttle")):
        return AuthFailureKind.RATE_LIMIT
    if any(
        x in msg
        for x in (
            "captcha",
            "recaptcha",
            "hcaptcha",
            "challenge",
            "cloudflare",
            "cf-browser",
            "attention required",
            "verify you are human",
            "bot detection",
            "access denied",
        )
    ):
        return AuthFailureKind.CAPTCHA_OR_ANTIBOT
    if any(x in msg for x in ("403", "forbidden")) and "login" not in msg:
        return AuthFailureKind.CAPTCHA_OR_ANTIBOT
    if any(
        x in msg
        for x in (
            "invalid password",
            "mot de passe incorrect",
            "identifiants incorrect",
            "wrong password",
            "invalid credentials",
            "authentication failed",
            "login failed",
            "login echoue",
        )
    ):
        return AuthFailureKind.INVALID_CREDENTIALS
    if any(x in msg for x in ("session expired", "session expiree", "unauthorized", "401", "please log in", "reconnect")):
        return AuthFailureKind.EXPIRED_SESSION
    if any(x in msg for x in ("login", "connexion", "sign in", "authentif")):
        return AuthFailureKind.EXPECTED_LOGIN_FLOW
    return AuthFailureKind.UNKNOWN


def should_retry_login(kind: AuthFailureKind) -> bool:
    """Pas de retry agressif sur anti-bot / rate-limit / credentials invalides."""
    return kind in {AuthFailureKind.EXPIRED_SESSION, AuthFailureKind.EXPECTED_LOGIN_FLOW}


def should_stop_platform(kind: AuthFailureKind) -> bool:
    return kind in {
        AuthFailureKind.CAPTCHA_OR_ANTIBOT,
        AuthFailureKind.RATE_LIMIT,
        AuthFailureKind.INVALID_CREDENTIALS,
    }


def policy_snapshot() -> dict[str, Any]:
    return {
        "session": session_rules(),
        "sources": source_hierarchy(),
        "cycle_flow": target_cycle_flow(),
        "vertical_order": list(VERTICAL_ORDER),
        "prefer_official_import": sorted(PREFER_OFFICIAL_IMPORT),
        "forbidden_auth_artifact_globs": list(FORBIDDEN_AUTH_ARTIFACT_GLOBS),
    }
