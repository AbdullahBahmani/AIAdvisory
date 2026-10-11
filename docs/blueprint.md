# AI Opportunity Advisor — Complete FDE Project Blueprint

> **Purpose:** Build an AI-assisted decision-support system that helps a Forward Deployed Engineer (FDE) discover, qualify, analyze, and prioritize business workflows for AI adoption — while keeping the **final decision with the FDE**.
>
> The system should automate repetitive discovery and analysis work, surface missing information, calculate consistent scores, suggest solution patterns, identify risks, propose experiments and architecture, and produce an assessment report. It must **not** independently authorize a production AI rollout.

---

# 1. Product Vision

Businesses often ask:

> "Where should we use AI?"

That question is too broad.

The better question is:

> "Which steps in our business workflows are valuable, feasible, measurable, and safe candidates for AI — and which should remain deterministic or human-controlled?"

The **AI Opportunity Advisor** guides an FDE through that process.

The system acts as:

- interviewer
- workflow-analysis assistant
- structured note taker
- scoring engine
- risk-screening assistant
- architecture-pattern recommender
- experiment planner
- KPI designer
- report generator
- FDE decision copilot

The FDE remains responsible for:

- interpreting ambiguous business context
- validating assumptions
- interviewing stakeholders
- making architectural trade-offs
- approving risk classification
- deciding whether to prototype
- deciding whether to deploy
- defining human-control boundaries
- signing off on the final recommendation

---

# 2. Product Principle

The core design principle is:

```text
AI assists.
Rules enforce.
Data informs.
Humans decide.
```

The product must never reduce the process to:

```text
Business problem
    ↓
LLM
    ↓
"Use AI"
```

Instead:

```text
Business workflow
      ↓
Structured discovery
      ↓
Evidence collection
      ↓
Deterministic checks
      ↓
AI-assisted analysis
      ↓
Risk + feasibility + value scoring
      ↓
Recommended solution pattern
      ↓
Prototype / no prototype recommendation
      ↓
FDE review
      ↓
Final decision
```

---

# 3. Primary Users

## 3.1 Forward Deployed Engineer

Primary user.

Needs to:

- conduct discovery efficiently
- avoid asking the same questions repeatedly
- identify missing facts
- compare use cases consistently
- explain recommendations to stakeholders
- document decisions
- create a repeatable assessment process

## 3.2 AI Solutions Engineer / Architect

Can use the platform for:

- architecture qualification
- AI-vs-traditional-software decisions
- enterprise readiness analysis
- integration planning
- proof-of-concept scoping

## 3.3 Business Stakeholder

Can participate in guided discovery.

Examples:

- operations manager
- support lead
- CFO
- HR lead
- sales manager
- legal operations lead
- product manager

They should not receive an authoritative final AI adoption decision without FDE review.

---

# 4. Main Product Outcomes

For every business workflow, the platform should produce:

1. **Workflow definition**
2. **Business-value estimate**
3. **AI suitability score**
4. **Technical-feasibility score**
5. **Data-readiness score**
6. **Risk score**
7. **Measurability score**
8. **Automation recommendation**
9. **AI / deterministic / hybrid decomposition**
10. **Human-approval requirements**
11. **Suggested architecture**
12. **Required integrations**
13. **Required data**
14. **Security considerations**
15. **Evaluation plan**
16. **Pilot / PoC proposal**
17. **KPIs**
18. **Cost/benefit assumptions**
19. **Open questions**
20. **FDE final decision section**

---

# 5. Final Recommendation Categories

The system should never output only "yes" or "no".

Use these categories:

## A. Traditional Software Preferred

Use when:

- rules are deterministic
- inputs are structured
- exact correctness is required
- AI adds little value
- rule-based logic is easier to test
- cost and latency favor normal code

Example:

```text
Shipping fee = weight × region rate + tax
```

Recommendation:

```text
Use deterministic code.
Do not use an LLM for calculation.
```

---

## B. Strong AI Candidate

Use when:

- task involves unstructured content
- ambiguity exists
- natural-language understanding is useful
- volume is meaningful
- errors are tolerable or verifiable
- value can be measured

Examples:

- email classification
- document extraction
- summarization
- knowledge search
- drafting
- ticket routing

---

## C. Hybrid System Recommended

Use when AI is useful for interpretation, but deterministic systems should control final actions.

Example:

```text
Customer email
   ↓
AI extracts intent
   ↓
Rules engine checks policy
   ↓
Order API retrieves facts
   ↓
AI drafts explanation
```

---

## D. AI + Human Approval

Use when:

- AI helps analyze or recommend
- action has meaningful consequences
- human review is required

Examples:

- refund approval
- contract interpretation
- account changes
- high-value claims
- employee-impacting decisions

---

