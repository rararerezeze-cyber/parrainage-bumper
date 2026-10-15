"""Generic evidence requirements across all public offers."""
from lib.monitor.evidence import evidence_reviewable, source_excerpt


def test_source_excerpt_records_real_claim_not_just_a_number():
    html = "<h2>Parrainage</h2><p>Le filleul reçoit 15€ de bonus de parrainage.</p>"
    quote = source_excerpt(html, "referee_reward", "15 €")
    assert quote and "15€" in quote and "filleul" in quote


def test_source_excerpt_does_not_accept_tariff_near_parrainage():
    html = (
        "<h1>Programme de parrainage</h1>"
        "<p>Forfait mobile éligible à partir de 4,99€/mois.</p>"
    )
    assert source_excerpt(html, "referee_reward", "4,99 €") is None


def test_source_excerpt_refuses_value_absent_and_js_content():
    html = "<script>bonus='20€';</script><p>Programme de parrainage.</p>"
    assert source_excerpt(html, "referee_reward", "20 €") is None
    assert source_excerpt("<p>Remise de 20€</p>", "referee_reward", "99 €") is None


def test_generic_approval_requires_evidence_from_verified_official_source():
    base = dict(
        field="referee_reward", observed="15 €",
        excerpt="Le filleul reçoit 15 € de bonus de parrainage",
        source_url="https://example.test/parrainage", observations=4,
        source_class="VERIFIED_OFFICIAL", confidence="HIGH",
        parser_tests_passed=True,
    )
    assert evidence_reviewable(**base)
    for altered in (
        {"excerpt": None}, {"source_url": "http://example.test/"},
        {"observations": 2}, {"confidence": "REVIEW"},
        {"source_class": "UNVERIFIED"}, {"parser_tests_passed": False},
        {"excerpt": "Offre de 99 € de bonus"},
    ):
        assert not evidence_reviewable(**(base | altered))


def test_auto_accept_fails_closed_for_unsubstantiated_candidate(tmp_path):
    from lib.monitor.auto_accept import evaluate_field
    from lib.monitor.models import Confidence, FieldChange, Observation, ObservationStatus
    from lib.operator_overrides import OperatorOverrideStore
    cand = {
        "program": "example", "field": "referee_reward",
        "canonical": "5 €", "observed": "15 €",
        "authority": "OFFICIAL_PUBLIC_MONITOR",
        "source_country": "FR", "source_locale": "fr",
        "campaign_scope": "FR", "live_high_streak": 5,
        "source_url": "https://example.test/parrainage",
    }
    obs = Observation(
        program="example", status=ObservationStatus.CANDIDATE,
        confidence=Confidence.HIGH, source_url=cand["source_url"],
        parser="example_parser", detected_at="2026-10-15T00:00:00+00:00",
        canonical_fields={"referee_reward": "5 €"},
        observed_fields={"referee_reward": "15 €"},
        changes=[FieldChange(field="referee_reward", old="5 €", new="15 €")],
        source_class="VERIFIED_OFFICIAL", monitor_status="MONITOR_VERIFIED",
        live_high_streak=5, parser_tests_passed=True,
        source_country="FR", source_locale="fr",
        campaign_scope="FR", field_authority={"referee_reward": "OFFICIAL_PUBLIC_MONITOR"},
    )
    out = evaluate_field(cand, obs, store=OperatorOverrideStore(path=tmp_path / "overrides.json"), accepted={})
    assert out["decision"] == "REJECT"
    assert "missing_verified_source_excerpt" in out["reasons"]
