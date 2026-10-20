# AI Opportunity Advisor — Main Implementation Planner

**Repository:** AbdullahBahmani/AIAdvisory  
**Target file:** main-planner.md (repository root)  
**Plan created:** 2026-10-11  
**Source:** Uploaded AI_Opportunity_Advisor_Blueprint.zip, containing AI_OPPORTUNITY_ADVISOR_BLUEPRINT.md (55 sections).  
**Status (2026-10-20):** Phases 0–8 are implemented on `main` and the local verification that was previously blocked is now done (Python 3.12 lock, clean lint, a passing test suite, drift-free migrations, a booting Docker deployment, a real PostgreSQL 16 drill, and a 31-case evaluation corpus). **Phase 9** (`docs/phase9.md`) has since been delivered: verified reviewer attribution, a decision provenance ledger, evaluation expansion with a 0-of-20 false-positive rate, value sensitivity envelopes, and a ranking calibration harness. The whole suite now runs genuinely against PostgreSQL (432 passed, 3278 statements executed), the Docker deployment builds and boots on its canonical ports, the rate limiter is verified to be enforced globally across replicas, and the UI has been accessibility-audited to zero axe violations.

**Still not verified, and unchanged in substance:** a real IdP/SSO/MFA session, privacy/DPIA/residency and provider-governance approval, encrypted off-site backup with a measured RPO/RTO, managed TLS/WAF ingress, external monitoring and alerting, container image scanning and SBOM, an independent penetration test, a named release approver, field-quality evaluation against real assessments, and browser-level SSO/CSRF end-to-end testing. **The production gate remains CLOSED** and `ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot. No GitHub-hosted runners are used or enabled.

> Product rule: **AI assists. Rules enforce. Data informs. Humans decide.** A successful result can be traditional software, a hybrid solution, more discovery, or no project—not necessarily AI.

## 0. Repository inspection and assumptions

- Inspected the current default branch, **main**. The repository currently contains only a minimal README.md ("# AIAdvisory"). There is no application, package configuration, schema, tests, CI, existing planner, or infrastructure to migrate.
- Treat this as a **greenfield implementation** and build in this repository. Do not claim any feature is already implemented.
- The uploaded ZIP is a **design specification, not runnable source code**; its suggested code layout and 30-day schedule are a starting point, not existing implementation.
- This plan intentionally chooses the blueprint's fast-to-demo stack: **Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite for local development, Streamlit UI, pytest, Docker**. Use PostgreSQL for multi-user/production use. Confirm exact dependency versions when implementation starts and lock them.
- Favor a **modular monolith**, not microservices, agent frameworks, complex orchestration, or cloud services before their need is demonstrated. The Streamlit UI calls FastAPI; only the backend accesses the database and AI provider.
- Initial delivery is a **local or access-controlled portfolio MVP**. Do not expose sensitive real business data or a public multi-user deployment before authentication, tenant isolation, retention, and provider governance controls are implemented.
- The blueprint's scoring weights and policies are **initial hypotheses**, to be versioned and tested with representative assessments before treating recommendations as reliable.

## 1. Product objective and non-negotiable behavior

Build a decision-support application for a Forward Deployed Engineer (FDE) to:
1. Record business context and stakeholder discovery answers.
2. Map a workflow into individual steps and atomic tasks.
3. Track known facts, unknowns, assumptions, contradictions, and evidence needed.
4. Run deterministic suitability, data, value, feasibility, and risk analysis **per task**.
5. Compare AI, deterministic software, hybrid, human-approved, human-led, or more-discovery options.
6. Propose a simplest-adequate architecture, test plan, and bounded PoC.
7. Explain recommendation rationale, evidence gaps, AI/non-AI boundaries, and risk controls.
8. Allow a human FDE to challenge and override advisory conclusions with a documented reason.
9. Record the signed-off final decision and export a complete, clearly labeled report.

**Hard constraints:**
- An LLM never makes a final FDE decision, authorizes a rollout, bypasses required approval, executes irreversible actions, or invents measurements.
- Deterministic rules run independently of the LLM and take precedence. A reviewer may document a disagreement, but a score override cannot silently turn off hard safety controls.
- Unknown data stays unknown: no fabricated cost, ROI, error rates, data access, evidence, or success rates.
- Every recommendation should show **why**, **what AI may do**, **what conventional code must do**, **what humans approve**, and **what evidence is missing**.
- Prefer conventional software for known rules, exact arithmetic, system-of-record truth, and predictable sequences. A tool-using agent is a later option only if dynamic planning is actually needed.

## 2. Scope and release boundaries

> **Reconciled 2026-10-20.** The checkboxes below were written as a scope statement before any
> code existed and were never updated as the phases landed. They are now marked against the
> delivered code. A checked box here means *the capability exists in this repository*; it does
> **not** mean the capability is approved for production use, and it does not close any §10
> release gate.

### MVP / required
- [x] Assessment creation, editing, listing, and workflow status.
- [x] Structured intake and conditional, guided discovery questions.
- [x] Workflow builder with ordered steps and input/output/owner/system metadata.
- [x] Per-step task decomposition, initially manual with AI suggestions later.
- [x] Fact / assumption / unknown / contradiction / evidence tracking.
- [x] Versioned deterministic policy checks and reproducible explainable scoring.
- [x] AI suitability, business value, technical feasibility, data readiness, risk, measurability, and recommendation results, with incomplete-data indicators. *Measurability is surfaced through the `clear_kpi` signal and the evaluation/PoC proposals in the report, not as a separately scored dimension.*
- [x] Six recommendation categories: traditional software; strong AI candidate; hybrid; AI + human approval; human-led / do not automate; more discovery required.
- [x] Risk flags, required controls, and an explicit approval matrix. *The three-stage approval chain is Phase 7; the action-category policy matrix is in `app/services/rule_engine.py`.*
- [x] Optional structured AI discovery and analysis with a useful rules-only fallback.
- [x] FDE challenge/review, reasoned overrides, and final human decision.
- [x] Full Markdown or HTML assessment report export with evidence and caveats.

### If MVP is stable / next release
- [ ] Architecture-pattern diagrams and detailed PoC planning. *Partially delivered: the report carries a deterministic "simplest adequate starting pattern" table per task and Phase 8 adds PoC plans with KPI targets. **Diagrams** are not delivered.*
- [x] KPI and evaluation-case suggestions, comparison of assessments. *Phase 8: revision-bound PoC KPI observations and the cross-assessment portfolio view.*
- [ ] Richer multi-assessment dashboard and configurable scoring weights. *Partially delivered: the Phase 7 organization dashboard and Phase 8 portfolio dashboard exist. **Configurable** scoring weights are not implemented — the weights are a versioned constant in `app/services/scoring.py`.*
- [ ] PDF export and more polished reports. *Not delivered; explicitly deferred. The HTML report can be printed to PDF by the operator, but that is not a validated PDF pipeline.*

### Later / explicitly not MVP
- [ ] Organization-wide prioritization, external CRM/ERP connectors, automated ingestion, enterprise SSO, multi-FDE collaboration, historic benchmarks, agentic execution, and real-world transactional actions.
- [ ] Production rollout authorization by the software itself (never a feature).

## 3. Proposed technical architecture

**Flow:** Streamlit UI → versioned FastAPI REST API → assessment orchestration → deterministic policy + scoring + risk services → *optional* structured AI assistance → validated recommendation → FDE review → report.

**Storage:** SQLAlchemy models, Alembic migrations, SQLite locally; PostgreSQL before genuine multi-user deployment. Keep services independent of persistence and provider SDK choices.

**Packages and responsibilities:**
- **app/api/**: HTTP request/response validation, routes, auth boundaries, error translation.
- **app/domain/**: domain enums, Pydantic contracts, rules and assessment state.
- **app/services/**: question selection, missing information, policy gates, scoring, ROI, risks, solution-pattern selection, report rendering.
- **app/ai/**: provider interface, prompts, structured schemas, response validation, AI-result provenance, request limits; disabled by default unless configured.
- **app/db/**: SQLAlchemy tables, sessions, repositories, migrations.
- **ui/**: Streamlit wizard and report/review pages; no direct SQL or AI keys.
- **tests/**: rules/unit tests, API and persistence integration tests, LLM mocks, golden test cases and evaluation fixtures.
- **docs/**: blueprint copy/reference, architecture, security, evaluation, and ADRs.

### Target repository structure (create incrementally, not all empty at once)

    README.md
    main-planner.md
    .gitignore
    .env.example
    .github/workflows/ci.yml
    pyproject.toml
    uv.lock (or another explicitly chosen lock file)
    Dockerfile
    compose.yaml
    app/
      main.py
      api/{assessments,discovery,workflows,analysis,reviews,reports}.py
      domain/{schemas,enums,policies}.py
      services/{question_selector,missing_info,scoring,rule_engine,risk,value,recommendation,reporting}.py
      ai/{client,schemas,discovery,workflow,risk,architecture,challenge}.py
      prompts/{discovery,workflow_analysis,risk_analysis,architecture,evaluation,challenge}.md
      db/{base,models,session,repositories}.py
    ui/streamlit_app.py
    migrations/
    tests/{unit,integration,evals,fixtures}/
    docs/{blueprint,architecture,security,evaluation,decisions}/

## 4. Core domain model and storage

Model everything needed for **per-workflow-step decisions**, not a single opaque assessment score:

| Entity | Important fields / relationships |
| --- | --- |
| Organization | id, name, industry, region, regulatory context |
| User / membership (before multi-user release) | org_id, user_id, role: FDE/reviewer/stakeholder/admin |
| Assessment | id, organization_id, owner, title, status, timestamps, version |
| Workflow | id, assessment_id, trigger, outcome, frequency, baseline metrics |
| WorkflowStep | id, workflow_id, order, name, owner, system, input/output type, decision type, duration, errors, automation status, action reversibility |
| Task | id, workflow_step_id, description, dependencies, risk and suitability signals |
| Question / Answer | question key, category, condition, answer type, required flag; answer value, source, confidence, timestamp |
| Finding | assessment/task scope, type (fact/unknown/assumption/contradiction/evidence_needed), text, status, provenance |
| Evidence | assessment/task scope, evidence level (0–5), description, source, reviewed status |
| ScoreResult | task_id, dimension, 0–100 or not-assessable status, input snapshot, version, per-factor contributions, calculation time |
| Risk / Control | category, likelihood 1–5, impact 1–5, score 1–25, control, approval requirement |
| Recommendation | task_id, one of six categories, solution pattern, alternatives, rationale, required human gates, next action |
| AnalysisRun | model, prompt/schema/rules version, request metadata, redacted input reference, validated structured output, errors/cost |
| FDEReview / Decision | task/assessment, selected decision, reviewer, rationale, override reason, date, evidence references |
| AuditEvent | actor, organization, assessment, action, timestamp, diff/metadata |

Implementation notes:
- Use UUID identifiers, UTC timestamps, enum/field constraints, foreign keys, indexes, and migrations.
- Represent missing metrics with **nullable values plus a completeness state**, never substitute zero without a real observation.
- Store AI recommendations separately from authoritative deterministic results. Do not let AI output write final scores or mark the decision as approved.
- Freeze or version the analysis inputs so exported recommendations can be reproduced; mark analyses stale if an answer, workflow step, policy, or weight changes.
- Keep audit events append-only. Before multi-user deployment, enforce **organization scope on every query**, including reports and exports.

## 5. Decision engine specification — implement before AI

### 5.1 Rules and hard policy gates

Implement named, unit-tested rules returning machine-readable rule ID, trigger inputs, reason, severity, and required control:

- **DET-001:** Fully deterministic task with known stable rules → prefer conventional code.
- **MATH-001:** Exact calculations → deterministic calculator/typed code; no LLM as numeric source of truth.
- **DATA-001:** Required authoritative records → validated source-system/API lookup; do not guess database facts.
- **SAFE-001:** Zero-error tolerance with no verification → prohibit autonomous AI.
- **ACT-001:** High-risk irreversible or consequential actions → require explicit human/policy approval and safe fallback.
- **VALUE-001:** Value below a configured evidenced threshold → no-project recommendation may be preferable.
- **PRIV-001:** Data sensitivity/provider permissions unknown → block sending that information to an external model until reviewed.
- **EVID-001:** Critical inputs missing → more discovery required; no definitive ROI/feasibility claims.

These rule IDs were proposed implementation identifiers before any code existed. **They are now
real code** — see the implemented catalogue below. Override UI may capture the FDE's rationale,
but **hard approval, privacy, and authorization gates stay enforced**.

#### Implemented rule catalogue (reconciled 2026-10-20)

The implementation carries **13** rules, not the 8 originally listed above. The five extra rules
were added during implementation and are part of the enforced precedence. Every rule below is
emitted by `app/services/rule_engine.py`, appears in the analysis snapshot, is rendered in the
report, and is counted by the evaluation runner's per-rule firing rates.

| Rule | Severity | Trigger | Effect on the recommendation |
| --- | --- | --- | --- |
| `HUM-001` | `HUMAN_ONLY` | Legal commitment, employment decision or medical decision | Human-led. Highest precedence — nothing overrides it. |
| `PRIV-002` | `BLOCK_PROVIDER_DATA` | The workflow has no permission to use the data | More discovery. Second precedence. |
| `SAFE-001` | `BLOCK_AUTONOMOUS_AI` | Zero-error tolerance with no independent verification | Human-led unless a reviewer is available. |
| `EVID-001` | `NEED_EVIDENCE` | High consequence with unknown human-review availability | More discovery. |
| `EVID-002` | `NEED_EVIDENCE` | Task input characteristics are entirely unknown | More discovery. |
| `EVID-003` | `NEED_EVIDENCE` | No reliable task data available | More discovery. |
| `PRIV-001` | `BLOCK_PROVIDER_DATA` | Sensitive data and provider permission not proven | More discovery. |
| `ACT-001` | `REQUIRE_APPROVAL` | Irreversible **and** high-consequence action | Human-led without a reviewer; more discovery if the reviewer is unknown; AI + human approval otherwise. |
| `ACT-002` | `REQUIRE_APPROVAL` | Approval-required business action (modify customer record, cancel order, issue refund, transfer money, delete data) | Same precedence as `ACT-001`. |
| `VALUE-001` | `PREFERENCE` | Modelled net savings below the evidenced threshold | Human-led (no-project may be preferable). |
| `DET-001` | `PREFERENCE` | Fully deterministic task with known stable rules | Traditional software. |
| `MATH-001` | `PREFERENCE` | Exact arithmetic | Traditional software. |
| `DATA-001` | `PREFERENCE` | Requires an authoritative lookup | Supports the hybrid pattern (AI interprets, code retrieves facts). |

**On overriding.** An FDE may record a review that departs from the recommended category, but
only with an explicit `override_reason` (enforced by `validate_review_category`). That override
changes the *recorded category*; it does not remove the rule hit, does not remove the required
controls, and does not bypass the approval chain. The report keeps showing the rule hits and
the controls the task still needs, so a reviewer who overrides a privacy or approval gate sees
exactly which gate they are overriding.

### 5.2 Scoring and explainability

- **AI suitability (0–100):** Begin with the blueprint's explicit positive/negative signal weights, recording each observed signal and its source. Positive examples: unstructured text +15, extraction/classification +10, semantic search +10, verification +10; negative examples: fully deterministic −20, exact arithmetic −15, no reliable data −15, high consequence without review −25. Clamp summed weight to 0–100; track coverage and return **insufficient evidence** where appropriate. Score is advisory, never a green light.
- **Business value:** Record volume, baseline minutes/case, loaded labor cost, target minutes/case, rework, quality and other known impacts. Derive potential labor saving as (baseline minutes − target minutes) × cases × hourly cost / 60; calculate operating costs separately, show units/time period, assumptions and sensitivity range. A forecast is not validated ROI.
- **Technical feasibility:** APIs, auth, rate limits, latency, deployment, environment, system ownership, test access, and integration complexity; use a documented normalized rubric.
- **Data readiness:** availability, quality, accessibility, permission, and governance; do not treat missing permission as a favorable score.
- **Risk:** Score impact × likelihood on 1–5 scales with category (business, safety, legal, privacy, security, operations). Provisional bands: 1–4 low, 5–9 medium, 10–16 high, 17–25 critical. Validate bands with fixtures; risk policy gates override permissive scores.
- **Measurability:** whether baseline, ground truth, KPI, evaluation examples, and success threshold exist.
- **Recommendation:** deterministic precedence: prohibited/approval gates first → missing critical evidence → deterministic suitability/value → hybrid/AI candidate as appropriate → recommend minimal viable pattern. Never derive solely from one numeric score.
- **Evidence:** Mark facts, estimates, assumptions, AI suggestions, evidence level 0–5, and confidence separately. Explain every factor and unknown in API and UI.

### 5.3 Recommendation and pattern catalog

Six outcome categories from the blueprint:
1. Traditional software preferred
2. Strong AI candidate
3. Hybrid system recommended
4. AI + human approval
5. Human-led / do not automate
6. More discovery required

Supported proposed patterns: classification, extraction, RAG with citations, AI + deterministic rules, tool-using agent (only after proving dynamic need), AI + human approval. Use an ordinary state machine/workflow before considering an agent.

### 5.4 Approval policy defaults

- Knowledge search, summarization, and draft creation: can be automatic within data permissions.
- Reads of customer data: permission-scoped.
- Customer-record edits, order cancellations, refunds, deletions, high-impact financial actions: policy gate and/or human approval.
- Legal commitments, hiring/firing, diagnosis/treatment decisions: human-led.
- The MVP **analyzes these scenarios**; it does not actually execute external business actions.

## 6. API contract plan (prefix /api/v1)

- **Assessments:** POST /assessments; GET /assessments; GET /assessments/{id}; PATCH /assessments/{id}
- **Discovery:** GET /assessments/{id}/next-question; POST /assessments/{id}/answers; GET /assessments/{id}/missing-information
- **Workflows:** POST /assessments/{id}/workflows; GET /workflows/{id}; POST /workflows/{id}/steps; PATCH /workflow-steps/{id}; POST /workflow-steps/{id}/tasks
- **Analysis:** POST /assessments/{id}/analyze; GET /assessments/{id}/scores; GET /assessments/{id}/risks; GET /assessments/{id}/recommendations
- **FDE:** POST /assessments/{id}/fde-review; POST /assessments/{id}/final-decision
- **Reporting:** GET /assessments/{id}/report (format markdown/html; PDF later)
- **Health:** GET /health

For every mutating endpoint: validate payload, enforce authorization when enabled, use transactions, capture audit event, return stable error codes and idempotence/duplicate protections where needed. Analysis endpoints return individual task results and warnings, not just a single workflow conclusion. Define OpenAPI schemas and contract tests.

## 7. AI integration plan (after functioning rules-only journey)

- Provider SDK behind an interface; configuration per environment and explicit opt-in for allowed data. Offer a **no-API-key rules-only mode**.
- Separate, versioned prompts for discovery, workflow decomposition, risk identification, architecture recommendations, evaluation planning, and FDE challenge.
- Use Pydantic validated structured JSON for all model outputs; strict enums, maximum sizes, required fields, request timeouts, retry limits, and graceful failures.
- **Discovery:** choose one nonrepeated, information-gaining question based on conditionals; surface conflicting answers and missing inputs.
- **Workflow:** suggest atomic steps, but mark them proposed until FDE accepts them; never overwrite hand-entered workflow mappings.
- **Risk:** propose risks and safeguards, not legal determinations or silent policy changes.
- **Architecture:** explain why traditional workflow, extraction, RAG, or hybrid beats an agent when simpler.
- **Challenge:** present the strongest arguments against, weakest assumptions, simpler alternative, and evidence that could reverse the conclusion.
- Test adversarial answers and uploaded text for prompt injection; treat all such text as untrusted and never execute tool instructions found inside it.
- Record provider, model, prompt/schema version, token usage, cost estimates, latency, failure reason, and safe/redacted provenance. Redact secrets/PII in logs. Do not send a private assessment to a provider if policy, retention, residency, or authorization is unresolved.

## 8. UI / user journey

Implement a staged Streamlit app; every page reads/writes through FastAPI:
1. **Dashboard** — assessments, status, owner, last update, resume.
2. **New assessment** — organization/problem, stakeholders, baseline volume and effort.
3. **Guided discovery** — conditional question, answer provenance, known/unknown/assumption tracker.
4. **Workflow builder** — reorder/add/edit steps, decision type, data type, systems, risks, time, errors; map tasks.
5. **Analysis** — per-task scores, explanations, hard-rule badges in the interface, data completeness, risk matrix and controls.
6. **AI/non-AI boundary** — what LLM does vs code vs human; display simpler alternatives.
7. **Recommendation & PoC** — solution pattern, open questions, evaluation metrics and go/no-go gates.
8. **Ask advisor / challenge** — optional guided challenges, not a free pass to bypass rules.
9. **Final FDE decision** — decision category, evidence, rationale, required controls, override reasons, export.

Recommended local assessment state transitions: **draft → discovering → analyzed → in_review → finalized**, with explicit **reopen/reassess** action. Changes after analysis invalidate stale derived recommendations.

## 9. Test, evaluation, security and release quality

**Deterministic unit tests** (no model or network): policy ordering; score factors/clamping and missing-data states; risk multiplication/bands; exact arithmetic handling; no-project outcome; mutually conflicting inputs; conditional question selection; unauthorized state changes; override audit.

**API/integration tests:** assessment and workflow CRUD, persistence across restarts, migrations, malformed payloads, status transitions, stale analysis detection, report generation, failure recovery.

**AI tests:** mocked provider success/timeout/bad JSON, schema rejection, prompt-injection resistance, missing-evidence refusal, no unauthorized write action, consistent separation of advisory vs enforced controls. Golden fixture tests detect prompt/schema regressions.

**Three mandatory end-to-end demo cases:**
- **A / AI:** customer-support knowledge workflow with unstructured content and verifiable responses.
- **B / non-AI:** exact, stable pricing calculation where deterministic code is preferred.
- **C / hybrid:** refund/customer-email workflow where AI extracts language, deterministic rules decide policy, humans approve consequential actions.

**Security gates before public/real-data use:** authenticated sessions; RBAC; strict tenant isolation; protected secrets/env; TLS; input and payload limits; audit trails; data retention and deletion procedures; no secrets in prompts; log redaction; external-model data review; rate limits; dependency scanning; backup and recovery; tests for cross-tenant leakage. Public deployment is blocked until these are demonstrated.

**CI gates:** formatting/linting (Ruff), type checks where practical, pytest unit+API suite, dependency vulnerability checks and build smoke test. Keep AI/live-provider tests separate from offline CI. Capture decisions in docs/decisions.

## 10. Incremental implementation milestones and task order

Each checkbox is a trackable task; update this section as implementation progresses. **Do not mark completed until code, tests, and acceptance evidence are present.**

### Phase 0 — Repository and technical foundation (days 1–2)
- [x] P0-01 Copy/normalize the uploaded blueprint into docs/blueprint.md for in-repo reference; preserve attribution to original uploaded document and version.
- [x] P0-02 Replace placeholder README with scope, architecture, safety principle, local start, development workflow, and feature status.
- [x] P0-03 Initialize Python project and dependency lock, package modules, Ruff, pytest, .gitignore, .env.example; keep secrets out of version control. **Verified 2026-10-20:** a `uv.lock` resolved against CPython 3.12 is committed (60 packages); `uv sync --extra dev --extra postgres` reproduces the environment; `ruff check .` and `ruff format --check .` are clean repo-wide (0 findings, 102 files formatted) with the FastAPI `Depends()` idiom and pytest fixture shadowing configured as intentional per-file ignores; `python -m pytest -q` passes 148 tests. The Docker image installs from the same lock with `uv sync --locked`, so a pyproject/lock mismatch fails the build.
- [x] P0-04 Add FastAPI health endpoint, Streamlit smoke page and local run/test commands. **Owner-directed change:** remove GitHub-hosted CI workflow; use local tests instead.
- [x] P0-05 Record architecture ADR (Streamlit/FastAPI/SQLite initially; modular monolith; optional LLM); write a short threat-model draft.

**Exit verification (2026-10-11):** Local Python 3.13 unit/API tests, module compilation and live FastAPI smoke were successful. Owner requested **no GitHub Actions runners**, so `.github/workflows/ci.yml` is removed and CI success is explicitly **not a prerequisite in this repository's current workflow**. The original P0-04 hosted-CI deliverable is superseded by documented local `python -m pytest` / compileall checks. A Python 3.12-resolved dependency lock, Ruff run, and full Streamlit runtime smoke remain unverified due to offline tool availability. Finish those checks locally when dependencies are accessible before production use.

### Phase 1 — Deterministic assessment engine (days 3–7)
- [x] P1-01 Define typed schemas/enums for organizations, assessments, workflows, tasks, six categories, questions, answers, evidence, risks, controls, scores, decisions.
- [x] P1-02 Build intake question bank and conditional branching logic; required fields and source/confidence model.
- [x] P1-03 Implement workflow-step/task decomposition input and validation (manual first).
- [x] P1-04 Build missing-information engine: known, unknown, assumption, contradiction, evidence needed.
- [x] P1-05 Implement deterministic hard-rule engine with traceable rule IDs and structured results.
- [x] P1-06 Implement factor-based scoring, technical/data/value rubrics, ROI calculator and risk × likelihood matrix; preserve unknowns.
- [x] P1-07 Implement six-category recommendation engine with pattern choice, alternatives, rationale and approvals.
- [x] P1-08 Test all rules and the three contrastive fixture cases without any external AI call.

**Exit:** Pure Python engine gives reproducible per-task verdicts, sensible no-AI decisions, all active controls and missing-evidence warnings.

### Phase 2 — Persistence, APIs, and working UI (days 8–14)
- [x] P2-01 Add SQLAlchemy/Alembic schema and test migrations; SQLite local DB, portable design for PostgreSQL.
- [x] P2-02 Add repositories and service layer, data consistency/ownership checks and append-only audit events.
- [x] P2-03 Implement assessment/workflow/task CRUD APIs and OpenAPI request/response contracts.
- [x] P2-04 Implement answers/next-question/missing-information APIs.
- [x] P2-05 Implement analyze/scores/risks/recommendations APIs with stale-input handling.
- [x] P2-06 Build Streamlit intake + guided discovery + workflow builder + per-task analysis screens.
- [x] P2-07 Add final-decision/review endpoints and UI with required override rationale; lock final sign-off to an authorized FDE before multi-user use. **Verified 2026-10-20 (ADR-0014):** the audit actor is now always the **server-side principal**, never a client-supplied `reviewer_id`; a typed reviewer name is recorded separately as an explicitly unverified `claimed_actor`. Local/demo mode resolves a **named, organization-scoped principal** with a verified subject, organization and role, so tenant scoping applies in local mode too and the Phase 7 approval chain can require **distinct** actors. The shared `ADVISORY_REVIEW_TOKEN` survives only as an explicitly-labelled synthetic fallback whose records stay `identity_unverified`; it is never a path to a verified identity. New Alembic revision `0009_local_attribution` verified on SQLite and PostgreSQL 16 with a working downgrade. The dead `require_local_reviewer` helper was removed so there is exactly one reviewer gate. **Still open (tracked under P5-01/P7-09):** a stored credential proves possession of a secret, not human identity — no MFA, no IdP round trip, no session binding.
- [x] P2-08 Add offline integration test for save → reload → analyze → review → final decision.

**Exit (local demo verified on 2026-10-11):** Rules-only assessment can be created, edited, persisted, reloaded, analyzed per task, reviewed and locally finalized with an explicitly configured reviewer token. 49 offline tests pass, a fresh SQLite Alembic migration and `alembic check` pass, and live `/health` and `/openapi.json` return 200. **Not production complete:** actual authentication and tenant-scoped authorization, Ruff/dependency lock and Streamlit browser smoke remain open. The shared token is a demo-only gate, never rollout authority.

### Phase 3 — Structured AI assistant (days 15–21)
- [x] P3-01 Implement provider adapter, flags/env, timeouts/cost logging, mock provider and no-provider fallback.
- [x] P3-02 Implement versioned discovery prompt + schema, single next-best question, non-repetition and contradiction signals.
- [x] P3-03 Implement editable workflow-decomposition suggestions with human acceptance.
- [x] P3-04 Implement AI-assisted missing-information/risk suggestions; keep rule classifications and hard controls authoritative.
- [x] P3-05 Implement architecture pattern comparison, agent-vs-workflow rationale, evaluation/PoC suggestion schema.
- [x] P3-06 Implement challenge mode: arguments against, weak assumptions, evidence to change decision, simpler option.
- [x] P3-07 Add provider error handling, retry limits, privacy gating, provenance and adversarial prompt-injection tests.
- [x] P3-08 Validate that complete end-to-end workflow still works with AI **disabled**.

**Exit (local/offline verified 2026-10-11):** All eight Phase 3 implementation task groups are implemented for the **local synthetic-data demo**. Six strict proposal schemas/handlers and Streamlit advisor, default-off provider, per-request consent + 16+ character access token, task-level privacy blocks, 30 successful proposal cap, bounded retries/errors, versioned prompt/schema provenance, revision-based staleness, anti-duplicate exact question checks, contradiction flags, task/step ID validation, policy precedence, optional operator-sourced token-cost estimates, token-guarded explicit human task acceptance and audit event; accepted drafts do not infer any signals/actions and invalidate previous analysis. **72 local automated tests passed**; Alembic 0001→0002→0003 and schema drift check passed. **Remaining verification & production gates:** no live model call or browser-based Streamlit test was performed; semantic paraphrase de-duplication is heuristic/not guaranteed, current provider pricing must be supplied/verified, no authenticated tenancy/DLP/compliance, no resolved Python 3.12 lock/Ruff verification. These limit production readiness but not local Phase 3 implementation.

### Phase 4 — Reports, evaluation, and demo (days 22–27)
- [x] P4-01 Build comprehensive Markdown/HTML assessment report (executive summary, workflow, decomposition, scores, assumptions/evidence, risks, AI/non-AI boundaries, architecture, evaluation, PoC, open questions, final FDE decision).
- [x] P4-02 Add report export UI; PDF optional after the report content is correct.
- [x] P4-03 Add evaluation dataset, gold expectations, outcome metrics and regression runner; measure decision quality and unknown-data handling. **Verified 2026-10-20:** the corpus grew from 4 to **31 synthetic cases** (`dataset_version` 5.1) covering every branch of the recommendation precedence and all 13 rule ids, with each of the six categories exercised at least twice. `python -m app.evaluation` now reports per-category accuracy, a 6×6 confusion matrix, a **false-positive AI-recommendation rate of 0 of 20 non-AI cases**, an unknown-data handling rate of 31/31 (ROI 30/30, feasibility 23/23), per-rule firing rates and advisory-only compliance. The corpus also exposed a real policy defect — a pure system-of-record lookup was recommended as a strong AI candidate — which is fixed in **`rules-v2`** (ADR-0009). **Remaining, tracked under P8-09:** calibration and outcome quality against *real* field assessments still require a consented pilot; a 0.0 false-positive rate over hand-written synthetic cases is a regression guard, not evidence of real-world accuracy.
- [x] P4-04 Add snapshot/golden tests for reports, deterministic verdict explanations and status/override history.
- [x] P4-05 Add Dockerfile and Compose local deployment; verify migrations, health and startup with no AI key. **Verified 2026-10-19 (Docker Engine 29.8.2 / Compose v5.6.0, local synthetic demo only):** the image is built from the committed `uv.lock` via `uv sync --locked`, runs as non-root uid 10001 with a read-only rootfs, `cap_drop ALL` and `no-new-privileges`, and embeds no secret. `docker compose build` + `up -d` bring up the API and the Streamlit UI on loopback only; Alembic applies 0001→0008 at container start against a fresh named volume, `GET /health`, `/ready` and `/openapi.json` return 200, the compose healthchecks report healthy, `ADVISORY_AI_ENABLED=0` with no AI key present and the AI routes answer 503, and a full synthetic create→map→analyze→report flow passes (`python -m scripts.docker_smoke`). `docker compose down -v` followed by `up -d` re-migrates from an empty volume. The production startup guard still refuses to boot (`RuntimeError: Production deployment blocked…`). Caveats: the run used alternate loopback host ports (18000/18501) because another local process held 8000/8501; **this caveat was closed on 2026-10-20** when the committed `compose.yaml` was booted with no port override — both services came up healthy on the canonical `127.0.0.1:8000` and `127.0.0.1:8501`, `/health`, `/ready`, `/openapi.json` and both Streamlit routes returned 200, `python -m scripts.docker_smoke` passed all eleven assertions with `policy_version: rules-v2`, and the container was confirmed as uid 10001 with `CapEff=0`, `NoNewPrivs=1` and no AI key present. Browser-level UI verification, PostgreSQL-in-container, image scanning and load/TLS testing remain unverified. A schema-drift item found during the same run (`alembic check` reported `FLOAT`→`Double` on `answers.confidence`) was **fixed on 2026-10-19** by declaring the column type explicitly in `app/db/models.py`; no schema change was needed and `tests/test_alembic_drift.py` now enforces the gate.
- [x] P4-06 Run and document three complete demos (AI / non-AI / hybrid) showing an FDE decision and export.
- [x] P4-07 Document architectural and evaluation trade-offs, known limitations and screenshots/demo script. **Verified 2026-10-20:** twelve real headless-Chrome screenshots of the running local app (dashboard, guided discovery, workflow builder, per-task analysis with scores/hard rules/risk register, AI-non-AI boundary, recommendation + PoC gate, FDE override review, final decision + audit trail, exported Markdown report, Phase 8 portfolio, API docs) committed under `docs/images/` and embedded with captions and regeneration steps in `docs/demo.md`; trade-offs and known limitations cross-reference `docs/decisions/`, `docs/architecture.md` and `docs/evaluation.md` instead of duplicating them; reproducible commands documented for `python -m scripts.demo` (offline), `python -m scripts.seed_demo_data` (live synthetic seed) and `python -m scripts.capture_screenshots` (capture helper, Playwright/Chrome are documentation-only and not project dependencies). **Still not delivered/verified:** no live model call is shown anywhere (AI advisor tab stays empty by design), the enterprise technical/security/business approval chain cannot be exercised in demo mode (HTTP 403), there is no separate risk-matrix screen (risks are per-task registers), and field-quality/outcome evaluation remains open under P4-03.

**Exit (local demonstration):** Three in-memory persistence/API demo workflows produce Markdown+HTML reports showing AI, traditional software, and human-approved hybrid outcomes. Four offline curated policy fixtures pass. 59 tests on reconstructed Phase 2+new Phase 4 code pass; full merged Phase 3+4 suite, a live Streamlit browser check, and operational/real-data evaluation are **still pending** (the Docker/Compose local deployment was verified separately on 2026-10-19, see P4-05), so Phase 4 is not production validated.

### Phase 5 — Production-readiness gate (days 28–30 for assessment; hardening may take longer)
- [ ] P5-01 Add authentication, RBAC and tenant isolation before inviting real users; authorization tests for every data/export route. **Partial:** API credentials, RBAC, tenant checks and credential-bound review logs done; **the credential and membership lifecycle is now implemented (2026-10-20)** — expiry is mandatory and enforced in the strict direction (a credential is refused 30 seconds *before* its stated expiry, never after, and there is no grace period); a credential with no expiry requires an explicit `--unlimited` opt-in with a stated reason and is labelled UNLIMITED wherever it is listed; rotation retires the previous credential first and issues the replacement in the same transaction, so a failure mid-rotation never leaves two valid credentials; a live credential whose role exceeds the holder's effective active membership role is revoked, and a reviewer assignment is CLEARED whenever the assigned reviewer can no longer act, which closes the risk of an assessment waiting forever for a review nobody can perform; a revocation that would leave an organization without an administrator is refused unless the operator explicitly overrides, and the override is audited. New operator commands: `sweep-membership-lifecycle` (reports by default, acts only with `--execute`) and `access-review` (the human-readable artefact an auditor asks for). OIDC signed JWTs use pinned issuer/audience/JWKS and locally provisioned organization membership, and OIDC-only mode is available. **Still open:** a real IdP session, MFA, and IdP-driven de-provisioning — a stored credential proves possession of a secret, not human identity.
- [ ] P5-02 Confirm external-model policy, privacy, residency, deletion, retention, safe logging and model-provider configuration. **Partial:** provider permission checks retained and secrets not logged; actual DPIA/DLP, residency, contract, retention and deletion workflows pending. **Additional:** offline scoped hard-deletion command (dry-run default) and SQLite online backup. Policy, consent, data residency, legal hold and backup expiry still need independent sign-off.
- [ ] P5-03 Migrate/test PostgreSQL; review indexes, migrations, backup/restore and failure recovery. **Local drill verified 2026-10-20:** against a real `postgres:16-alpine` instance the full 0001→0008 migration chain applies to an empty database with `alembic check` reporting no drift and round-trips through `downgrade base`; optimistic locking yields exactly one winner and a 409 on the losing concurrent HTTP write; `If-Match` returns 428/412/200; `SELECT … FOR UPDATE` verifiably blocks a second writer (which SQLite cannot demonstrate); the rate limiter's `ON CONFLICT DO UPDATE` is atomic under 24 parallel writers; and `pg_dump` → `pg_restore` into a second independent container reproduced identical row counts on 12 key tables with the same API reads. Six concrete index findings from the live catalogue are recorded and being addressed. **Still open for production:** a managed cluster with TLS/`verify-full` and least-privilege accounts, encrypted off-site backups with PITR, retention/rotation/legal-hold, capacity and load testing, failover, a backend-parametrised test suite (the pre-existing tests were SQLite-bound), and failure-recovery drills. The local drill's 0.25 s dump/restore is **not** an RPO/RTO measurement.
- [x] P5-04 Add structured telemetry (request ID, latency, provider, token usage, rule/prompt versions, errors and overrides); never log sensitive raw content. **Verified 2026-10-20:** `GET /metrics` serves the Prometheus text exposition format (written by hand, no new dependency) with request counts and latency by route template and status class, the running policy and prompt versions, per-rule hit counts and **per-rule firing analyses** — so a rule that silently stops firing shows zero, which is the failure the evaluation corpus exists to catch — review and FDE-override counts, AI token totals, and AI cost with an explicit **priced/unpriced split**. The endpoint is **authenticated and tenant-scoped** (401 without a credential), because it exposes tenant-derived aggregates; the demo bypass is not widened. Redaction is enforced in code: no content, no identifiers (the request id is deliberately not a metric), and low-cardinality labels with overflow collapsing to `other`. `scripts/metrics_check.py` turns the failure modes into PASS/WARN/FAIL on a schedule. `scripts/spend_report.py` aggregates provider spend and reports the priced/unpriced split honestly — an unpriced run has **unknown** cost, never an estimate. **Still open:** externally operated dashboards and alert delivery, and a scheduled provider-spend job; the suggested thresholds are documented as suggestions to tune, **not** validated SLOs.
- [ ] P5-05 Add security checks for injection, untrusted evidence, prompt injection, secrets, rate limits and dependency vulnerabilities. **Partial:** cross-tenant, revoked/expired, forged-token, impersonated reviewer, role and report security tests added; **the per-principal rate limiter is now verified to be enforced globally across API replicas** — 2, 3 and 4 real API processes sharing one database admitted exactly the configured capacity (20/25/30) and rate-limited the remainder, which a per-process limiter would have admitted. `alembic check` drift, atomic `ON CONFLICT DO UPDATE` counting under 24 parallel writers, the `SELECT … FOR UPDATE` row lock and JWT negative tests are also done. **A local dependency vulnerability scanner now exists** and scans the committed lock file; it was proven not to be vacuous by running it against a deliberately vulnerable pinned package. **Still open:** an ingress/WAF limit in front of the application for unauthenticated floods, container image scanning and SBOM generation, and an independent penetration test.
- [ ] P5-06 Publish deployment/runbook, evidence-based limitations, known risks and rollback plan. **Partial:** operator runbook and ADR-0005 document credential rotation, containment and rollback/backup plans; no operational drill completed. **Additional:** operator OIDC/membership, backup and retention commands and explicit production startup guard are documented; real incident/restore drills pending.
- [ ] P5-07 Release only when explicit gate checklist below passes; otherwise retain local/demo-only designation. **Blocked:** no approved production deployment; see security gate in `docs/operations.md`. **Still blocked:** deliberate production startup refusal until independent approval; do not interpret merged code as a production launch.

**Exit status:** NOT CLEARED. A server-scoped credential and isolated pilot are not production authentication, privacy compliance or release authorization. Resolve all P5 gates before public or real-data use.

## 11. MVP definition of done — release gate

> **Reconciled 2026-10-20.** These boxes were never ticked as the phases landed. They are now
> marked against the delivered code. The last item is deliberately **unchecked**: the MVP
> *capability* is delivered and demonstrable, but the production-readiness gate is NOT, and a
> checked "definition of done" must never be read as release authorisation.

An FDE can, using the app and not manual DB edits:
- [x] Create and resume an assessment with an identified owner and business problem.
- [x] Map workflow steps and atomic tasks.
- [x] Answer conditional questions and see evidence provenance.
- [x] See missing information and unresolved assumptions/contradictions.
- [x] Receive per-task, explainable AI/non-AI/hybrid/human recommendations and factor scores.
- [x] See explicit risks, policy flags, verification and approval controls.
- [x] Review a simplest-adequate architecture and a testable PoC/evaluation plan.
- [x] Challenge an AI recommendation, review supporting evidence, and compare simpler solutions.
- [x] Override advisory results with rationale, without bypassing non-overridable safeguards.
- [x] Sign and store a final FDE decision; no system-initiated production approval occurs.
- [x] Export a truthful assessment report with facts/estimates/assumptions labeled.
- [x] Demonstrate case A (AI), B (deterministic) and C (hybrid) with passing tests. *`python -m scripts.demo` and the 31-case evaluation corpus cover all three, plus the false-positive and unknown-data regressions.*
- [x] Run offline, without AI provider credentials, and pass CI from a clean clone. *The suite runs offline with no provider key and passes from a clean clone. Hosted CI is absent by owner direction; documented local runs replace it.*
- [ ] For any production use: auth, RBAC, tenant separation, privacy, retention, audit, backup and security checks completed. **STILL OPEN.** Auth, RBAC, tenant separation, audit and backup exist in code, but real IdP/MFA, privacy and residency sign-off, encrypted off-site backup, vulnerability scanning, penetration testing and a named release approver do not. See P5-01…P5-07, P6-06…P6-09 and P7-08…P7-11.

## 12. Project risks and mitigation

| Risk | Mitigation / measurable check |
| --- | --- |
| LLM acts as unreviewed decision-maker | Separate scoring/policy services, hard gates and mandatory FDE signoff |
| Scores imply false certainty | Input provenance, incomplete-data states, factor explanations and calibration fixtures |
| Entire workflow gets one verdict | Per-task scores and recommendations; workflow result is a summary |
| ROI is hallucinated | Typed inputs, dimensional formulas, assumptions and sensitivity bands; never invented baselines |
| Sensitive data sent to model | Provider opt-in and policy checks, redaction, configurable logging/retention |
| High-risk action slips through | Allowlist/approval matrix, test gate precedence; no execution endpoints in MVP |
| Scope creep toward agents/connectors | Use six pattern catalog entries; phase agents and external actions out |
| Sprint timeline underestimates auth and security | Treat days as sequence, not guarantee; production release depends on gates |
| UI and API diverge | OpenAPI contract tests; UI through API only |
| Lack of reliable evaluation | Golden datasets A/B/C, false-positive AI recommendation tests, staged pilot |

## 13. Start-here checklist and sequencing decisions

**Implementation progress (2026-10-20):** Phases 0–8 are implemented on `main` and the local
verification that was previously blocked is now done (Python 3.12 lock, clean lint, 148 passing
tests, drift-free migrations, a booting Docker deployment, a real PostgreSQL drill, and a 31-case
evaluation corpus). Phases 5–8 release gates remain open because they depend on external
evidence — a real IdP, privacy sign-off, off-site encrypted backups, TLS/WAF, scanning, a pen
test, a named approver and field evaluation. No hosted runners. Continue forbidding real or
sensitive data until those gates are satisfied. Phase 9 (`docs/phase9.md`) defines the next
increment: decision attribution, provenance, evaluation expansion, value sensitivity and
ranking calibration.

**Recommended implementation PR order:**
1. Project scaffold and docs
2. Typed domain model and discovery questions
3. Rules, scoring and recommendation engine with fixtures
4. Database migrations and assessment APIs
5. Streamlit end-to-end rules-only assessment
6. Structured AI adapter and guarded prompts
7. FDE review, challenge and reports
8. Evaluation, containerized demo and security hardening

**Open decisions — now recorded as ADRs (2026-10-20).** The planner asked for each to be written
down rather than held implicitly. All four are now decided for the current posture, each with
explicit conditions under which it must be revisited:

| Open decision | Decision | ADR |
| --- | --- | --- |
| Local-only vs authorized pilot audience; hosting environment | Local/synthetic only. A pilot needs a real IdP + MFA, independent access-control verification, a backup/restore drill, privacy sign-off, managed TLS/ingress, monitoring, scanning, a pen test and a named approver. | [ADR-0010](docs/decisions/0010-deployment-audience-and-hosting.md) |
| Model provider and permitted data regions | **No** provider is provisioned or approved; no data leaves the boundary. The provider stays off by default and every permitted region set is empty. | [ADR-0011](docs/decisions/0011-no-external-model-provider.md) |
| Value/score thresholds after reviewing sample assessments | Weights and thresholds stay fixed versioned constants, not operator-tunable. Recalibration requires P8-09 field evidence and an ADR; the synthetic corpus may check for regressions but may not justify a change. | [ADR-0012](docs/decisions/0012-value-and-score-thresholds.md) |
| Whether PDF is MVP-critical | It is not. Markdown and HTML remain the supported exports; HTML-to-PDF is an operator convenience, not a validated pipeline. | [ADR-0013](docs/decisions/0013-pdf-export-not-mvp-critical.md) |

**Still genuinely open (requires the owner, not the code):** value and score thresholds *after*
real sample assessments exist, the identity provider and hosting environment for an authorised
pilot, and every Phase 5–8 release gate listed above.

**Progress convention:** Change a box to checked only after merging implementation and verifying its exit criteria. Update the status line, blockers, decisions, and evidence links as work progresses. This planning commit does **not** claim any application code was built.

---
**Source-of-truth principle:** When the blueprint and implementation details differ, preserve the blueprint's safety and human-accountability principles; document deliberate trade-offs in docs/decisions and revise this planner accordingly.


## Implementation handoff / independent release acceptance — 2026-10-13

The application code is consolidated on the repository's `main` branch; no PR or runner is required. The new OIDC flow, SQL limiter, SQLite backup and tenant-scoped retention operations have locally executed unit/API tests. The API deliberately **refuses** the `production` deployment mode. **Do not mark Phase 5 P5-07 complete** until the organization provides a real IdP + MFA, independent access-control verification, a deployed PostgreSQL backup/restore drill, DPA/DLP/residency/retention approval, managed TLS/ingress rate limits, external monitoring, vulnerability scanning/penetration test, Python 3.12 lock, Ruff/full AI suite, and live Docker/Streamlit verification. See `docs/release-verification.md` for executable local checks and evidence requests. Updating a checkbox requires verifiable evidence, not merely a source-code commit.


## Phase 6 — enterprise production foundation (2026-10-18)

**Source code delivered (subject to local verification):**
- [x] P6-01 Add optimistic assessment row locking and migration 0006; detect
  stale simultaneous writes and respond 409. Authenticated pilot mutations
  require `If-Match` assessment revision (428 missing, 412 stale).
- [x] P6-02 Strengthen pilot boot: OIDC-only, PostgreSQL and `sslmode=verify-full`.
  Keep production mode deliberately blocked.
- [x] P6-03 Add schema-aware read-only `/ready`, separate from `/health`.
- [x] P6-04 Add conservative sensitive-context checks to the optional AI path;
  this does not provide complete DLP or permission to process real personal data.
- [x] P6-05 Add offline regression tests and a release-gate inventory.

**External work remaining (production gate CLOSED):**
- [ ] P6-06 Run full merged tests, lint and dependency lock verification, and
  authenticated browser SSO/MFA/CSRF tests on the *actual* integrated deployment.
  **Local half verified 2026-10-20:** the full merged suite passes on Python 3.12
  (148 tests), `ruff check` and `ruff format --check` are clean repo-wide, the
  dependency lock is committed and reproduced by both `uv sync` and the Docker
  build, and the migration chain applies with no drift. **Still open:** authenticated
  browser SSO/MFA/CSRF testing against a real IdP on the integrated deployment.
- [ ] P6-07 Test production PostgreSQL isolation, concurrent writes, schema
  upgrades, load, off-site encrypted backups and timed restore (RPO/RTO).
  **Local half verified 2026-10-20:** against a real PostgreSQL 16 instance the
  migration chain applies to an empty database with no drift and round-trips
  through `downgrade base`; optimistic locking yields exactly one winner with a
  409 on the loser; `If-Match` returns 428/412/200; `SELECT … FOR UPDATE`
  verifiably blocks a second writer; the rate limiter is atomic under parallel
  writers and is enforced globally across 2–4 API replicas; and
  `pg_dump` → `pg_restore` into a second independent container reproduced
  identical row counts on 12 key tables. A local load check measured 463 rps
  and a 232 ms p95 under 120 concurrent requests across 4 processes.
  **Still open:** production volume and capacity planning, encrypted off-site
  backups with PITR, a measured RPO/RTO, failover, and replication behaviour.
  The local dump/restore timing is **not** an RPO/RTO measurement.
- [ ] P6-08 Provision edge TLS/WAF, metrics/alerts, managed secrets, vulnerability
  scans and independent penetration test, and drill incident response/rollback.
- [ ] P6-09 Approve contracts, DPIA/DPA, residency, deletion, retention, DLP and
  AI model-provider governance; receive accountable written release approval.

**No GitHub-hosted runners are used.** Do not relax `ADVISORY_DEPLOYMENT_MODE=production`
for convenience; missing real-world controls cannot be implemented or
independently attested to by a source-code commit.


## Phase 7 — enterprise product experience (2026-10-18)

**Merged-source implementation (not a claim of verified production readiness):**
- [x] P7-01 Add organization status dashboard, assessment search and status filter,
  workspace navigation and revision/ownership-aware inbox UI.
- [x] P7-02 Introduce backend-validated assigned reviewers from active same-org
  memberships; no self-registered roles or cross-tenant lookups.
- [x] P7-03 Add append-only, author-attributed discussion comments stamped with
  the assessment revision.
- [x] P7-04 Add three ordered approval stages with distinct authenticated actors,
  role gates, audit evidence, and automatic invalidation when inputs change.
- [x] P7-05 Require complete approval chain before protected pilot final signoff;
  preserve existing human FDE and hard-rule controls.
- [x] P7-06 Add in-app notifications, private recipient inbox, read receipts
  and regression test cases for tenant boundaries and approvals.
- [x] P7-07 Add Alembic migration 0007, API/UI documentation and ADR-0007.

**Remaining / outside this source-only increment:**
- [ ] P7-08 Execute complete integrated tests, migration/lint and actual browser
  E2E against an operator-controlled OIDC/PostgreSQL environment.
  **Local half verified 2026-10-20:** the complete integrated suite passes (148
  tests) with clean lint/format and a drift-free migration chain, and 16 opt-in
  tests pass against a real PostgreSQL 16 instance. **Still open:** actual browser
  E2E against an operator-controlled OIDC/PostgreSQL environment.
- [ ] P7-09 Verified real IdP session lifecycle, SCIM, fine-grained security team
  role/approval policies and safe user-facing enterprise login.
  **SCIM and the approval policies are now implemented (2026-10-20):** the
  SCIM 2.0 Users and Groups endpoints with the SCIM content type, list
  envelope and error schema, filtering and pagination; `externalId` mapped to
  the IdP subject; DELETE **deactivates** rather than deletes, because
  deleting an audit-bearing membership would orphan the history that
  references it; the endpoint is authenticated with a dedicated bearer token
  compared in constant time and works in local demo mode **without** widening
  the demo bypass for ordinary user routes; it is scoped to one organization.
  A role is only ever taken from an allowlist (`admin` needs an explicit
  opt-in), SCIM groups grant nothing unless an operator configures a
  group-to-role map, and a provisioned user can never raise their own role.
  The approval policy may **tighten** the defaults but never loosen a hard
  rule: the rules-engine gates are not in the schema at all, and the
  separation-of-duties booleans are hard values a request cannot change; a
  policy change is audited and invalidates the approvals it affected.
  **Still open:** a real IdP session lifecycle, MFA, and a safe user-facing
  enterprise browser login.
- [ ] P7-10 Accessibility and localization evaluation, richer collaboration
  UX, secure evidence attachments, external notification delivery and PDF/Excel.
  **Accessibility, localization, attachments and external notification delivery
  are now implemented (2026-10-20):** an axe-core evaluation of the RENDERED
  DOM across every tab and both sidebar views, in the light and dark themes,
  took 108 affected nodes to **0**; a dependency-free localization layer with
  English and Bokmål catalogues, locale-aware number/currency/date formatting
  that keeps money exact (`Decimal.quantize`, never a float), and a
  byte-identical English report; enterprise-safe evidence attachments stored
  outside the web root under a server-generated opaque name, with the size
  ceiling enforced while streaming and traversal-tested against eleven hostile
  filenames; and external notification delivery over a signed webhook and a
  file outbox, with no person identifier in the payload and full SSRF
  controls. **Still open:** richer collaboration UX, and PDF/Excel export —
  the latter is deliberately deferred, see ADR-0013.
- [ ] P7-11 Legal and operational release signoff (Phase 6 gate still OPEN).

**Status:** Phase 7 experience APIs and UI source are implemented; independent
validation and production launch are NOT complete. No GitHub-hosted runners
are used or enabled. Proceed to Phase 8 portfolio intelligence only after
addressing high-risk verification gaps for any real customer data.


## Phase 8 — AI Opportunity Portfolio Intelligence (2026-10-18)

**Code implementation merged to `main` (verification separately required):**
- [x] P8-01 Build tenant-scoped cross-assessment portfolio with assessment
  status, current evidence coverage, explicit unranked cases and a versioned
  weights-based planning priority heuristic (`portfolio-v1`).
- [x] P8-02 Create a revision-bound evidence registry with source references,
  provenance, independent reviewer attestation and stale-record handling.
- [x] P8-03 Implement exact Decimal-based, immutable labor-value scenarios:
  gross/net/payback, entered currency, period and source of assumptions;
  never convert mixed currencies or claim observed ROI.
- [x] P8-04 Implement human-controlled PoC plans and source-linked KPI
  observations; prevent activity against stale revisions, preserve past results.
- [x] P8-05 Add Streamlit portfolio dashboards and assessment-level evidence,
  scenario and PoC screens.
- [x] P8-06 Add migration 0008 and offline regression tests covering economics,
  ownership, reviewer roles, evidence freshness and PoC lifecycle.
- [x] P8-07 Document arithmetic, incomplete-data semantics, versioned ranking,
  limitations and independent release requirements.

**Unverified / future production acceptance:**
- [ ] P8-08 Execute the full integrated test suite, Alembic drift/lint and
  authenticated UI/DB load scenarios in a controlled environment.
  **Local half verified 2026-10-20:** the full integrated suite passes (148 tests),
  `ruff check`/`ruff format --check` are clean, and `alembic check` reports no drift
  against a database built from the migration chain alone (now enforced by
  `tests/test_alembic_drift.py`). **Still open:** authenticated UI and DB load
  scenarios in a controlled environment.
- [ ] P8-09 Collect privacy-approved, representative field assessments,
  blinded expert outcomes and PoC samples to calibrate ranking and verify
  forecast accuracy, false-positive decisions and business impact.
- [ ] P8-10 Add enterprise-safe evidence attachments, metrics ingestion,
  comprehensive sensitivity/NPV, approved compliance controls and
  cross-currency logic only if explicitly required and correctly sourced.
  **Attachments and sensitivity are now delivered (2026-10-20):** evidence
  attachments are stored outside the web root under a server-generated
  opaque name, with the size ceiling enforced while streaming, an
  allowlisted and magic-byte-verified media type, sanitised display names,
  revision-bound staleness, tenant-scoped downloads that are never served
  inline, and file deletion that removes the blob. The value sensitivity
  envelope reports a band, a net range, an exact breakeven and the
  assumption range that keeps the case above the threshold, with mixed
  currency and mixed period **refused** rather than approximated.
  **Still open:** metrics ingestion from production systems, DLP and
  encryption at rest (a prerequisite decision that has not been made), and
  cross-currency logic, which has no sourced rate and rate date policy.
- [ ] P8-11 Close all outstanding Phase 6/7 identity, privacy, security,
  recovery and independent production-release gates.

**Status:** Phase 8 source delivered; NOT certified, launched, or approved for
personal/regulated business data. No GitHub-hosted runners used.


## Phase 9 — decision attribution, calibration and value maturity (2026-10-20)

Scope is defined in `docs/phase9.md`. Phase 9 closes **attribution and
measurement** gaps. It does not close any Phase 5–8 production gate, and it
does not change the fact that `ADVISORY_DEPLOYMENT_MODE=production` refuses
to boot.

### Delivered

- [x] P9-01 Verified reviewer attribution for local and pilot modes (merged
  with P2-07, ADR-0014). The audit actor is server-derived; a typed name is
  only ever recorded as an unverified claim.
- [x] P9-02 Decision provenance ledger. A finalization writes a
  `provenance.recorded` entry into the **existing** append-only audit trail
  — not into the analysis snapshot, which is cleared on every revision
  change and therefore could not show a stale basis. It captures the policy
  version, the analysis and assessment revisions, a digest of the analysis,
  the contributing evidence and value scenario, the verified reviewer, the
  server-derived decision maker, and whether AI contributed. Drift is
  detected and reported with reason codes, and the entry appears in the
  exported report. No Alembic migration was needed.
- [x] P9-03 Evaluation expansion and outcome metrics. The corpus grew from 4
  to **31 synthetic cases** covering every branch of the recommendation
  precedence and all 13 rule ids, with a confusion matrix, a
  **false-positive AI-recommendation rate**, an unknown-data handling rate,
  per-rule firing rates and advisory-only compliance. It also surfaced a real
  policy defect — a pure system-of-record lookup was recommended as a strong
  AI candidate — which is fixed in **`rules-v2`** (ADR-0009). The corpus
  measured a false-positive rate of **0 of 20** non-AI cases afterwards.
- [x] P9-04 Value sensitivity and scenario comparison. Exact `Decimal`
  arithmetic throughout, breakeven solved in exact rational arithmetic, the
  bounds labelled as a **modelling convention** rather than market data, and
  missing assumptions reported unavailable rather than as zero. Mixed currency
  and mixed period are **refused**, not approximated.
- [x] P9-05 Ranking calibration harness. Kendall's tau-b, implemented without
  a dependency because the heuristic produces integer scores and therefore
  common ties. The policy-blocked guard is enforced inside the harness and
  fails the run loudly. **`portfolio-v2` was deliberately not shipped**: the
  one disagreement the harness surfaces is against a label written in this
  repository, so re-weighting to match would be fitting to our own fixture
  rather than calibrating against expert judgement. The machinery for a
  future version bump is in place.

### Also delivered alongside Phase 9

- [x] P9-08 **Audit integrity and rollback detection.** The failure-recovery
  drill restored a `pg_dump` backup over a live database and found that the
  `fde.finalized` audit event was **silently lost**: the assessment reverted
  from `finalized` to `in_review`, the API still answered 200, and nothing
  said a decision record had disappeared. That is the most serious defect the
  drill found, because the audit trail is the evidence base for the product's
  central claim — *a human decided*. Now: a per-assessment hash chain in the
  database makes an in-place alteration detectable, and a high-water mark held
  **outside** the database — which is exactly what a restore cannot roll back —
  makes a rollback detectable. `GET /assessments/{id}/audit/integrity` reports
  the verdict and the audit history response carries
  `X-Audit-Chain-Intact` and `X-Audit-Rollback-Detected`. The drill now
  observes `integrity_rollback_detected = True` with a marked sequence of 9
  against an actual sequence of 7. A second audit path in `app/ai/service.py`
  was found writing rows directly and is now routed through the same gate, so
  there is genuinely one audit path.
- [x] P9-09 **Dependency-failure behaviour.** A stopped database made route
  handlers answer 500 while the readiness probe answered 503; an unhandled
  exception bypassed the request middleware so replies lost their
  `X-Request-ID` and no request was logged; and with no connect or statement
  timeout a silently partitioned database wedged requests until the pool was
  exhausted. Now: a `SQLAlchemyError` answers 503 with `Retry-After` and says
  the request was **not applied**; the middleware catches an unhandled
  exception, logs it with its traceback and returns a 500 that still carries
  every security header; and the engine sets connect, pool and (on
  PostgreSQL) server-side statement timeouts, all configurable and clamped.
  A dependency failure must never look like a successful write.
- [x] Index and constraint hardening: the missing foreign-key index, three
  composite indexes for the hot tenant-scoped read paths, and a real
  database-level step-order uniqueness constraint. Measured on PostgreSQL:
  the assessment list query went from 1.012 ms with a top-N heapsort over
  5000 rows to 0.032 ms without one, and the inbox query from 0.807 ms to
  0.035 ms.
- [ ] P9-07 Calibrate `portfolio-v1` against real expert judgements. This
  depends entirely on P8-09 and cannot be closed with synthetic labels.

### Also delivered alongside Phase 9

- [x] A backend-parametrised test suite: the existing tests now genuinely
  execute against PostgreSQL (**529 passed, 3278+ statements executed**)
  instead of silently running on SQLite, with a guard that fails the run if
  zero statements were executed.
- [x] A local failure-recovery drill exercising crash mid-write, restore
  into a running service, audit-history survival, an unreachable database
  and a write failure. It surfaced five real application defects, which are
  recorded and tracked rather than silently patched.
- [x] An accessibility evaluation of the running UI with axe-core against
  the rendered DOM in both themes: **108 affected nodes → 0**. No WCAG
  conformance is claimed.
- [x] A single local release-gate command (`python -m scripts.verify_all`),
  which is what hosted CI would otherwise have provided.

**Status:** Phase 9 source delivered and verified locally. **NOT certified,
launched, or approved for personal or regulated business data.** The
production startup guard remains closed. No GitHub-hosted runners are used.

The local failure-recovery drill is the sharpest instrument here. After the
audit-integrity and dependency-failure work, a re-run reports **crash-mid-write,
restore-into-running-service, dependency-failure and disk-full all PASS**, and
states that *no scenario produced corrupting or misleading behaviour*. The one
remaining finding is the underlying storage limitation, now stated accurately:
the audit trail still lives in the same database as the data it describes, so
anything that can roll the database back can roll the trail back — but the
rollback is now **detected and reported** rather than silent, and a production
deployment needs the mark in an external append-only sink.

Three findings the drill reported earlier turned out to be defects **in the
drill**, and were corrected: a hard-coded migration head that made it report
"data was lost" whenever the schema did not match a stale constant, a
case-sensitive header comparison that reported headers which were demonstrably
present as missing, and an audit-history check that did not know about the
rollback detection. Correcting a tool that reports false findings is as
important as fixing the defects it found.