## E. Human-Led / Do Not Automate

Use when:

- risk is too high
- value is too low
- data is insufficient
- decision is highly subjective
- regulation requires human judgment
- error cannot be detected
- automation cost exceeds benefit

---

## F. More Discovery Required

Use when the system lacks enough evidence.

This category is important.

The system should prefer:

```text
"Insufficient evidence"
```

over fabricated confidence.

---

# 6. Assessment Lifecycle

Use this full workflow.

```text
1. Intake
2. Stakeholder discovery
3. Workflow mapping
4. Task decomposition
5. Data assessment
6. AI suitability assessment
7. Risk assessment
8. Integration assessment
9. Value/ROI assessment
10. Evaluation design
11. Architecture recommendation
12. PoC recommendation
13. FDE review
14. Decision
15. Pilot
16. Measure
17. Reassess
18. Production rollout
```

---

# 7. Phase 1 — Intake

Collect basic context before asking technical questions.

## Required questions

### Organization

- What does the company/team do?
- Which department owns this workflow?
- Who performs the work today?
- Who is affected by the output?
- Who owns the data?
- Who owns the final decision?

### Problem

- What problem are you trying to solve?
- Why is it important now?
- What happens if nothing changes?
- How is the problem handled today?

### Volume

- How often does the workflow occur?
- How many users perform it?
- How many cases per day/week/month?
- Is demand predictable or bursty?

### Effort

- How long does one case take?
- How many manual steps are involved?
- Which step takes the most time?
- Where do people switch between systems?

### Quality

- What are the common mistakes?
- How often does rework happen?
- What causes escalations?
- What makes a case difficult?

---

# 8. Phase 2 — Workflow Mapping

The system should guide the FDE to build the current workflow.

Template:

```text
Trigger
  ↓
Step 1
  ↓
Step 2
  ↓
Decision
 ↙     ↘
A       B
 ↓       ↓
Action  Action
  \     /
   Outcome
```

For each step record:

| Field | Description |
|---|---|
| Step name | Human-readable name |
| Owner | Person/team/system |
| Input | Data entering the step |
| Output | Data produced |
| System | CRM, ERP, email, spreadsheet, DB, etc. |
| Average time | Approximate effort |
| Frequency | Volume |
| Error rate | If known |
| Decision type | deterministic / judgment / mixed |
| Data type | structured / unstructured |
| Risk | low / medium / high / critical |
| Automation status | manual / partially automated / automated |
| Pain point | Why this step is problematic |

---

# 9. Phase 3 — Task Decomposition

Do not assess the entire workflow as one AI task.

Break it into atomic tasks.

Example:

```text
"Process customer refund"
```

should become:

```text
1. Read customer email
2. Identify customer
3. Extract order ID
4. Retrieve order
5. Search refund policy
6. Determine eligibility
7. Calculate amount
8. Draft response
9. Approve high-value refund
10. Execute refund
11. Log outcome
```

Each step receives its own recommendation.

---

# 10. Phase 4 — AI Suitability Questions

For each task ask:

## Input characteristics

- Is input text?
- PDF?
- image?
- audio?
- video?
- structured database fields?
- mixed?

## Reasoning characteristics

- Is interpretation needed?
- Is language understanding required?
- Is summarization needed?
- Is classification needed?
- Is extraction needed?
- Is semantic search needed?
- Is drafting needed?
- Is planning needed?
- Is the task deterministic?
- Can rules express the decision exactly?

## Output characteristics

- Is output free text?
- structured data?
- binary decision?
- numeric result?
- action?
- recommendation?

## Verification

- Can output correctness be checked automatically?
- Can a deterministic system validate it?
- Is there a known expected result?
- Can a human review quickly?

## Variability

- Are cases highly repetitive?
- Are inputs diverse?
- Does the workflow have exceptions?
- Are edge cases common?

---

# 11. Deterministic "Do Not Use AI" Rules

Certain rules should execute before LLM analysis.

Example pseudocode:

```python
if task.is_deterministic and task.rules_are_known:
    prefer_traditional_software = True

if task.requires_exact_math:
    llm_should_not_calculate = True

if task.requires_database_truth:
    require_api_or_database_lookup = True

if task.has_zero_error_tolerance and not task.has_verification:
    prohibit_autonomous_ai = True

if task.action_is_irreversible and task.risk >= HIGH:
    require_human_approval = True

if task.business_value < minimum_threshold:
    recommend_no_project = True
```

The LLM may explain these rules.

It should not override them silently.

---

# 12. AI Suitability Score

Example scoring model: 0–100.

## Positive signals

