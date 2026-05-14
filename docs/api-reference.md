# API Reference

## `POST /api/review`

Submit a prior authorization request for multi-agent review.
Returns the complete result as a single JSON response (no streaming).
Prefer `POST /api/review/stream` for the frontend — this endpoint is
useful for programmatic/API integrations that don't need progress updates.

**Request body:**

```json
{
  "patient_name": "John Smith",
  "patient_dob": "1955-03-15",
  "provider_npi": "1234567890",
  "diagnosis_codes": ["M17.11", "M17.12"],
  "procedure_codes": ["27447"],
  "clinical_notes": "Patient presents with bilateral knee OA...",
  "insurance_id": "ABC123456"
}
```

**Response** (top-level synthesis + per-agent breakdown + audit trail):

```json
{
  "request_id": "uuid",
  "recommendation": "approve",
  "confidence": 0.87,
  "confidence_level": "HIGH",
  "summary": "All three agents report clean findings...",
  "tool_results": [...],
  "clinical_rationale": "Gate 1 PASS: Provider NPI active. Gate 2 PASS: All ICD-10 codes valid...",
  "coverage_criteria_met": ["Criterion — evidence"],
  "coverage_criteria_not_met": [],
  "missing_documentation": [],
  "documentation_gaps": [
    {"what": "Prior imaging results", "critical": false, "request": "Please provide X-ray reports"}
  ],
  "policy_references": ["NCD 150.7 — Joint Replacement"],
  "disclaimer": "AI-assisted draft. Coverage policies reflect Medicare LCDs/NCDs only...",
  "agent_results": {
    "compliance": {
      "checklist": [...],
      "overall_status": "complete",
      "missing_items": []
    },
    "clinical": {
      "diagnosis_validation": [{"code": "M17.11", "valid": true, "billable": true, "hierarchy_note": ""}],
      "procedure_validation": [{"code": "27447", "valid": true, "source": "orchestrator_preflight"}],
      "clinical_extraction": {
        "chief_complaint": "...",
        "extraction_confidence": 82
      },
      "literature_support": [...],
      "clinical_trials": [...],
      "clinical_summary": "...",
      "tool_results": [...]
    },
    "coverage": {
      "provider_verification": {"npi": "...", "status": "active"},
      "criteria_assessment": [
        {"criterion": "...", "status": "MET", "confidence": 85, "evidence": [...]}
      ],
      "documentation_gaps": [...]
    }
  },
  "audit_trail": {
    "data_sources": ["CPT/HCPCS Format Validation (Local)", "NPI Registry MCP (NPPES)", "ICD-10 MCP (2026 Code Set)"],
    "review_started": "2026-02-13T10:30:00Z",
    "review_completed": "2026-02-13T10:30:45Z",
    "extraction_confidence": 82,
    "assessment_confidence": 78,
    "criteria_met_count": "4/5"
  }
}
```

