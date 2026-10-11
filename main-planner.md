# AI Opportunity Advisor — Main Implementation Planner

**Repository:** AbdullahBahmani/AIAdvisory  
**Target file:** main-planner.md (repository root)  
**Plan created:** 2026-10-11  
**Source:** Uploaded AI_Opportunity_Advisor_Blueprint.zip, containing AI_OPPORTUNITY_ADVISOR_BLUEPRINT.md (55 sections).  
**Status:** Phase 3 structured, opt-in AI advisor committed (69 offline tests; SQLite Alembic upgrade and schema drift check passed). Provider disabled by default, local AI token + explicit data attestation required; no GitHub runner. Remaining: exact provider cost estimates, deterministic AI-question de-duplication, one-click human acceptance UI, live external-provider/Streamlit verification, auth/tenancy, dependency lock and Ruff.

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

### MVP / required
- [ ] Assessment creation, editing, listing, and workflow status.
- [ ] Structured intake and conditional, guided discovery questions.
- [ ] Workflow builder with ordered steps and input/output/owner/system metadata.
- [ ] Per-step task decomposition, initially manual with AI suggestions later.
- [ ] Fact / assumption / unknown / contradiction / evidence tracking.
- [ ] Versioned deterministic policy checks and reproducible explainable scoring.
- [ ] AI suitability, business value, technical feasibility, data readiness, risk, measurability, and recommendation results, with incomplete-data indicators.
- [ ] Six recommendation categories: traditional software; strong AI candidate; hybrid; AI + human approval; human-led / do not automate; more discovery required.
- [ ] Risk flags, required controls, and an explicit approval matrix.
- [ ] Optional structured AI discovery and analysis with a useful rules-only fallback.
- [ ] FDE challenge/review, reasoned overrides, and final human decision.
- [ ] Full Markdown or HTML assessment report export with evidence and caveats.

### If MVP is stable / next release
- [ ] Architecture-pattern diagrams and detailed PoC planning.
- [ ] KPI and evaluation-case suggestions, comparison of assessments.
- [ ] Richer multi-assessment dashboard and configurable scoring weights.
- [ ] PDF export and more polished reports.

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

These rule IDs are proposed implementation identifiers (not existing repo code). Override UI may capture the FDE's rationale, but **hard approval, privacy, and authorization gates stay enforced**.

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
- [ ] P0-03 Initialize Python project and dependency lock, package modules, Ruff, pytest, .gitignore, .env.example; keep secrets out of version control. **Partially complete:** packaging/tests, `.gitignore`, `.env.example`, Ruff config committed; resolved transitive lock and Ruff run pending offline dependency availability.
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
- [ ] P2-07 Add final-decision/review endpoints and UI with required override rationale; lock final sign-off to an authorized FDE before multi-user use. **Partial (local demo):** review and override endpoints/UI exist and guarded local finalization requires an explicit shared reviewer token (403 otherwise). Reviewer identity is unverified; replace with authenticated organization-scoped FDE roles before multi-user release.
- [x] P2-08 Add offline integration test for save → reload → analyze → review → final decision.

**Exit (local demo verified on 2026-10-11):** Rules-only assessment can be created, edited, persisted, reloaded, analyzed per task, reviewed and locally finalized with an explicitly configured reviewer token. 49 offline tests pass, a fresh SQLite Alembic migration and `alembic check` pass, and live `/health` and `/openapi.json` return 200. **Not production complete:** actual authentication and tenant-scoped authorization, Ruff/dependency lock and Streamlit browser smoke remain open. The shared token is a demo-only gate, never rollout authority.

### Phase 3 — Structured AI assistant (days 15–21)
- [ ] P3-01 Implement provider adapter, flags/env, timeouts/cost logging, mock provider and no-provider fallback. **Partial:** Adapter is opt-in with timeout, one transient retry, mocked tests, token usage and latency provenance. Accurate currency cost tracking is pending verified model-specific prices.
- [ ] P3-02 Implement versioned discovery prompt + schema, single next-best question, non-repetition and contradiction signals. **Partial:** Versioned JSON schema and highest-value question prompt exist; deterministic generated-question de-duplication and contradiction reconciliation remain pending.
- [ ] P3-03 Implement editable workflow-decomposition suggestions with human acceptance. **Partial:** Strict per-step editable task suggestions persist for review, but acceptance is currently manual entry via existing workflow editor, not a one-click import/approval flow.
- [x] P3-04 Implement AI-assisted missing-information/risk suggestions; keep rule classifications and hard controls authoritative.
- [x] P3-05 Implement architecture pattern comparison, agent-vs-workflow rationale, evaluation/PoC suggestion schema.
- [x] P3-06 Implement challenge mode: arguments against, weak assumptions, evidence to change decision, simpler option.
- [x] P3-07 Add provider error handling, retry limits, privacy gating, provenance and adversarial prompt-injection tests.
- [x] P3-08 Validate that complete end-to-end workflow still works with AI **disabled**.