| Signal | Weight |
|---|---:|
| Unstructured text/documents | +15 |
| Classification/extraction task | +10 |
| Semantic search required | +10 |
| Natural-language generation useful | +10 |
| High repetitive volume | +10 |
| Human task currently slow | +10 |
| Output easy to verify | +10 |
| Existing data available | +10 |
| Clear KPI | +5 |
| Existing APIs/integrations | +5 |
| Human review available | +5 |

## Negative signals

| Signal | Weight |
|---|---:|
| Fully deterministic rules | -20 |
| Exact calculation required | -15 |
| Zero-error requirement | -20 |
| No reliable data | -15 |
| Very low volume | -10 |
| High consequence + no human review | -25 |
| Hard to evaluate | -15 |
| No integration path | -10 |

This score is advisory.

The FDE can override it with a documented reason.

---

# 13. Business Value Score

Measure:

## Frequency

- volume per period
- number of employees
- seasonality

## Cost

- labor cost
- tooling cost
- outsourced cost
- error cost
- delay cost

## Impact

- revenue
- customer satisfaction
- employee productivity
- compliance
- quality
- speed

Example:

```text
500 tickets/day
8 minutes/ticket
Target = 3 minutes/ticket

Potential time saving:
5 minutes × 500 = 2,500 minutes/day
≈ 41.7 hours/day
```

Important:

These numbers are hypotheses until validated in real usage.

---

# 14. Technical Feasibility Score

Assess:

- API availability
- database access
- document availability
- authentication
- rate limits
- latency requirements
- cloud/network constraints
- model availability
- deployment restrictions
- integration complexity
- data format quality
- system ownership
- sandbox/test environment availability

---

# 15. Data Readiness Score

Assess:

## Availability

- Does data exist?
- Where?
- Who owns it?

## Quality

- Is it complete?
- Is it current?
- Is it duplicated?
- Is it labeled?
- Is terminology consistent?

## Accessibility

- API?
- SQL?
- files?
- email?
- PDFs?
- SaaS connector?

## Permission

- Can AI access it?
- Can it leave the region?
- Can external model providers receive it?
- Does it contain sensitive information?

## Governance

- retention
- deletion
- audit
- access control
- residency
- lineage

---

# 16. Risk Assessment

Use separate risk categories.

## 16.1 Business Risk

- financial loss
- wrong customer action
- SLA violation
- reputational harm

## 16.2 Safety Risk

- physical harm
- health impact
- psychological harm

## 16.3 Legal/Compliance Risk

- regulatory decisions
- protected-class decisions
- legal commitments
- contract interpretation

## 16.4 Privacy Risk

- PII
- health data
- financial data
- employee data
- confidential documents

## 16.5 Security Risk

- prompt injection
- indirect prompt injection
- excessive tool permissions
- credential exposure
- data exfiltration
- unsafe external content

## 16.6 Operational Risk

- incorrect actions
- duplicate actions
- tool failure
- stale knowledge
- unavailable API
- runaway loops
- uncontrolled cost

---

# 17. Risk Matrix

```text
Impact
  ↑
5 |         HUMAN ONLY / STRICT CONTROL
4 |       HUMAN APPROVAL
3 |     AI + VERIFICATION
2 |   CONTROLLED AUTOMATION
1 | AI AUTOMATION POSSIBLE
  +----------------------------→
    1   2   3   4   5
        Likelihood
```

The platform should output:

```text
Risk score: 16/25
Risk class: High

Required controls:
- human approval
- action allowlist
- audit logging
- strict identity checks
- rollback strategy
```

---

# 18. Human Approval Matrix

Define actions explicitly.

| Action | Default policy |
|---|---|
| Search knowledge | autonomous |
| Summarize content | autonomous |
| Draft email | autonomous draft, human send optional |
| Read customer data | permission-controlled |
| Create low-risk ticket | possibly autonomous |
| Modify customer record | approval recommended |
| Cancel order | approval |
| Issue refund | approval or deterministic policy gate |
| Transfer money | strong deterministic controls + approval |
| Delete data | approval |
| Legal commitment | human |
| Hiring/firing decision | human-led |
| Medical diagnosis/treatment decision | human professional |

---

# 19. AI's Role in the Discovery Process

AI can automate repetitive FDE tasks.

## AI should:

- ask the next best discovery question
- summarize stakeholder answers
- detect contradictions
- identify missing information
- map answers into structured fields
- propose workflow steps
- propose decomposition
- calculate preliminary scores
- identify common architecture patterns
- generate questions for stakeholders
- propose KPI candidates
- draft risk lists
- draft evaluation cases
- create a PoC checklist
- produce an assessment report
- compare multiple candidate workflows

## AI should not:

- fabricate evidence
- silently assume missing facts
- approve production deployment
- override security policies
- make irreversible business decisions
- make final legal/compliance determinations
- claim ROI as fact before validation
- hide uncertainty

---

# 20. AI Conversation Behavior

