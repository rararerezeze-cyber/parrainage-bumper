"""merge_conservative_mapping_update() -- non-regression for the
capture_oneparrainage() destructive-overwrite incident.

Real incident: capture_auth_readonly.py::capture_oneparrainage() called
write_build_result() unconditionally on every READ-ONLY capture run, which
always emits a fixed schema (edit_url=None, quality="native_list_or_auth",
no memory of anything previously learned) and blindly overwrote
data/platform-mappings/1parrainage.*.json. One capture run (2026-08-15,
commit 83f22bca) destroyed manually-verified evidence for all 31
1parrainage programs it touched, including the real WRITE_VERIFIED
edit_url/platform_offer_id for kraken.
"""
from __future__ import annotations

from lib.template_builder import merge_conservative_mapping_update


def _kraken_verified_record() -> dict:
    return {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "sync_mode": "REVIEW",
        "announcement_url": "https://www.1parrainage.com/listeannonces_98906_Adrien89.php#id=100408",
        "edit_url": "https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/",
        "platform_values": {
            "personal_code": "cpbrgddy",
            "personal_link": "https://invite.kraken.com/JDNW/s5qudqe4",
            "referee_reward": "200 € en cryptomonnaies",
        },
        "confidences": {
            "personal_code": "medium",
            "personal_link": "medium",
            "referee_reward": "medium",
        },
        "notes": (
            "WRITE_VERIFIED 2026-08-13: public now matches OPERATOR_VALIDATED "
            "cpbrgddy / s5qudqe4 / 200 € en cryptomonnaies."
        ),
        "quality": "native_list_full_inventory",
        "platform_offer_id": "100408",
        "occurrences": [{"offer_id": "100408", "brand": "coupon promotionnel Kraken", "chars": 407}],
        "occurrence_count": 1,
        "edit_url_source": "headed_manual_discovery",
        "edit_url_learned_at": "2026-08-13T10:29:29.092808+00:00",
    }


def _degraded_recapture() -> dict:
    """What capture_oneparrainage()'s blind write_build_result() call
    actually produced for kraken -- the literal shape of the real
    regression (edit_url/platform_offer_id/occurrences gone, sync_mode and
    quality silently changed, generic notes)."""
    return {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "sync_mode": "SAFE_AUTO",
        "announcement_url": "https://www.1parrainage.com/listeannonces_98906_Adrien89.php",
        "edit_url": None,
        "platform_values": {
            "personal_code": "cpbrgddy",
            "personal_link": "https://invite.kraken.com/JDNW/s5qudqe4",
            "referee_reward": "200 € en cryptomonnaies",
        },
        "confidences": {
            "personal_code": "high",
            "personal_link": "high",
            "referee_reward": "high",
        },
        "notes": "native_platform_style_only; no emoji re-injection",
        "quality": "native_list_or_auth",
        "style_policy": "native_platform_style_only",
    }


def test_write_verified_record_is_frozen_entirely():
    existing = _kraken_verified_record()
    fresh = _degraded_recapture()
    merged, report = merge_conservative_mapping_update(existing, fresh)
    assert merged == existing
    assert report["kept_existing"] == ["*ALL* (WRITE_VERIFIED record frozen)"]


def test_write_verified_freeze_is_idempotent():
    existing = _kraken_verified_record()
    fresh = _degraded_recapture()
    once, _ = merge_conservative_mapping_update(existing, fresh)
    twice, _ = merge_conservative_mapping_update(once, fresh)
    assert once == twice == existing


def test_protected_fields_survive_a_degraded_recapture_when_not_write_verified():
    """Same real-world shape as kraken, minus the WRITE_VERIFIED marker --
    proves the protection is not solely dependent on the freeze branch."""
    existing = _kraken_verified_record()
    existing["notes"] = "some manual note only, unverified"
    fresh = _degraded_recapture()

    merged, report = merge_conservative_mapping_update(existing, fresh)

    assert merged["edit_url"] == existing["edit_url"]
    assert merged["platform_offer_id"] == "100408"
    assert merged["occurrences"] == existing["occurrences"]
    assert merged["occurrence_count"] == 1
    assert merged["edit_url_source"] == "headed_manual_discovery"
    assert merged["edit_url_learned_at"] == existing["edit_url_learned_at"]
    assert merged["sync_mode"] == "REVIEW"
    assert merged["quality"] == "native_list_full_inventory"
    assert merged["announcement_url"] == existing["announcement_url"]
    assert merged["confidences"] == existing["confidences"]
    assert merged["notes"] == existing["notes"]
    for key in (
        "edit_url",
        "platform_offer_id",
        "occurrences",
        "occurrence_count",
        "edit_url_source",
        "edit_url_learned_at",
        "sync_mode",
        "quality",
        "announcement_url",
    ):
        assert key in report["kept_existing"], key