**Exit (local/offline verified 2026-10-11):** Six typed proposal endpoints and Streamlit advisor tab; model provider explicitly off by default; localhost spend token, per-request data attestation, forbidden-data checks, 30-proposal/assessment cap, revision-based staleness, ID/schema checks, hard-rule veto of unsafe architecture, full rules-only fallback. 69 local automated tests pass, Alembic 0001→0002 and `alembic check` pass. **Not fully complete:** no live provider call was made and no real-data certification, identity/RBAC, validated cost estimator, generated-question de-duplication, or one-click acceptance; UI browser and Ruff checks remain unverified.

### Phase 4 — Reports, evaluation, and demo (days 22–27)
- [ ] P4-01 Build comprehensive Markdown/HTML assessment report (executive summary, workflow, decomposition, scores, assumptions/evidence, risks, AI/non-AI boundaries, architecture, evaluation, PoC, open questions, final FDE decision).
- [ ] P4-02 Add report export UI; PDF optional after the report content is correct.
- [ ] P4-03 Add evaluation dataset, gold expectations, outcome metrics and regression runner; measure decision quality and unknown-data handling.
- [ ] P4-04 Add snapshot/golden tests for reports, deterministic verdict explanations and status/override history.
- [ ] P4-05 Add Dockerfile and Compose local deployment; verify migrations, health and startup with no AI key.
- [ ] P4-06 Run and document three complete demos (AI / non-AI / hybrid) showing an FDE decision and export.
- [ ] P4-07 Document architectural and evaluation trade-offs, known limitations and screenshots/demo script.

**Exit:** Reproducible demo and export show that the system can recommend *against* AI and handle high-risk hybrid cases correctly.

### Phase 5 — Production-readiness gate (days 28–30 for assessment; hardening may take longer)
- [ ] P5-01 Add authentication, RBAC and tenant isolation before inviting real users; authorization tests for every data/export route.
- [ ] P5-02 Confirm external-model policy, privacy, residency, deletion, retention, safe logging and model-provider configuration.
- [ ] P5-03 Migrate/test PostgreSQL; review indexes, migrations, backup/restore and failure recovery.
- [ ] P5-04 Add structured telemetry (request ID, latency, provider, token usage, rule/prompt versions, errors and overrides); never log sensitive raw content.
- [ ] P5-05 Add security checks for injection, untrusted evidence, prompt injection, secrets, rate limits and dependency vulnerabilities.
- [ ] P5-06 Publish deployment/runbook, evidence-based limitations, known risks and rollback plan.
- [ ] P5-07 Release only when explicit gate checklist below passes; otherwise retain local/demo-only designation.

**Exit:** Production use is permitted only if security/privacy/authorization testing passes; a 30-day target is not a reason to skip controls.

## 11. MVP definition of done — release gate

An FDE can, using the app and not manual DB edits:
- [ ] Create and resume an assessment with an identified owner and business problem.
- [ ] Map workflow steps and atomic tasks.
- [ ] Answer conditional questions and see evidence provenance.
- [ ] See missing information and unresolved assumptions/contradictions.
- [ ] Receive per-task, explainable AI/non-AI/hybrid/human recommendations and factor scores.
- [ ] See explicit risks, policy flags, verification and approval controls.
- [ ] Review a simplest-adequate architecture and a testable PoC/evaluation plan.
- [ ] Challenge an AI recommendation, review supporting evidence, and compare simpler solutions.
- [ ] Override advisory results with rationale, without bypassing non-overridable safeguards.
- [ ] Sign and store a final FDE decision; no system-initiated production approval occurs.
- [ ] Export a truthful assessment report with facts/estimates/assumptions labeled.
- [ ] Demonstrate case A (AI), B (deterministic) and C (hybrid) with passing tests.
- [ ] Run offline, without AI provider credentials, and pass CI from a clean clone.
- [ ] For any production use: auth, RBAC, tenant separation, privacy, retention, audit, backup and security checks completed.

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

**Implementation progress:** Phases 1 and 2 (local demo) are implemented; Phase 3 has gated, offline-tested AI proposals but is intentionally not production-ready. P0 lock/Ruff, P3 cost/de-duplication/one-click acceptance, and P5 identity, tenant and privacy gates remain open. No hosted runners are used. Next: complete P3 polish or advance to Phase 4 report/evaluation behind synthetic-data restrictions.

**Recommended implementation PR order:**
1. Project scaffold and docs
2. Typed domain model and discovery questions
3. Rules, scoring and recommendation engine with fixtures
4. Database migrations and assessment APIs
5. Streamlit end-to-end rules-only assessment
6. Structured AI adapter and guarded prompts
7. FDE review, challenge and reports
8. Evaluation, containerized demo and security hardening

**Open decisions to resolve early:** local-only vs authorized pilot audience; model provider and permitted data regions; value/score thresholds after reviewing sample assessments; whether PDF is MVP-critical; hosting environment. Record each decision as an ADR rather than hiding it in prompt text.

**Progress convention:** Change a box to checked only after merging implementation and verifying its exit criteria. Update the status line, blockers, decisions, and evidence links as work progresses. This planning commit does **not** claim any application code was built.

---
**Source-of-truth principle:** When the blueprint and implementation details differ, preserve the blueprint's safety and human-accountability principles; document deliberate trade-offs in docs/decisions and revise this planner accordingly.