The assistant should operate as a structured consultant.

## Good behavior

```text
You said the process handles 2,000 requests/month,
but the number of employees performing it is missing.

Before estimating ROI, I need:
1. average handling time
2. loaded hourly cost
3. rework rate
```

## Bad behavior

```text
This will save your company €500,000/year.
```

without evidence.

---

# 21. Question Selection Logic

Do not ask every question every time.

Use dynamic branching.

Example:

```text
Does workflow process sensitive data?
    ↓ yes
Ask privacy + residency + permissions

Does task trigger external actions?
    ↓ yes
Ask authorization + approval + rollback

Is input unstructured?
    ↓ yes
Ask format + examples + quality

Are decisions deterministic?
    ↓ yes
Ask rule stability + edge cases
```

---

# 22. Missing-Information Engine

Every assessment should maintain:

```json
{
  "known": [],
  "unknown": [],
  "assumptions": [],
  "contradictions": [],
  "evidence_needed": []
}
```

Examples:

```text
Unknown:
- current handling time
- cost per employee
- refund error rate

Assumption:
- order API exists

Evidence needed:
- 50 historical examples
- refund policy documents
```

The final report must clearly distinguish:

- facts
- estimates
- assumptions
- AI-generated suggestions

---

# 23. Recommended Solution Pattern Engine

Based on the assessment, recommend patterns.

## Pattern 1 — Classification

```text
Input
 ↓
LLM classifier
 ↓
Structured category
 ↓
Routing
```

## Pattern 2 — Extraction

```text
Document
 ↓
AI extraction
 ↓
Structured schema
 ↓
Validation
 ↓
Database
```

## Pattern 3 — RAG

```text
Question
 ↓
Retrieve knowledge
 ↓
Relevant chunks
 ↓
LLM
 ↓
Answer + citations
```

## Pattern 4 — AI + Deterministic Rules

```text
Unstructured input
 ↓
AI interpretation
 ↓
Structured result
 ↓
Rules engine
 ↓
Action
```

## Pattern 5 — Tool-Using Agent

```text
User request
 ↓
Agent
 ├── search
 ├── database
 └── API
 ↓
Response
```

Use only when task needs dynamic tool selection.

## Pattern 6 — AI + Human Approval

```text
AI recommendation
 ↓
Human review
 ↓
Approved action
```

---

# 24. "Agent or Workflow?" Decision

Do not recommend an agent automatically.

Use deterministic workflows when sequence is known.

```text
Known sequence
→ workflow/orchestration

Dynamic planning needed
→ consider agent
```

Questions:

- Are the steps predictable?
- Is tool selection dynamic?
- Does the task require planning?
- Can a state machine solve it?
- Would an agent increase unnecessary risk?

---

# 25. Architecture Generator

The system should produce a recommended architecture.

Example:

```text
               Web UI
                 ↓
              FastAPI
                 ↓
       Assessment Orchestrator
        ┌────────┼──────────┐
        ↓        ↓          ↓
 Rules Engine  LLM       Scoring
        ↓        ↓          ↓
   Policy Gate  Analysis   Scores
         \       |         /
          \      |        /
            Recommendation
                 ↓
             FDE Review
                 ↓
          Assessment Report
```

Supporting services:

```text
PostgreSQL
├── users
├── organizations
├── assessments
├── workflows
├── workflow_steps
├── questions
├── answers
├── scores
├── risks
├── recommendations
├── evidence
├── decisions
└── audit_events
```

---

# 26. Suggested Technical Stack

## MVP

### Frontend

Choose one:

- Streamlit
- Next.js / React

Recommended MVP:

```text
Streamlit
```

because it is faster for an FDE portfolio prototype.

### Backend

```text
Python
FastAPI
Pydantic
```

### Database

MVP:

```text
SQLite
```

Production-oriented:

```text
PostgreSQL
```

### AI

Model API with support for:

- structured outputs
- tool/function calling
- strong reasoning
- evaluation support

### Deployment

```text
Docker
Cloud platform
```

### Observability

Capture:

- request ID
- prompt/version
- model
- latency
- token usage
- tool calls
- failures
- scores
- human overrides

---

# 27. Data Model

## Organization

```text
id
name
industry
country
size
regulatory_context
```

## Assessment

```text
id
organization_id
title
owner
status
created_at
updated_at
final_decision
fde_notes
```

## Workflow

```text
id
assessment_id
name
trigger
outcome
volume
frequency
baseline_cost
baseline_time
```

## WorkflowStep

```text
id
workflow_id
order
name
owner
input_type
output_type
system
decision_type
risk
time_minutes
error_rate
automation_status
```

## Question

```text
id
category
text
answer_type
required
condition
```

## Answer

```text
id
assessment_id
question_id
value
source
confidence
```

