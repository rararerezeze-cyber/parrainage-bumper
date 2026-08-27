"""The one authoritative content-mutation plan for a fused Super-Parrain cycle.

Incident 2026-08-27 (GH run 33098049116). The pre-check reported
``canary_need_update_count = 0`` and the runtime nevertheless prefilled Kraken's
body and performed a real Save, which then failed post-verify.

Both were "right" about different things, because they compared different
sources for the same field:

* pre-check  (``compare_from_mapping_platform_values``) took ``current.body``
  from the **golden file in the repository**;
* runtime    (``prepare_before_save``) took ``current.body`` from the **live
  edit form** on the site.

The golden matched the desired render, so the pre-check said ``in_sync``; the
live form still held an older plain-text body, so the runtime said
``needs_update``. Two sources of truth, two verdicts, one unplanned write.

This module makes the pre-check verdict the single authority for *whether a
program may receive content at all*. The runtime may still decide it has
nothing to change -- that is a narrowing, and always allowed -- but it can never
widen the plan. A program absent from the plan gets bump-only, full stop.

Fail-closed: no plan, an unreadable plan, or a runtime that wants to write
outside the plan all resolve to "no content mutation". The historical bumper is
never blocked by any of this -- it keeps saving and bumping every listing.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

# Env var carrying the serialized plan from tools/super_parrain_cycle.py to the
# bumper.py subprocess. A plan is a JSON object, never a bare list, so that
# "absent" and "empty" stay distinguishable.
CONTENT_PLAN_ENV = "AUTOFRESH_CONTENT_PLAN"

PLAN_VERSION = 1

# Reasons surfaced in the cycle report.
REASON_NO_PLAN = "no_precheck_content_plan_fail_closed"
REASON_NOT_IN_PLAN = "not_in_precheck_content_plan"
REASON_DISAGREEMENT = "precheck_runtime_disagreement_fail_closed"


def build_plan(programs: Iterable[str], *, source: str = "precheck") -> dict[str, Any]:
    """Build the plan the pre-check authorizes. Empty list = no content at all."""
    allowed = sorted({str(p).strip().lower() for p in (programs or []) if str(p).strip()})
    return {"version": PLAN_VERSION, "source": source, "allowed_programs": allowed}


def serialize_plan(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":"))


def load_plan(env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Read the plan from the environment. None means *no plan was provided*."""
    env = env if env is not None else os.environ
    raw = (env.get(CONTENT_PLAN_ENV) or "").strip()
    if not raw:
        return None
    try:
        plan = json.loads(raw)
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    if not isinstance(plan.get("allowed_programs"), list):
        return None
    return plan


def content_allowed(program: str, plan: dict[str, Any] | None) -> tuple[bool, str]:
    """May this program receive a content mutation in this cycle?

    Returns (allowed, reason). Fail-closed on every uncertainty.
    """
    if plan is None:
        return False, REASON_NO_PLAN
    name = (program or "").strip().lower()
    if not name:
        return False, REASON_NOT_IN_PLAN
    allowed = {str(p).strip().lower() for p in plan.get("allowed_programs") or []}
    if name in allowed:
        return True, "in_precheck_content_plan"
    return False, REASON_NOT_IN_PLAN


def classify_disagreement(
    *, program: str, plan: dict[str, Any] | None, runtime_needs_update: bool
) -> dict[str, Any]:
    """Describe a runtime that wants to write where the pre-check did not.

    This is the invariant the incident violated. When it happens the content
    write is refused (fail-closed) and the event is reported; the bump is not
    affected.
    """
    allowed, reason = content_allowed(program, plan)
    disagreement = bool(runtime_needs_update) and not allowed
    return {
        "program": program,
        "precheck_allowed": allowed,
        "runtime_needs_update": bool(runtime_needs_update),
        "disagreement": disagreement,
        "reason": REASON_DISAGREEMENT if disagreement else reason,
        "content_mutation_allowed": allowed and bool(runtime_needs_update),
    }
