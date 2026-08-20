"""Safety and schema tests for the public 1Parrainage CMP diagnostic."""
from __future__ import annotations

import inspect
from pathlib import Path

from tools import diagnose_1parrainage_consent as diag


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "diagnose_1parrainage_consent.yml"


def test_diagnostic_is_public_login_only_and_has_no_interaction_primitives():
    source = inspect.getsource(diag)
    assert diag.LOGIN_URL == "https://www.1parrainage.com/login"
    for forbidden in (
        ".click(",
        ".fill(",
        ".press(",
        "press_sequentially",
        ".submit(",
        "form.submit",
        "add_cookies",
        "storage_state",
        "localStorage",
        "sessionStorage",
        "ONEPARRAINAGE_EMAIL",
        "ONEPARRAINAGE_PASSWORD",
        "parrainages/edit",
    ):
        assert forbidden not in source


def test_diagnostic_captures_required_cmp_evidence_without_cookie_values():
    source = inspect.getsource(diag)
    for required in (
        "#sd-cmp",
        "html_excerpt",
        "buttons",
        "bounding_box",
        "shadow_roots",
        "overlay_nodes",
        "requestfailed",
        "relevant_console",
        "user_agent",
        "viewport",
        "timezone",
        "locale",
    ):
        assert required in source
    assert "context.cookies" not in source
    assert "document.cookie" not in source


def test_urls_are_sanitized_before_artifact_persistence():
    assert (
        diag._safe_url("https://example.test/path?token=secret#frag")
        == "https://example.test/path"
    )


def test_network_filter_is_strictly_cmp_related():
    assert diag._is_cmp_url("https://choices.consentframework.com/js/cmp")
    assert diag._is_cmp_url("https://cdn.sirdata.io/a.js")
    assert diag._is_cmp_url("https://x.test/sddan.js")
    assert not diag._is_cmp_url("https://www.1parrainage.com/login")


def test_workflow_has_no_secrets_and_read_only_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "secrets." not in workflow
    assert "AUTOFRESH_LIVE_WRITES: \"0\"" in workflow
    assert "canary_write_1parrainage" not in workflow
    assert "diagnose_1parrainage_consent.py" in workflow
    assert "--wait-seconds 30" in workflow


def test_workflow_uploads_only_dedicated_diagnostic_directory():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "diagnostic-artifacts/1parrainage-consent/" in workflow
    for forbidden in ("data/platform-write-status.json", "data/audit", "git push", "git commit"):
        assert forbidden not in workflow