## Score

```text
assessment_id
business_value
ai_suitability
technical_feasibility
data_readiness
risk
measurability
```

## Recommendation

```text
category
reasoning_summary
recommended_pattern
human_approval_required
next_action
```

## Evidence

```text
type
source
description
confidence
```

## FDEDecision

```text
decision
rationale
override_reason
approved_by
date
```

---

# 28. API Blueprint

## Assessment

```text
POST /assessments
GET  /assessments/{id}
PATCH /assessments/{id}
```

## Discovery

```text
GET  /assessments/{id}/next-question
POST /assessments/{id}/answers
GET  /assessments/{id}/missing-information
```

## Workflow

```text
POST /assessments/{id}/workflows
POST /workflows/{id}/steps
GET  /workflows/{id}
```

## Analysis

```text
POST /assessments/{id}/analyze
GET  /assessments/{id}/scores
GET  /assessments/{id}/risks
GET  /assessments/{id}/recommendation
```

## FDE Review

```text
POST /assessments/{id}/fde-review
POST /assessments/{id}/final-decision
```

## Report

```text
GET /assessments/{id}/report
```

---

# 29. Structured AI Outputs

Never parse critical decisions from arbitrary prose.

Example:

```json
{
  "task_name": "refund eligibility",
  "ai_suitability": 62,
  "recommended_category": "hybrid",
  "suggested_pattern": "AI interpretation + deterministic rules",
  "risk_level": "high",
  "requires_human_approval": true,
  "missing_information": [
    "refund policy edge cases"
  ],
  "assumptions": [
    "order history API is available"
  ],
  "reasoning_summary": "AI can interpret customer language, but refund eligibility should be controlled by deterministic policy rules."
}
```

Validate with Pydantic.

---

# 30. Prompt Architecture

Do not use one giant prompt.

Use separate prompts/components.

## Prompt A — Discovery Assistant

Purpose:

- ask one useful next question
- avoid repetition
- identify missing information

System instruction concept:

```text
You are an FDE discovery assistant.
Your job is to gather evidence, not to sell AI.
Ask only questions that materially improve the assessment.
Never assume unknown facts.
Clearly mark uncertainty.
```

---

## Prompt B — Workflow Analyst

Purpose:

- decompose workflow
- identify AI-friendly and deterministic steps
- identify dependencies

---

## Prompt C — Risk Analyst

Purpose:

- identify plausible risks
- classify impact
- suggest controls

It should not make final legal conclusions.

---

## Prompt D — Architecture Advisor

Purpose:

- recommend the simplest adequate solution
- avoid unnecessary agents
- prefer deterministic components where possible
- propose human approval for high-risk actions

---

## Prompt E — Evaluation Planner

Purpose:

- generate test cases
- define expected behavior
- propose measurable acceptance criteria

---

## Prompt F — FDE Decision Copilot

Purpose:

After all evidence exists, allow the FDE to ask:

```text
"What are the strongest arguments against deploying this?"
"What assumptions are weakest?"
"What would make this a no-go?"
"What should we validate in a 2-week pilot?"
"Which architecture is simpler?"
"What information would change your recommendation?"
```

This prompt should challenge the assessment, not merely agree.

---

# 31. Decision-Challenge Mode

Add a dedicated "Challenge My Decision" feature.

If the FDE is leaning toward AI, the system should produce:

## Arguments against

- simpler deterministic alternatives
- risk concerns
- data-quality issues
- evaluation limitations
- adoption problems
- integration burden
- cost uncertainty

If the FDE is leaning against AI, produce:

## Arguments for

- overlooked repetitive work
- high unstructured-data burden
- low-risk assistive opportunities
- human-in-the-loop patterns
- small reversible experiments

The goal is better decisions, not agreement.

---

# 32. Evidence Requirements

For a strong recommendation, require evidence.

Suggested evidence levels:

## Level 0 — Opinion

Stakeholder belief only.

## Level 1 — Examples

A few real cases.

## Level 2 — Sample dataset

Dozens/hundreds of historical examples.

## Level 3 — Measured baseline

Current time/cost/error metrics.

## Level 4 — Pilot evidence

Real test with target users.

## Level 5 — Production evidence

Observed results after deployment.

The report should show evidence level.

---

# 33. PoC Recommendation Logic

Recommend a PoC when:

```text
Business value: medium/high
Technical feasibility: medium/high
AI suitability: medium/high
Risk: manageable
Evidence: insufficient for production
```

PoC should answer specific questions.

Bad:

```text
Build chatbot.
```

Good:

```text
Test whether AI can classify 200 historical tickets
with ≥92% category accuracy and <2% critical misroutes.
```

---

# 34. Experiment Template

Every PoC should include:

```text
Hypothesis:
AI can reduce policy-search time while preserving answer quality.

Dataset:
200 historical support cases.

Baseline:
Average search time = 4.2 minutes.

Treatment:
RAG assistant.

Metrics:
- retrieval success
- citation accuracy
- answer correctness
- handling time
- escalation rate

Success criteria:
- ≥90% answer correctness
- ≥95% citation validity
- ≥40% time reduction

Failure criteria:
- critical incorrect answers >1%
- sensitive-data leakage
```

---

# 35. Evaluation Framework

Use both software tests and AI evals.

## Deterministic tests

Test:

- scoring
- permissions
- rules
- API behavior
- schema validation
- approval gates

## AI evals

Test:

- extraction
- classification
- retrieval
- groundedness
- tool selection
- refusal
- risk identification
- next-question quality

---

# 36. Evaluation Dataset

Example schema:

```json
{
  "case_id": "C-001",
  "input": {},
  "expected": {
    "recommendation": "traditional_software",
    "requires_human_approval": false
  },
  "critical_conditions": [
    "must_not_recommend_llm_for_exact_calculation"
  ]
}
```

Include cases for:

- obvious AI candidate
- obvious no-AI case
- hybrid case
- insufficient information
- high-risk action
- low-value workflow
- sensitive data
- contradictory answers
- inaccessible data
- unreliable source material

---

# 37. Product KPIs

## Assessment quality

- FDE agreement rate
- FDE override rate
- missing-information detection
- recommendation consistency
- false-positive AI recommendations
- false-negative AI recommendations

## Efficiency

- discovery preparation time
- assessment completion time
- report-generation time

## Business usefulness

- percentage of assessments leading to useful PoCs
- percentage of PoCs meeting success criteria
- stakeholder satisfaction
- avoided low-value AI projects

A valuable KPI is:

```text
Bad projects avoided
```

not only projects started.

---

# 38. Security Requirements

## Authentication

- authenticated users
- role-based access
- organization isolation

## Authorization

- FDE
- reviewer
- business stakeholder
- administrator

## Data protection

- encryption in transit
- encryption at rest
- no secrets in prompts
- least-privilege access

## Prompt injection controls

Treat uploaded documents and user-entered external content as untrusted.

Never let document content override system policy.

## Tool security

- allowlisted tools
- validated arguments
- scoped credentials
- read-only by default
- approval for writes

---

# 39. Privacy Requirements

The platform may contain sensitive business information.

Support:

- configurable retention
- deletion
- tenant separation
- audit logs
- redaction
- data classification
- region constraints
- configurable model-provider policy

Before sending data to an AI provider, evaluate:

- sensitivity
- contractual restrictions
- residency
- retention
- organizational policy

---

# 40. Auditability

Record important events.

```text
assessment_created
answer_added
assumption_added
score_calculated
recommendation_generated
risk_flagged
fde_override
final_decision
report_exported
```

For AI-generated analysis store:

- model
- version if available
- prompt version
- timestamp
- relevant inputs
- structured output

---

# 41. Explainability

Every recommendation must explain:

```text
Recommendation:
Hybrid system.

Why:
- input is unstructured
- policy rules are deterministic
- high-value action has financial impact

AI should:
- classify intent
- extract fields
- retrieve relevant policy

AI should not:
- calculate final refund policy independently
- execute high-value refund

Human should:
- approve exceptions over threshold
```

Avoid opaque scores without explanation.

---

# 42. FDE Override

The FDE must be able to override:

- score
- category
- risk level
- suggested architecture
- recommendation

But require:

```text
override_reason
```

This becomes valuable training/evaluation data later.

---

# 43. Final FDE Decision Template

```text
Final Decision:
[ ] Proceed to PoC
[ ] Proceed with traditional software
[ ] Hybrid PoC
[ ] More discovery
[ ] Do not proceed

FDE rationale:

Key evidence:

Key assumptions:

Primary risk:

Required controls:

Pilot success criteria:

Decision owner:

Review date:
```

---

# 44. Assessment Report Structure

Generate a Markdown/PDF-ready report containing:

## Executive Summary

- problem
- recommendation
- expected value
- main risk
- next step

## Current Workflow

- workflow diagram
- pain points
- baseline

## Task Decomposition

Table of every step.

## AI Suitability

- score
- evidence
- explanation

## Value

- baseline
- opportunity
- assumptions

## Risk

- category
- controls

## Recommended Architecture

Diagram and explanation.

## AI vs Non-AI Boundary

Explicitly show what AI does and does not do.

## Evaluation Plan

- dataset
- metrics
- thresholds

## PoC Plan

- scope
- duration
- users
- evidence needed

## Open Questions

## FDE Final Decision

---

# 45. Example Assessment

## Business problem

Support agents spend significant time responding to refund requests.