**Validation rules** (enforced by Pydantic at request-binding time, before the orchestrator runs — see issue #28):

| Field | Rule |
|---|---|
| `patient_dob` | Must match `YYYY-MM-DD`, parse as a real calendar date (e.g. `2026-02-30` is rejected), and be `≤ today` (no future DOBs). |
| `diagnosis_codes` | At least one non-blank entry required. Each item is trimmed + uppercased, then matched against `^[A-TV-Z][0-9][A-Z0-9](?:\.[A-Z0-9]{1,4})?$` (ICD-10; U-prefix is reserved by WHO and rejected). |
| `procedure_codes` | At least one non-blank entry required. Each item is trimmed + uppercased, then matched against `^([0-9]{4}[0-9A-Z]\|[A-Z][0-9]{4})$` (CPT 5-digit, CPT Cat-III, or HCPCS Level II). |

Codes are normalized server-side (`r91.1` → `R91.1`, `" 27447 "` → `"27447"`) before being passed to the agents.

**Error responses:**

- `422 Unprocessable Entity` — One or more fields failed validation. The response body uses FastAPI's standard envelope:

  ```json
  HTTP/1.1 422 Unprocessable Entity
  Content-Type: application/json

  {
    "detail": [
      {
        "type": "value_error",
        "loc": ["body", "patient_dob"],
        "msg": "Value error, patient_dob must not be in the future",
        "input": "2043-01-01"
      },
      {
        "type": "value_error",
        "loc": ["body", "diagnosis_codes"],
        "msg": "Value error, invalid ICD-10 diagnosis code: 'NOT-A-CODE' (expected format e.g. R91.1, M17.11, J18.9)",
        "input": ["NOT-A-CODE"]
      }
    ]
  }
  ```

  When the API returns 422, the multi-agent pipeline is **not** invoked — Hosted Agent V2 capacity is only consumed by structurally valid requests.

- `500 Internal Server Error` — Multi-agent review failed (e.g., transient Foundry outage, MCP gateway timeout). Body: `{"detail": "Multi-agent review failed: ..."}`.

---

## `POST /api/review/stream`

Submit a prior authorization request with **real-time SSE progress streaming**.
Same request body, **same validation rules**, and same `422` error envelope as
`POST /api/review`. Returns `text/event-stream`.

The frontend uses `fetch` + `ReadableStream` (not `EventSource`, which only
supports GET) to consume this endpoint.

**SSE event types:**

| Event | When | Payload |
|-------|------|---------|
| `progress` | At each phase boundary (9 total) | `{phase, status, progress_pct, message, agents}` |
| `result` | Review complete | Full `ReviewResponse` JSON |
| `error` | Pipeline failure | `{detail: "error message"}` |
| `: keepalive` | Every 2s during long agent runs | SSE comment (ignored by client) |

**Progress event example:**

```json
{
  "phase": "phase_1",
  "status": "running",
  "progress_pct": 10,
  "message": "Running Compliance and Clinical agents in parallel",
  "agents": {
    "compliance": {"status": "running", "detail": "Checking documentation completeness"},
    "clinical": {"status": "running", "detail": "Validating codes and extracting clinical evidence"}
  }
}
```

**Phase IDs:** `preflight` → `phase_1` → `phase_2` → `phase_3` → `phase_4`

**Agent statuses:** `pending` → `running` → `done` | `error`

---

## `GET /health`

Health check endpoint. Returns `{"status": "ok"}`.

---

## `GET /api/review/{request_id}`

Retrieve a previously completed review by its request ID.

**Response:** Same `ReviewResponse` structure as `POST /api/review`.

Returns `404` if the request ID is not found in the review store.

---

## `GET /api/reviews`

List all completed reviews (most recent first).

**Response:**

```json
[
  {
    "request_id": "uuid",
    "patient_name": "John Smith",
    "recommendation": "approve",
    "confidence_level": "HIGH",
    "reviewed_at": "2026-02-13T10:30:45Z",
    "decision_made": false
  }
]
```

---

## `POST /api/decision`

Submit a human reviewer decision (accept or override) for a completed review.
Generates an authorization number and notification letter.

**Request body (accept):**

```json
{
  "request_id": "uuid",
  "action": "accept",
  "reviewer_name": "Dr. Jane Doe"
}
```

**Request body (override):**

```json
{
  "request_id": "uuid",
  "action": "override",
  "override_recommendation": "approve",
  "override_rationale": "Clinical evidence supports approval despite agent uncertainty...",
  "reviewer_name": "Dr. Jane Doe"
}
```

**Response:**

```json
{
  "request_id": "uuid",
  "authorization_number": "PA-20260213-00001",
  "final_recommendation": "approve",
  "decided_by": "Dr. Jane Doe",
  "decided_at": "2026-02-13T11:05:00Z",
  "was_overridden": true,
  "override_rationale": "Clinical evidence supports approval despite agent uncertainty...",
  "original_recommendation": "pend_for_review",
  "letter": {
    "authorization_number": "PA-20260213-00001",
    "letter_type": "approval",
    "effective_date": "2026-02-13",
    "expiration_date": "2026-05-14",
    "patient_name": "John Smith",
    "provider_name": "Dr. ...",
    "body_text": "PRIOR AUTHORIZATION — APPROVED ...",
    "appeal_rights": null,
    "documentation_deadline": null,
    "pdf_base64": "JVBERi0xLjQg..."
  },
  "updated_audit_justification_pdf": "JVBERi0xLjQg..."
}
```

When `was_overridden` is `true`, `override_rationale` and
`original_recommendation` are included. The notification letter contains a
"Clinician Override Notice" section. The `updated_audit_justification_pdf`
contains a regenerated audit PDF with Section 9 ("Clinician Override Record").

**Error responses:**
- `404` — Review not found
- `409` — Decision already recorded for this review
- `422` — Invalid action or missing override fields

---

## Per-Agent Endpoints

These endpoints expose each agent individually for per-agent evaluation,
red-teaming, integration testing, and future microservices migration.
The orchestrator calls the equivalent hosted agent containers directly over HTTP
when running the full pipeline.

All per-agent responses share a common envelope:

```json
{
  "agent": "<agent-name>",
  "started": "2026-02-13T10:30:00Z",
  "completed": "2026-02-13T10:30:12Z",
  "result": { ... }
}
```

### `POST /api/agents/clinical`

Run the **Clinical Reviewer Agent** in isolation. Returns diagnosis validation, clinical extraction, literature support, clinical trials, and clinical summary.

**Request body:** Same `PriorAuthRequest` as `POST /api/review` (same validation rules; returns `422` on invalid input without invoking the agent).

**Response `result`:** Same structure as `agent_results.clinical` in the full review response.

---

### `POST /api/agents/compliance`

Run the **Compliance Validation Agent** in isolation. Returns the compliance checklist, documentation status, and missing items.

**Request body:** Same `PriorAuthRequest` as `POST /api/review` (same validation rules; returns `422` on invalid input without invoking the agent).

**Response `result`:** Same structure as `agent_results.compliance` in the full review response.

---

### `POST /api/agents/coverage`

Run the **Coverage Assessment Agent** in isolation. Requires clinical findings from a prior Clinical Agent run (or test fixtures). The nested `request` object is validated under the same rules as `POST /api/review` (returns `422` on invalid input without invoking the agent).

**Request body:**

```json
{
  "request": {
    "patient_name": "John Smith",
    "patient_dob": "1955-03-15",
    "provider_npi": "1234567890",
    "diagnosis_codes": ["M17.11"],
    "procedure_codes": ["27447"],
    "clinical_notes": "...",
    "insurance_id": "ABC123456"
  },
  "clinical_findings": {
    "diagnosis_validation": [{"code": "M17.11", "valid": true}],
    "clinical_extraction": {"chief_complaint": "bilateral knee OA"}
  }
}
```

**Response `result`:** Same structure as `agent_results.coverage` in the full review response.

---

### `POST /api/agents/synthesis`

Run the **Synthesis Decision Agent** in isolation. Requires all three upstream agent results (or test fixtures). The nested `request` object is validated under the same rules as `POST /api/review` (returns `422` on invalid input without invoking the agent).

**Request body:**

```json
{
  "request": { ... },
  "compliance_result": { ... },
  "clinical_result": { ... },
  "coverage_result": { ... },
  "cpt_validation": null
}
```

**Response `result`:** The final synthesis output (recommendation, confidence, decision gates, rationale).

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `uvicorn` | ASGI server |
| `azure-ai-agentserver` | Microsoft Agent Framework (MAF) SDK |
| `httpx` | Async HTTP client (backend dispatch + MCP transport in agent containers) |
| `fpdf2` | PDF generation for notification letters |
| `pydantic` | Request/response validation + structured output models |
| `react` + `next` | Frontend SPA (Next.js static export) |
| `shadcn/ui` + `tailwindcss` | UI component library + utility-first CSS |
