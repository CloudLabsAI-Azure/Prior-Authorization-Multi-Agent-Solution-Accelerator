"""Boundary-validation tests for PriorAuthRequest (issue #28).

These tests assert that the FastAPI/Pydantic layer rejects structurally
invalid prior-authorization payloads with HTTP 422 *before* the multi-agent
orchestrator (and any Hosted Agent V2 capacity) is ever invoked.

Run from the ``backend/`` directory:

    python -m pytest tests/ -v

The test client patches ``app.routers.review.run_multi_agent_review`` so
the one happy-path test never makes a real Foundry call, and the rejection
tests would fail loudly if validation were skipped because the patched
orchestrator raises ``AssertionError`` whenever it is reached.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# Sample fully-valid request body — mirrors frontend SAMPLE_REQUEST and
# backend/e2e_test.py so the validators can never silently break the demo.
VALID_BODY: dict[str, Any] = {
    "patient_name": "John Smith",
    "patient_dob": "1958-03-15",
    "provider_npi": "1234567890",
    "diagnosis_codes": ["R91.1", "J18.9", "R05.9"],
    "procedure_codes": ["31628"],
    "clinical_notes": "65yo M with persistent cough; CT shows 2.1cm spiculated RUL nodule.",
    "insurance_id": "ABC123456",
}


def _with(**overrides: Any) -> dict[str, Any]:
    """Return VALID_BODY with the given fields overridden."""
    body = dict(VALID_BODY)
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Happy path — patched orchestrator returns a minimal-but-valid result dict
# ---------------------------------------------------------------------------


_MOCK_RESULT: dict[str, Any] = {
    "recommendation": "approve",
    "confidence": 0.92,
    "confidence_level": "HIGH",
    "summary": "Mock review.",
    "tool_results": [],
    "clinical_rationale": "Mock rationale.",
    "coverage_criteria_met": [],
    "coverage_criteria_not_met": [],
    "missing_documentation": [],
    "documentation_gaps": [],
    "policy_references": [],
    "decision_gate": "APPROVE",
    "criteria_summary": "5/5",
    "synthesis_audit_trail": {},
    "agent_results": {},
    "audit_trail": None,
    "audit_justification": "Mock justification.",
    "audit_justification_pdf": None,
}


async def _mock_orchestrator(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return _MOCK_RESULT


def test_valid_request_passes_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A structurally-valid request should be accepted (HTTP 200) and reach
    the orchestrator. We monkeypatch the orchestrator so no real Foundry
    call is made."""
    monkeypatch.setattr(
        "app.routers.review.run_multi_agent_review", _mock_orchestrator
    )
    response = client.post("/api/review", json=VALID_BODY)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"] == "approve"


# ---------------------------------------------------------------------------
# Negative cases — issue #28 reproductions
# ---------------------------------------------------------------------------


def _orchestrator_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "Orchestrator was invoked despite the request being invalid — "
        "validation regressed (issue #28)."
    )