def test_enriches_a_field_that_was_previously_missing():
    existing = {
        "platform": "1parrainage",
        "program": "binance",
        "language": "fr",
        "platform_values": {},
        "confidences": {},
        "notes": None,
        "quality": None,
        "edit_url": None,
        "platform_offer_id": "1082",
        "occurrences": [{"offer_id": "1082"}],
        "occurrence_count": 1,
    }
    fresh = {
        "platform": "1parrainage",
        "program": "binance",
        "language": "fr",
        "platform_values": {"personal_link": "https://www.binance.com/activity/referral-entry/CPA?ref=X"},
        "confidences": {"personal_link": "medium"},
        "notes": "native_platform_style_only",
        "quality": "native_list_or_auth",
        "edit_url": None,
        "platform_offer_id": None,
        "occurrences": None,
        "occurrence_count": None,
    }
    merged, report = merge_conservative_mapping_update(existing, fresh)

    assert merged["platform_values"]["personal_link"] == fresh["platform_values"]["personal_link"]
    assert merged["confidences"]["personal_link"] == "medium"
    assert merged["quality"] == "native_list_or_auth"  # was missing -> enriched
    assert merged["notes"] == "native_platform_style_only"  # was missing -> enriched
    # never touched -- these were already present and fresh had nothing
    assert merged["platform_offer_id"] == "1082"
    assert merged["occurrences"] == existing["occurrences"]
    assert "platform_values.personal_link" in report["enriched"]
    assert "quality" in report["enriched"]


def test_capture_before_enrich_after_is_superset_never_a_regression():
    """The exact scenario requested: an initial mapping gets enriched by a
    first capture, then a second (degraded) capture must never lose
    anything the first one gained -- the field set after is always a
    superset (by key AND by "not None") of the field set before.
    """
    original = {
        "platform": "1parrainage",
        "program": "vinted",
        "language": "fr",
        "platform_values": {"personal_code": "weeew89"},
        "confidences": {"personal_code": "high"},
        "notes": None,
        "quality": None,
        "edit_url": None,
        "platform_offer_id": None,
        "occurrences": None,
        "occurrence_count": None,
        "announcement_url": None,
    }
    first_capture = {
        "platform": "1parrainage",
        "program": "vinted",
        "language": "fr",
        "platform_values": {
            "personal_code": "weeew89",
            "personal_link": "https://www.vinted.fr/invite/weeew89",
        },
        "confidences": {"personal_code": "high", "personal_link": "high"},
        "notes": "native_platform_style_only",
        "quality": "native_list_full_inventory",
        "edit_url": None,
        "platform_offer_id": "910",
        "occurrences": [{"offer_id": "910", "brand": "coupon promotionnel Vinted", "chars": 441}],
        "occurrence_count": 1,
        "announcement_url": "https://www.1parrainage.com/listeannonces_98906_Adrien89.php#id=910",
    }
    after_first, _ = merge_conservative_mapping_update(original, first_capture)

    def present_keys(d: dict) -> set[str]:
        return {k for k, v in d.items() if v not in (None, "", [], {})}

    assert present_keys(after_first) >= present_keys(original)
    assert present_keys(after_first) >= present_keys(first_capture)

    # Second capture: a degraded quick-list pass, same shape as the real
    # incident (loses platform_offer_id/occurrences, downgrades quality).
    second_degraded_capture = {
        "platform": "1parrainage",
        "program": "vinted",
        "language": "fr",
        "platform_values": {"personal_code": "weeew89", "personal_link": "https://www.vinted.fr/invite/weeew89"},
        "confidences": {"personal_code": "high", "personal_link": "high"},
        "notes": "native_platform_style_only; no emoji re-injection",
        "quality": "native_list_or_auth",
        "edit_url": None,
        "platform_offer_id": None,
        "occurrences": None,
        "occurrence_count": None,
        "announcement_url": "https://www.1parrainage.com/listeannonces_98906_Adrien89.php",
    }
    after_second, report = merge_conservative_mapping_update(after_first, second_degraded_capture)

    assert present_keys(after_second) >= present_keys(after_first), (
        "a later capture must never drop a field the previous one had"
    )
    assert after_second["platform_offer_id"] == "910"
    assert after_second["occurrences"] == first_capture["occurrences"]
    assert after_second["quality"] == "native_list_full_inventory"
    assert after_second["announcement_url"] == first_capture["announcement_url"]
    assert "platform_offer_id" in report["kept_existing"]


def test_merge_is_idempotent_for_a_stable_source_page():
    """A read-only probe that observes the exact same page twice in a row
    (nothing changed on the live site) must produce a byte-identical
    mapping both times -- no field should drift on repeated, unchanged
    captures."""
    existing = {
        "platform": "1parrainage",
        "program": "joko",
        "language": "fr",
        "platform_values": {"personal_code": "stxzzb"},
        "mutable_fields": ["personal_code"],
        "confidences": {"personal_code": "high"},
        "notes": "native_platform_style_only",
        "quality": "native_list_full_inventory",
        "edit_url": None,
        "platform_offer_id": "1703",
        "occurrences": [{"offer_id": "1703"}],
        "occurrence_count": 1,
        "announcement_url": "https://www.1parrainage.com/listeannonces_98906_Adrien89.php#id=1703",
    }
    fresh = dict(existing)  # identical observation
    merged_once, report_once = merge_conservative_mapping_update(existing, fresh)
    merged_twice, report_twice = merge_conservative_mapping_update(merged_once, fresh)
    assert merged_once == existing
    assert merged_twice == existing
    assert merged_once == merged_twice


def test_no_existing_mapping_adopts_fresh_wholesale():
    """First-ever capture (no prior mapping): nothing to protect yet."""
    fresh = {"program": "newprogram", "platform_values": {"personal_code": "ABC"}, "notes": "x"}
    merged, report = merge_conservative_mapping_update(None, fresh)
    assert merged == fresh
    assert set(report["enriched"]) == {"program", "platform_values", "notes"}
