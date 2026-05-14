import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Request-validation regex (issue #28)
# ---------------------------------------------------------------------------
# Enforced by `PriorAuthRequest` field validators below so that FastAPI
# returns HTTP 422 at request-binding time, BEFORE the orchestrator (and
# therefore Hosted Agent V2) is ever invoked. This protects the model
# capacity budget from malformed or clinically impossible inputs.
#
# DOB:  ISO date (YYYY-MM-DD); additionally parsed with date.fromisoformat()
#       to reject impossible calendar dates (e.g. 2026-02-30) and compared
#       against today() to reject future DOBs.
#
# ICD-10: First char A-T or V-Z (U-prefix is reserved by WHO for emergency
#       codes such as U07.1 COVID; not accepted on PA submissions), then
#       digit, then alphanumeric, optional decimal + 1-4 alphanumerics.
#       Matches: R91.1, J18.9, M17.11, M17, J3490 (no — that's HCPCS).
#
# CPT/HCPCS: Two alternations:
#       - 4 digits + (digit|letter)  → CPT (27447) and CPT Cat-III (0028T)
#       - Letter + 4 digits          → HCPCS Level II (J3490)
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ICD10_RE = re.compile(r"^[A-TV-Z][0-9][A-Z0-9](?:\.[A-Z0-9]{1,4})?$")
_CPT_HCPCS_RE = re.compile(r"^([0-9]{4}[0-9A-Z]|[A-Z][0-9]{4})$")


class PriorAuthRequest(BaseModel):
    patient_name: str
    patient_dob: str
    provider_npi: str
    diagnosis_codes: list[str] = Field(min_length=1)  # ICD-10 codes
    procedure_codes: list[str] = Field(min_length=1)  # CPT/HCPCS codes
    clinical_notes: str
    insurance_id: str | None = None

    @field_validator("patient_dob", mode="after")
    @classmethod
    def _validate_dob(cls, v: str) -> str:
        v = v.strip()
        if not _DOB_RE.match(v):
            raise ValueError(
                "patient_dob must be a valid past date in YYYY-MM-DD format"
            )
        try:
            parsed = date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(
                "patient_dob must be a valid past date in YYYY-MM-DD format"
            ) from exc
        if parsed > date.today():
            raise ValueError("patient_dob must not be in the future")
        return v

    @field_validator("diagnosis_codes", mode="after")
    @classmethod
    def _validate_diagnosis_codes(cls, v: list[str]) -> list[str]:
        # Trim + uppercase, drop empties (frontend "Add Code" UI artifacts).
        cleaned = [c.strip().upper() for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("at least one diagnosis_code is required")
        for code in cleaned:
            if not _ICD10_RE.match(code):
                raise ValueError(
                    f"invalid ICD-10 diagnosis code: {code!r} "
                    "(expected format e.g. R91.1, M17.11, J18.9)"
                )
        return cleaned

    @field_validator("procedure_codes", mode="after")
    @classmethod
    def _validate_procedure_codes(cls, v: list[str]) -> list[str]:
        # Trim + uppercase, drop empties (frontend "Add Code" UI artifacts).
        cleaned = [c.strip().upper() for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("at least one procedure_code is required")
        for code in cleaned:
            if not _CPT_HCPCS_RE.match(code):
                raise ValueError(
                    f"invalid CPT/HCPCS procedure code: {code!r} "
                    "(expected format e.g. 27447, 31628, J3490, 0028T)"
                )
        return cleaned


class ToolResult(BaseModel):
    tool_name: str = ""
    status: str = "warning"  # "pass", "fail", "warning"
    detail: str = ""


# --- Per-agent result models ---


class AgentCheck(BaseModel):
    """A single rule/check that an agent performed."""
    rule: str = ""
    result: str = "info"  # "pass", "fail", "warning", "info"
    detail: str = ""


class ChecklistItem(BaseModel):
    item: str = ""
    status: str = "incomplete"  # "complete", "incomplete", "missing"
    detail: str = ""


class ComplianceResult(BaseModel):
    agent_name: str = "Compliance Agent"
    checks_performed: list[AgentCheck] = []
    checklist: list[ChecklistItem] = []
    overall_status: str = "incomplete"
    missing_items: list[str] = []
    additional_info_requests: list[str] = []
    error: str | None = None


class DiagnosisValidation(BaseModel):
    code: str = ""
    valid: bool = False
    description: str = ""
    billable: bool = False
    hierarchy_note: str = ""  # only when non-billable code has specific children


class ClinicalExtraction(BaseModel):
    chief_complaint: str = ""
    history_of_present_illness: str = ""
    prior_treatments: list[str] = []
    severity_indicators: list[str] = []
    functional_limitations: list[str] = []
    diagnostic_findings: list[str] = []
    duration_and_progression: str = ""
    medical_history_and_comorbidities: str = ""
    extraction_confidence: int = 0  # 0-100 overall extraction confidence


class ProcedureValidation(BaseModel):
    code: str = ""
    valid: bool = False
    description: str = ""
    source: str = ""  # "orchestrator_preflight" or "unverified"


class LiteratureReference(BaseModel):
    title: str = ""
    pmid: str = ""
    relevance: str = ""


class ClinicalTrialReference(BaseModel):
    nct_id: str = ""
    title: str = ""
    status: str = ""
    relevance: str = ""


class ClinicalResult(BaseModel):
    agent_name: str = "Clinical Reviewer Agent"
    checks_performed: list[AgentCheck] = []
    diagnosis_validation: list[DiagnosisValidation] = []
    procedure_validation: list[ProcedureValidation] = []
    clinical_extraction: ClinicalExtraction | None = None
    literature_support: list[LiteratureReference] = []
    clinical_trials: list[ClinicalTrialReference] = []
    clinical_summary: str = ""
    tool_results: list[ToolResult] = []
    error: str | None = None


class ProviderVerification(BaseModel):
    npi: str = ""
    name: str = ""
    specialty: str = ""
    status: str = ""  # "active", "inactive", "not_found"
    detail: str = ""


class CoveragePolicy(BaseModel):
    policy_id: str = ""
    title: str = ""
    type: str = ""  # "LCD", "NCD"
    relevant: bool = True


class CriterionAssessment(BaseModel):
    criterion: str = ""
    status: str = "INSUFFICIENT"  # "MET", "NOT_MET", "INSUFFICIENT"
    confidence: int = 0  # 0-100 per-criterion confidence
    evidence: list[str] = []
    notes: str = ""
    source: str = ""
    # Backward compat field
    met: bool = False


class DocumentationGap(BaseModel):
    what: str = ""
    critical: bool = False
    request: str = ""


class CoverageResult(BaseModel):
    agent_name: str = "Coverage Agent"
    checks_performed: list[AgentCheck] = []
    provider_verification: ProviderVerification | None = None
    coverage_policies: list[CoveragePolicy] = []
    criteria_assessment: list[CriterionAssessment] = []
    coverage_criteria_met: list[str] = []
    coverage_criteria_not_met: list[str] = []
    policy_references: list[str] = []
    coverage_limitations: list[str] = []
    documentation_gaps: list[DocumentationGap] = []
    tool_results: list[ToolResult] = []
    error: str | None = None


class AgentResults(BaseModel):
    compliance: ComplianceResult | None = None
    clinical: ClinicalResult | None = None
    coverage: CoverageResult | None = None


class SynthesisOutput(BaseModel):
    """Output schema for the Synthesis Decision Agent.

    Used as structured output format to enforce consistent JSON from the
    synthesis agent. Fields match the keys consumed by the orchestrator
    in run_multi_agent_review().
    """
    recommendation: str = "pend_for_review"  # "approve" or "pend_for_review"
    confidence: float = 0.0
    confidence_level: str = ""  # "HIGH", "MEDIUM", "LOW"
    summary: str = ""
    clinical_rationale: str = ""
    decision_gate: str = ""  # "gate_1_provider", "gate_2_codes", "gate_3_necessity", "approved"
    coverage_criteria_met: list[str] = []
    coverage_criteria_not_met: list[str] = []
    missing_documentation: list[str] = []
    policy_references: list[str] = []
    criteria_summary: str = ""
    synthesis_audit_trail: dict = {}  # gate_results + confidence_components from evaluation
    disclaimer: str = ""


class AuditTrail(BaseModel):
    data_sources: list[str] = []
    review_started: str = ""
    review_completed: str = ""
    extraction_confidence: int = 0
    assessment_confidence: int = 0
    criteria_met_count: str = ""  # "N/M" format


class ReviewResponse(BaseModel):
    request_id: str
    recommendation: str  # "approve", "pend_for_review"
    confidence: float = 0.0
    confidence_level: str = ""  # "HIGH", "MEDIUM", "LOW"
    summary: str
    tool_results: list[ToolResult]
    clinical_rationale: str
    coverage_criteria_met: list[str] = []
    coverage_criteria_not_met: list[str] = []
    missing_documentation: list[str] = []
    documentation_gaps: list[DocumentationGap] = []
    policy_references: list[str] = []
    decision_gate: str = ""  # "gate_1_provider", "gate_2_codes", "gate_3_necessity", "approved"
    criteria_summary: str = ""  # e.g. "8 of 8 criteria MET"
    synthesis_audit_trail: dict = {}  # gate_results + confidence_components from synthesis agent
    disclaimer: str = "AI-assisted draft. Medicare LCDs/NCDs applied. Human review required."
    agent_results: AgentResults | None = None
    audit_trail: AuditTrail | None = None
    audit_justification: str | None = None
    audit_justification_pdf: str | None = None  # Base64-encoded PDF


# --- Decision & Notification models ---


class DecisionRequest(BaseModel):
    """POST /api/decision request body."""
    request_id: str
    action: str  # "accept" or "override"
    override_recommendation: str | None = None  # "approve" or "pend_for_review"
    override_rationale: str | None = None
    reviewer_name: str
    reviewer_id: str | None = None


class NotificationLetter(BaseModel):
    """Generated notification letter content."""
    authorization_number: str
    letter_type: str  # "approval" or "pend"
    effective_date: str
    expiration_date: str | None = None
    patient_name: str
    provider_name: str
    body_text: str
    appeal_rights: str | None = None
    documentation_deadline: str | None = None
    pdf_base64: str | None = None  # Base64-encoded PDF bytes


class DecisionResponse(BaseModel):
    """POST /api/decision response body."""
    request_id: str
    authorization_number: str
    final_recommendation: str  # "approve" or "pend_for_review"
    decided_by: str
    decided_at: str
    was_overridden: bool
    override_rationale: str | None = None
    original_recommendation: str | None = None
    letter: NotificationLetter
    updated_audit_justification_pdf: str | None = None  # Regenerated audit PDF with override info


class ReviewSummary(BaseModel):
    """Lightweight summary for GET /api/reviews list endpoint."""
    request_id: str
    patient_name: str
    recommendation: str
    confidence_level: str
    reviewed_at: str
    decision_made: bool = False


# --- Per-agent invocation request models ---
# Used by /api/agents/* endpoints for standalone agent evaluation,
# red-teaming, and Foundry Control Plane registration.


class CoverageAgentRequest(BaseModel):
    """Request body for POST /api/agents/coverage.

    Includes the PA request plus clinical findings from a prior
    Clinical Agent run (or test fixtures).
    """
    request: PriorAuthRequest
    clinical_findings: dict = {}


class SynthesisAgentRequest(BaseModel):
    """Request body for POST /api/agents/synthesis.

    Includes the PA request plus all three upstream agent results
    (or test fixtures for evaluation).
    """
    request: PriorAuthRequest
    compliance_result: dict = {}
    clinical_result: dict = {}
    coverage_result: dict = {}
    cpt_validation: dict | None = None