@pytest.fixture(autouse=True)
def _block_orchestrator_unless_overridden(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """For every test except the explicit happy-path one, fail the test if
    the orchestrator is reached. The happy-path test installs its own
    monkeypatch via the ``monkeypatch`` fixture, which the autouse fixture
    cannot interfere with because pytest applies fixtures in dependency
    order — the explicit ``monkeypatch.setattr`` inside the test runs last
    and wins."""
    if request.node.name == "test_valid_request_passes_validation":
        return
    monkeypatch.setattr(
        "app.routers.review.run_multi_agent_review", _orchestrator_must_not_run
    )


def _assert_422_on(field: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/review with `body`, assert 422, and return the matching
    error entry whose `loc` ends with `field`."""
    response = client.post("/api/review", json=body)
    assert response.status_code == 422, (
        f"expected 422 for invalid {field}, got {response.status_code}: "
        f"{response.text}"
    )
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail, response.text
    matches = [e for e in detail if e.get("loc", [None])[-1] == field]
    assert matches, f"no error for field {field!r} in: {detail}"
    return matches[0]


def test_future_dob_rejected_422() -> None:
    err = _assert_422_on("patient_dob", _with(patient_dob="2043-01-01"))
    assert "future" in err["msg"].lower()


def test_impossible_dob_rejected_422() -> None:
    # 2026-02-30 parses as YYYY-MM-DD but is not a real calendar date.
    err = _assert_422_on("patient_dob", _with(patient_dob="2026-02-30"))
    assert "valid past date" in err["msg"].lower()


def test_freetext_dob_rejected_422() -> None:
    err = _assert_422_on("patient_dob", _with(patient_dob="not a date"))
    assert "yyyy-mm-dd" in err["msg"].lower()


def test_wrong_format_dob_rejected_422() -> None:
    # MM-DD-YYYY is a common mistake — must be rejected.
    err = _assert_422_on("patient_dob", _with(patient_dob="03-15-1958"))
    assert "yyyy-mm-dd" in err["msg"].lower()


def test_empty_diagnosis_codes_rejected_422() -> None:
    response = client.post("/api/review", json=_with(diagnosis_codes=[]))
    assert response.status_code == 422, response.text


def test_all_blank_diagnosis_codes_rejected_422() -> None:
    err = _assert_422_on("diagnosis_codes", _with(diagnosis_codes=["", " ", "\t"]))
    assert "diagnosis_code" in err["msg"]


def test_empty_procedure_codes_rejected_422() -> None:
    response = client.post("/api/review", json=_with(procedure_codes=[]))
    assert response.status_code == 422, response.text


def test_malformed_icd10_rejected_422() -> None:
    err = _assert_422_on(
        "diagnosis_codes", _with(diagnosis_codes=["NOT-A-CODE"])
    )
    assert "icd-10" in err["msg"].lower()


def test_u_prefix_icd10_rejected_422() -> None:
    # U codes are reserved by WHO and not used by US payers.
    err = _assert_422_on("diagnosis_codes", _with(diagnosis_codes=["U07.1"]))
    assert "icd-10" in err["msg"].lower()


def test_malformed_cpt_rejected_422() -> None:
    err = _assert_422_on("procedure_codes", _with(procedure_codes=["ABCDE"]))
    assert "cpt" in err["msg"].lower() or "hcpcs" in err["msg"].lower()


def test_short_cpt_rejected_422() -> None:
    err = _assert_422_on("procedure_codes", _with(procedure_codes=["1234"]))
    assert "cpt" in err["msg"].lower() or "hcpcs" in err["msg"].lower()


def test_lowercase_codes_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lowercase ICD-10 / HCPCS codes should be accepted *and* normalized
    to upper-case before reaching the orchestrator."""
    seen: dict[str, Any] = {}

    async def capturing_orchestrator(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        seen["payload"] = payload
        return _MOCK_RESULT

    monkeypatch.setattr(
        "app.routers.review.run_multi_agent_review", capturing_orchestrator
    )
    body = _with(
        diagnosis_codes=["r91.1", " j18.9 "],
        procedure_codes=["j3490"],
    )
    response = client.post("/api/review", json=body)
    assert response.status_code == 200, response.text
    assert seen["payload"]["diagnosis_codes"] == ["R91.1", "J18.9"]
    assert seen["payload"]["procedure_codes"] == ["J3490"]


# ---------------------------------------------------------------------------
# Per-agent endpoints inherit the same validation
# ---------------------------------------------------------------------------


def test_per_agent_clinical_inherits_validation() -> None:
    response = client.post("/api/agents/clinical", json=_with(patient_dob="2043-01-01"))
    assert response.status_code == 422, response.text


def test_per_agent_coverage_inherits_validation() -> None:
    body = {
        "request": _with(diagnosis_codes=["NOT-A-CODE"]),
        "clinical_findings": {"diagnosis_validation": []},
    }
    response = client.post("/api/agents/coverage", json=body)
    assert response.status_code == 422, response.text