## Current workflow

```text
Email
 ↓
Read request
 ↓
Find customer
 ↓
Find order
 ↓
Search policy
 ↓
Determine eligibility
 ↓
Calculate refund
 ↓
Draft email
 ↓
Execute refund
```

## Recommended decomposition

| Step | Recommendation |
|---|---|
| Read request | AI |
| Extract order ID | AI structured extraction |
| Find order | API |
| Search policy | RAG |
| Determine eligibility | deterministic policy rules |
| Calculate refund | deterministic code |
| Draft email | AI |
| Execute refund | API + approval |
| High-value exception | human |

## Final architecture

```text
Customer email
     ↓
AI extraction
     ↓
Order API
     ↓
Policy RAG
     ↓
Rules engine
     ↓
Refund proposal
     ↓
Human approval if required
     ↓
Refund API
     ↓
AI-generated explanation
```

This is better than:

```text
Email
 ↓
LLM
 ↓
Refund
```

---

# 46. UI Blueprint

## Screen 1 — Dashboard

Show:

- assessments
- status
- recommendation
- risk
- owner
- last updated

## Screen 2 — New Assessment

Collect:

- organization
- workflow name
- problem
- stakeholder

## Screen 3 — Guided Discovery

Show:

```text
Question

Why we are asking this

Answer field

Evidence/source

Confidence
```

## Screen 4 — Workflow Builder

Allow adding steps.

## Screen 5 — Analysis

Show cards:

```text
Business Value
AI Suitability
Technical Feasibility
Data Readiness
Risk
Measurability
```

## Screen 6 — AI vs Non-AI Decomposition

Table for every task.

## Screen 7 — Recommendation

Show:

- architecture
- risks
- controls
- PoC
- open questions

## Screen 8 — Ask the AI Advisor

FDE can consult AI.

Suggested buttons:

- Challenge recommendation
- Find missing assumptions
- Suggest simpler architecture
- What should not use AI?
- What evidence is weakest?
- Generate pilot plan
- Generate executive summary

## Screen 9 — Final Decision

FDE confirms.

---

# 47. Repository Structure

Suggested structure:

```text
ai-opportunity-advisor/
│
├── README.md
├── BLUEPRINT.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   ├── assessments.py
│   │   ├── discovery.py
│   │   ├── workflows.py
│   │   ├── analysis.py
│   │   └── reports.py
│   │
│   ├── models/
│   │   ├── assessment.py
│   │   ├── workflow.py
│   │   ├── score.py
│   │   └── recommendation.py
│   │
│   ├── services/
│   │   ├── scoring.py
│   │   ├── rule_engine.py
│   │   ├── ai_analysis.py
│   │   ├── risk.py
│   │   ├── architecture.py
│   │   └── reporting.py
│   │
│   ├── prompts/
│   │   ├── discovery.md
│   │   ├── workflow_analysis.md
│   │   ├── risk_analysis.md
│   │   ├── architecture.md
│   │   └── decision_challenge.md
│   │
│   └── db/
│       ├── models.py
│       └── session.py
│
├── ui/
│   └── streamlit_app.py
│
├── tests/
│   ├── test_rules.py
│   ├── test_scoring.py
│   ├── test_api.py
│   └── evals/
│       ├── cases.json
│       └── run_evals.py
│
├── docs/
│   ├── architecture.md
│   ├── security.md
│   ├── evaluation.md
│   └── examples/
│
└── migrations/
```

---

# 48. MVP Scope

Build only these features first:

## Must have

- create assessment
- guided questions
- workflow steps
- deterministic scoring
- AI structured analysis
- AI vs non-AI recommendation
- risk flags
- missing information
- FDE override
- final report

## Should have

- architecture generation
- PoC plan
- KPI suggestions
- evaluation-case generation
- assessment comparison

## Later

- organization-wide opportunity portfolio
- connectors
- historical benchmark data
- multi-FDE collaboration
- automated document ingestion
- enterprise SSO
- advanced governance

---

# 49. Implementation Plan

## Phase 1 — Rules-only prototype

Build:

- questionnaire
- scoring
- rules
- recommendation categories

No AI required yet.

Goal:

Validate framework.

---

## Phase 2 — AI discovery assistant

Add:

- next-question generation
- summaries
- missing-information detection

---

## Phase 3 — AI analysis

Add:

- workflow decomposition
- risk suggestions
- architecture suggestions
- evaluation-plan generation

All structured.

---

## Phase 4 — FDE copilot

Add:

- challenge mode
- comparison
- decision consultation
- final-report drafting

---

## Phase 5 — Production hardening

Add:

- auth
- PostgreSQL
- audit logs
- tracing
- evals
- cloud deployment
- security testing

---

# 50. 30-Day Build Plan

## Week 1 — Framework

Day 1:
Define product scope and recommendation categories.

Day 2:
Create question taxonomy.

Day 3:
Create workflow-step schema.

Day 4:
Build AI-suitability scoring.

Day 5:
Build value scoring.

Day 6:
Build risk scoring.

Day 7:
Build deterministic recommendation engine.

## Week 2 — Backend

Day 8:
FastAPI project.

Day 9:
Pydantic models.

Day 10:
SQLite/PostgreSQL schema.

Day 11:
Assessment APIs.

Day 12:
Question/answer APIs.

Day 13:
Workflow APIs.

Day 14:
Scoring APIs.

## Week 3 — AI

Day 15:
Structured LLM integration.

Day 16:
Discovery prompt.

Day 17:
Workflow decomposition.

Day 18:
Risk analysis.

Day 19:
Architecture advisor.

Day 20:
Missing-information engine.

Day 21:
Decision-challenge assistant.

## Week 4 — Quality + Product

Day 22:
Streamlit UI.

Day 23:
Report generation.

Day 24:
Unit tests.

Day 25:
Evaluation dataset.

Day 26:
Prompt-injection tests.

Day 27:
Docker.

Day 28:
Tracing/observability.

Day 29:
Run 3 complete example assessments.

Day 30:
Portfolio README + demo.

---

# 51. Definition of Done

The MVP is complete when an FDE can:

1. create an assessment
2. describe a workflow
3. answer guided questions
4. see missing information
5. receive per-task AI/non-AI recommendations
6. see scores
7. see risk controls
8. see suggested architecture
9. request a PoC plan
10. challenge the recommendation
11. override the AI
12. record a final decision
13. export a complete assessment report

---

# 52. Critical Product Rules

These must be explicit in the codebase and documentation.

## Rule 1

The product optimizes for **good decisions**, not maximum AI adoption.

## Rule 2

Traditional software is a valid successful recommendation.

## Rule 3

"No project" is a valid successful recommendation.

## Rule 4

Unknown information stays unknown.

## Rule 5

The AI cannot silently override deterministic safeguards.

## Rule 6

High-risk actions require human or policy-controlled approval.

## Rule 7

Business value must be measured.

## Rule 8

AI recommendations must be evaluated against real examples.

## Rule 9

The simplest reliable architecture should be preferred.

## Rule 10

The FDE makes the final decision.

---

# 53. FDE Field Checklist

Before recommending any AI project, confirm:

## Business

- [ ] Clear problem
- [ ] Workflow mapped
- [ ] Volume known
- [ ] Baseline measured
- [ ] KPI defined
- [ ] Owner identified

## AI fit

- [ ] AI-specific advantage exists
- [ ] deterministic alternative considered
- [ ] unstructured/ambiguous work identified
- [ ] output is verifiable

## Data

- [ ] data exists
- [ ] access available
- [ ] permissions known
- [ ] quality sampled
- [ ] privacy reviewed

## Integration

- [ ] APIs/systems identified
- [ ] authentication understood
- [ ] test environment available

## Risk

- [ ] consequence of failure known
- [ ] sensitive actions identified
- [ ] approval gates defined
- [ ] rollback/escalation exists

## Evaluation

- [ ] historical examples available
- [ ] eval dataset planned
- [ ] metrics defined
- [ ] threshold defined

## Economics

- [ ] baseline cost known
- [ ] expected benefit estimated
- [ ] AI operating cost estimated
- [ ] assumptions documented

## Rollout

- [ ] pilot scope defined
- [ ] users identified
- [ ] monitoring planned
- [ ] stop criteria defined

---

# 54. What Makes This a Strong FDE Portfolio Project

This project demonstrates that you understand:

- AI is not always the answer
- workflows matter more than demos
- business value matters
- AI must integrate with real systems
- deterministic code and AI should coexist
- risk changes architecture
- evaluation comes before scale
- human control is essential for high-impact actions
- decisions should be evidence-based
- AI can reduce repetitive consulting work without replacing engineering judgment

A strong portfolio demo should show two contrasting examples:

## Example A — AI recommended

Customer-support knowledge workflow.

## Example B — AI rejected

Exact deterministic pricing calculation.

Then show a third:

## Example C — Hybrid

AI interprets documents, deterministic rules control the final action, and humans approve high-risk exceptions.

That demonstrates mature FDE thinking.

---

# 55. Final Product Statement

The AI Opportunity Advisor is not an "AI that decides where AI should be used."

It is:

> **A structured decision-support platform that helps an FDE discover, analyze, compare, test, and document AI opportunities faster and more consistently — while preserving human accountability for the final decision.**

The desired outcome is not:

```text
More AI.
```

It is:

```text
Better business systems,
using AI only where it creates measurable value.
```
