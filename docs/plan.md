# AI Procurement Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, deploy, and demonstrate the approved AI procurement agent on a self-managed Kubernetes cluster with isolated dev/prod worker ASGs and automated worker termination cleanup.

**Architecture:** One fixed kubeadm control plane coordinates separate
single-AZ dev and prod worker ASGs. Required Kubernetes HPAs scale pods; Phase
1 node capacity remains an explicit Terraform input, while EventBridge, Lambda,
and SSM automate bounded worker drain and stale-node cleanup. One StockAI Odoo
image extends the pinned Community base with the narrow budget, atomic PO, and
ORM-bootstrap contracts selected after T10 discovery.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Python MCP SDK, React, TypeScript, Vite, Odoo 19 Community, PostgreSQL, Terraform, AWS EC2/Auto Scaling/SSM/EventBridge/Lambda/EBS/ALB/ACM/Route 53, kubeadm Kubernetes, Kustomize, Argo CD, Prometheus, Grafana, Loki, Alertmanager, GitHub Actions.

**Status:** Proposed remaining-MVP simplification amendment; specification
approved by the user and course staff, plan awaiting exact user and renewed
course-staff approval

**Date:** 2026-08-14

**Source design:** User- and course-staff-approved `docs/spec.md` dated
2026-08-14

**Current gate:** The user approved the exact 2026-08-07 revision, confirmed
course-staff approval, and explicitly authorized implementation to resume. T10,
T11A, and T11B are approved and merged. The user explicitly authorized T12 on
2026-08-09; its implementation and mocked-Bedrock verification are complete.
T12A is approved and merged. T13 is approved and merged through PR #17 at
`cca7208`. T14 is approved and merged through PR #18 at `fd6ba1d`. T15 is
approved and merged through PR #19 at `f89a089`. T16 is approved and merged
through PR #20 at `debbb5f`. T17 through T19A are complete and merged, including
the accepted T18B deferred live failure drill. T19B is merged through PR #27 at
`055aa01`. T20A is merged through PR #28 at `970aa8d`. T20B is merged through
PR #29 at `1e1b98d`. T21A is merged through PR #31 at `0b87a61`. T21B is
implemented locally with its remaining live lifecycle verification recorded in
`docs/implementation-status.md`. T22 is the next proposed task.

**Proposed plan amendment:** Preserve T01–T21B history and implemented
architecture; replace only future work with the compact T22–T29 and T32–T35
structure below. Implementation remains blocked until the user and course
staff approve this exact plan amendment and the user explicitly authorizes the
affected task.

## 1. Approval status and purpose

User and course-staff approval of the revised `docs/spec.md` dated 2026-08-02
were confirmed by the user on 2026-08-02. User and course-staff approval of this
synchronized implementation plan were subsequently completed through the
required pull-request workflow.

The user explicitly authorized implementation on 2026-08-02. T01–T09 proceeded
under that authorization. T10 then triggered its approved stop condition. On
2026-08-07 the user selected the remediation, approved this exact synchronized
specification and plan, confirmed course-staff approval, and explicitly
authorized T10 implementation to resume.

On 2026-08-14 the user approved the focused remaining-MVP design and exact
written specification amendment, then confirmed renewed course-staff approval.
This synchronized plan amendment is now the next approval artifact. T22 and
later remain unauthorized until the user and course staff approve this exact
plan and the user explicitly authorizes implementation to resume.

If an implementation discovery conflicts with the approved specification, work
must stop, the affected design and plan sections must be revised, and the
required approval must be obtained before work resumes.

## 2. Planning approach

This revision uses the dedicated `writing-plans` skill and preserves the
project-required `docs/plan.md` location:

- derive every task from an approved specification requirement;
- keep tasks small and independently reviewable;
- use tests or automated validation before implementation whenever applicable;
- keep the system runnable at the end of every phase;
- establish a local end-to-end walking skeleton before broad domain work;
- promote that walking skeleton through dev and prod before adding remaining
  capabilities;
- add later capabilities as end-to-end vertical slices;
- give every task concrete files, dependencies, verification, and completion
  criteria;
- preserve the required branch, immutable-artifact, GitHub Actions, and Argo CD
  workflow.
- remove duplicated acceptance work and split tasks only at meaningful review
  boundaries.

## 3. Global constraints

The following decisions come from the revised specification. They become fixed
after the required new approval and must not then be silently changed:

- Python, FastAPI, and LangGraph implement the API and agent.
- A custom Python Procurement MCP server uses Streamable HTTP.
- Amazon Bedrock model `openai.gpt-oss-20b-1:0` is the only LLM.
- React, TypeScript, and Vite build a separate frontend served by NGINX.
- Odoo 19 Community and PostgreSQL are separate per environment.
- DynamoDB stores checkpoints and application state; S3 stores Loki objects.
- Cognito provides identity; backend-managed opaque sessions protect the UI.
- AWS resources are provisioned with Terraform in `us-east-1`.
- Kubernetes is self-managed with kubeadm on one fixed `t3.medium` control
  plane and separate dev/prod `t3.medium` worker ASGs; every instance has an
  encrypted root EBS volume no larger than 30 GB.
- Active worker defaults are `min = 1`, `desired = 1`, and `max = 3` for each
  single-AZ environment ASG. Phase 1 has no ASG scaling policy, Cluster
  Autoscaler, Karpenter, or other automatic node scaling.
- One internet-facing ALB terminates an ACM certificate and sends traffic to a
  fixed NGINX Ingress NodePort through environment-specific ASG target groups.
- Odoo filestore, PostgreSQL, and Prometheus each use one Terraform-created,
  encrypted, static, retained EBS volume per environment through the pinned EBS
  CSI driver; initial volume size is 5 GiB each.
- Grafana has no EBS volume: Git-managed provisioning defines supported data
  sources, dashboards, folders, and alerts, while runtime state is disposable.
- Frontend, Agent API, and Procurement MCP each use a CPU HPA with minimum one,
  maximum three, and a 50% average utilization target.
- Dev and prod run complete, separate stacks on hard-labeled and tainted worker
  ASGs with separate launch templates, instance roles, Availability Zones, and
  ALB target groups.
- A finite 24-hour kubeadm token rotates every 12 hours into one Terraform-
  created SSM `SecureString`; workers validate and use it without logging it.
- An ASG termination lifecycle hook routes through EventBridge to one shared
  Lambda, which heartbeats and uses SSM on the control plane for bounded,
  idempotent node cleanup. Non-clean outcomes fail open, alert, and follow a
  recovery runbook.
- Kustomize defines application desired state.
- GitHub Actions builds and validates; Argo CD deploys. Actions never deploy
  workloads with `kubectl`.
- Every purchase order requires a revision-bound manager approval.
- The application owns one fixed, version-controlled system prompt; users
  configure only typed recommendation preferences in Odoo.
- Current recommendation preferences resolve product, then category, then
  company scope. Each record has a server-managed monotonically increasing
  revision, and each case stores an immutable applied-preference snapshot.
- Hard eligibility and approval policy always outrank advisory preferences;
  configured hard price-premium limits are enforced deterministically.
- The manager workflow is approve-to-confirm or reject-to-cancel only. The
  existing safe Odoo draft-update primitive remains implemented but outside
  the MVP flow.
- Every relevant non-bot dev release builds all four project images and binds
  them to one deterministic application-content identity. Changed-image
  detection and prior-digest carry-forward are excluded.
- Release identity, four image digests, content digest, provenance, Scout
  result, source traceability, and creation metadata are immutable after T22;
  T23 appends exact-release validation evidence that cannot be silently
  replaced after passing.
- Supplier contact, payment, and real legal ordering remain outside the MVP.

The implementation may choose exact dependency patch versions only after
compatibility checks, but it must pin them in lock files and immutable image
digests. The intended baseline is Python 3.12, a current supported Node LTS,
`uv` for Python locking, and `npm` lock files for the frontend. A compatibility
failure is resolved in the plan or specification rather than by an unrecorded
toolchain change.

---

## 4. Planned repository layout

The implementation uses one version-controlled Python distribution with two
independently started backend services. This is a shared-code boundary, not a
shared-process boundary. FastAPI and Procurement MCP have independent entry
points, images, configuration, credentials, health behavior, and Kubernetes
workloads.

```text
.
├── .github/
│   └── workflows/
├── deploy/
│   └── kubernetes/
│       ├── base/
│       ├── cluster/
│       ├── overlays/
│       │   ├── dev/
│       │   └── prod/
│       └── argocd/
├── docker/
├── docs/
│   ├── spec.md
│   ├── plan.md
│   ├── implementation-status.md
│   ├── odoo-contract.md
│   ├── demo/
│   └── runbooks/
├── frontend/
│   ├── src/
│   └── tests/
├── infra/
│   ├── cluster/
│   │   ├── install-node.sh
│   │   ├── init-control-plane.sh
│   │   ├── join-worker.sh
│   │   └── rotate-join-token.sh
│   └── terraform/
│       ├── bootstrap/
│       ├── modules/
│       │   ├── app-environment/
│       │   ├── compute/
│       │   ├── edge/
│       │   ├── network/
│       │   ├── node-iam/
│       │   └── worker-lifecycle/
│       ├── platform/
│       ├── edge/
│       └── environments/
│           ├── dev/
│           └── prod/
├── odoo/
│   ├── addons/
│   │   └── stockai_procurement/
│   └── bootstrap/
│       └── bootstrap.py
├── scripts/
├── src/
│   └── procurement/
│       ├── adapters/
│       │   ├── aws/
│       │   └── odoo/
│       ├── agent/
│       ├── api/
│       ├── bootstrap/
│       │   ├── api.py
│       │   └── mcp.py
│       ├── domain/
│       ├── mcp_server/
│       ├── observability/
│       └── ports/
├── tests/
│   ├── infra/
│   ├── integration/
│   ├── kubernetes/
│   ├── smoke/
│   ├── support/
│   └── unit/
├── compose.yaml
├── Makefile
├── pyproject.toml
└── uv.lock
```

Boundary rules:

- `procurement.domain` contains deterministic types and policy with no FastAPI,
  MCP SDK, Odoo, AWS, or LangGraph imports.
- `procurement.ports` contains framework-neutral, consumer-owned interfaces.
  The agent uses the LLM, MCP-client, and repository ports. Procurement MCP uses
  the ERP port.
- `procurement.agent` depends on domain types and ports, never on the MCP server
  implementation, Odoo adapter, FastAPI, or concrete AWS adapters.
- `procurement.api` owns HTTP, sessions, CSRF, RBAC, and graph orchestration. It
  calls Procurement MCP only through `procurement.ports.mcp`.
- `procurement.mcp_server` owns tool schemas, authorization, validation, Odoo
  operations, write idempotency, and independent approval verification. It does
  not import API, agent, or Bedrock implementation code.
- `procurement.adapters` contains concrete implementations but does not compose
  processes or import API, agent, or MCP implementation modules.
- `procurement.bootstrap.api` and `procurement.bootstrap.mcp` are the two
  composition roots. They are the only modules that connect concrete adapters
  to their owning runtime.
- Shared observability code contains framework-neutral logging and metric
  primitives. API- and MCP-specific middleware and collectors remain owned by
  their respective services.
- `frontend` uses only the versioned API and never receives AWS, Odoo, or
  Cognito tokens.
- `odoo/addons/stockai_procurement` owns only typed monthly budgets, explicit
  revision-bound PO methods, typed current preference records with monotonic
  revisions and Odoo change tracking, constraints, access control, and
  administration views. It contains
  no LLM, AWS client, system-prompt editor, supplier communication, payment
  operation, autonomous scheduler, or direct PO-state write.
- `odoo/bootstrap/bootstrap.py` is finite deployment/bootstrap code executed
  with the Odoo ORM. It is not imported by normal Odoo requests, API, or MCP
  processes and is the only code allowed to create the initial integration
  identity/key.

An automated import-boundary test will enforce these rules.

## 5. Per-task delivery workflow

After all planning gates are satisfied, each implementation task follows this
workflow:

1. Start the task from the latest protected `main` on a branch such as
   `feature/t01-python-foundation`.
2. Restate the task, affected requirements, and intended tests before editing.
3. Add a failing behavior test or failing configuration validation first when
   the task changes behavior.
4. Make the smallest change that satisfies the task.
5. Run the task-specific checks and the relevant regression suite.
6. Update `docs/implementation-status.md`, documentation, metrics, and
   dashboards when the task changes observable behavior.
7. Commit the reviewable task on its feature branch.
8. Merge the feature branch locally into `dev`, resolve conflicts, and push
   `dev` directly.
9. Allow the dev workflow and Argo CD to publish and reconcile the immutable
   artifact; validate it in the dev namespace.
10. From the feature branch, run `make promote-dev`, review and commit the
    exact dev-validated prod digest/provenance changes, and open its pull
    request to `main`.
11. Merge only after blocking tests and validation pass and the report-only
    Docker Scout job has recorded its available scan/report outcome.
12. Let the merge place the prepared prod digest on `main`; let the main
    workflow verify it without rebuilding or committing, let prod Argo CD
    reconcile it, then perform the task’s production smoke check.
13. Do not start a dependent task until the prior task is in `main` and prod is
    healthy.

`dev` must remain releasable: unrelated tasks are not combined when they
should not be promoted together. An urgent hotfix branches from `main`, follows
the same validation and promotion controls, and is then reconciled back into
`dev`.

Documentation-only planning tasks use their applicable approval workflow but
do not build or deploy images.

## 6. Common definition of done

Every task must satisfy the applicable parts of this definition:

- The task scope is limited to the files and behavior named in the task.
- Tests are deterministic and use fictional data.
- LLM, AWS, Odoo, and external APIs are mocked in unit tests.
- New behavior has unit tests and, when it crosses a process boundary, an
  integration or smoke test.
- Errors use safe stable codes and do not expose prompts, secrets, raw vendor
  data, stack traces, or upstream responses.
- State-changing operations are authenticated, authorized, validated,
  idempotent, and revision-aware where relevant.
- Structured logs and meaningful metrics are updated with the behavior.
- No secret or real commercial data is committed or logged.
- Dependency and image versions are pinned.
- The relevant local checks pass; passing is never claimed without executing
  them.
- The implementation status records evidence and remaining limitations.
- Dev validation succeeds before the exact artifact is promoted to prod.
- Prod returns to a healthy state after promotion.

## 7. Planned validation commands

Exact command flags may be adjusted for the pinned tool versions, but the
capabilities and Make targets must remain stable.

| Target | Planned checks |
|---|---|
| `make format-check` | Ruff formatting and frontend formatting check |
| `make lint` | Ruff, mypy, ESLint, import-boundary checks, and `actionlint` |
| `make test-unit` | Python and React unit tests with JUnit and coverage output |
| `make test-integration` | Real API/LangGraph-to-MCP Streamable HTTP tests with deterministic dependencies |
| `make test-e2e` | Local browser/API happy path and one representative safe failure |
| `make build` | Frontend build and four OCI image builds after T11A adds the StockAI Odoo image |
| `make compose-validate` | Render Compose configuration and verify service health |
| `make terraform-validate` | Format, initialize without apply, validate, lint, plan-test every root, and package/unit-test lifecycle Lambda code |
| `make kubernetes-validate` | Render both Kustomize overlays and run schema/policy checks |
| `make security-scan` | Dependency, secret, filesystem, configuration, and image checks; Docker Scout remains required in CI |
| `make promote-dev` | From a clean feature branch, verify the `origin/dev` release and prepare the exact prod digest/provenance changes without commit, push, merge, AWS, or Kubernetes access |
| `make smoke-dev` | Public HTTPS, auth, real Bedrock, real MCP, real Odoo, DynamoDB, audit, metrics, and logs |
| `make smoke-prod` | Same critical path against prod with prod-only fictional seed data |
| `make test-resilience` | Detailed automated retry/shutdown/lifecycle cases plus representative interruption, HPA/manual capacity, snapshot recovery, and one inactive/startup drill |
| `make verify-release` | Verify the immutable release core, single application-content identity, append-only exact-release validation, and exact four-digest prod promotion |

The full pull-request suite invokes all offline deterministic checks. Live
Bedrock and deployed-environment smoke tests do not run on ordinary pull
requests; they run after deployment with explicit environment credentials.

## 8. Implementation phases and tasks

### Phase 1 — Local walking skeleton

The phase exit is a runnable local user path:

```text
React → FastAPI 202 scan → LangGraph → real Streamable HTTP MCP call
      → fake Odoo adapter → approval-ready result → React polling
```

The path includes structured logs, request/LLM/MCP metrics, one happy-path
integration test, and one representative failure test.

#### T01 — Establish the Python quality and package foundation

**Files**

- Create `pyproject.toml`, `uv.lock`, `.python-version`, `.editorconfig`,
  `.env.example`, `Makefile`, and `README.md`.
- Create `src/procurement/__init__.py` and initial package directories.
- Create `tests/conftest.py`, `tests/unit/test_architecture.py`, and
  `docs/implementation-status.md`.
- Update `.gitignore` only to track planned project documentation while
  continuing to ignore untracked course source material and all local secrets,
  caches, environments, reports, and generated artifacts.

**Work and tests**

- [x] **Step 1:** Add an initially failing import-boundary test.
- [x] **Step 2:** Configure pinned runtime and development dependencies for FastAPI,
   LangGraph, the Python MCP SDK, Pydantic, boto3, HTTP clients, pytest,
   Ruff, and mypy.
- [x] **Step 3:** Define stable Make targets without adding application behavior.
- [x] **Step 4:** Make the boundary test pass with the empty deep-module structure.

**Verification**

- Run dependency lock verification, `make format-check`, `make lint`, and
  `make test-unit`.
- Confirm that `git status` contains no virtual environment, secret, cache, or
  generated report.

**Dependencies:** Approved plan and explicit implementation authorization.

**Requirements:** CR-01, CR-03, CR-13, CR-15.

**Complete when:** A clean clone can install pinned dependencies and run the
empty quality suite through documented commands.

#### T02 — Define domain language, state, contracts, and error taxonomy

**Files**

- Create `src/procurement/domain/models.py`,
  `src/procurement/domain/states.py`,
  `src/procurement/domain/errors.py`, and
  `src/procurement/domain/identifiers.py`.
- Create `tests/unit/domain/test_models.py`,
  `tests/unit/domain/test_states.py`, and
  `tests/unit/domain/test_errors.py`.

**Work and tests**

- [x] **Step 1:** Test bounded identifiers, amounts, currencies, dates, quantities, evidence
   references, revisions, and case-state transitions.
- [x] **Step 2:** Implement the states in specification section 7.2 without business policy.
- [x] **Step 3:** Define the stable error envelope and retryability classification.
- [x] **Step 4:** Reject unknown states, invalid transitions, unbounded text, negative money,
   invalid quantities, and mismatched environments.

**Verification:** Run domain unit tests, type checking, and import-boundary
checks.

**Dependencies:** T01.

**Requirements:** CR-02, CR-05, CR-13, CR-15; spec sections 7 and 13.3.

**Complete when:** Domain objects cannot represent invalid identifiers,
environment crossings, or illegal case transitions.

#### T03 — Add the API process, health endpoints, logging, and metrics baseline

**Files**

- Create `src/procurement/api/app.py`,
  `src/procurement/api/config.py`,
  `src/procurement/api/errors.py`,
  `src/procurement/api/lifecycle.py`, and
  `src/procurement/api/routes/health.py`.
- Create `src/procurement/observability/logging.py` and
  `src/procurement/observability/metrics.py`.
- Create `tests/unit/api/test_health.py`,
  `tests/unit/api/test_errors.py`, and
  `tests/unit/observability/test_redaction.py`.

**Work and tests**

- [x] **Step 1:** Test `/health/live`, `/health/ready`, `/health/dependencies`, and `/metrics`.
- [x] **Step 2:** Test the safe error envelope and correlation-ID propagation.
- [x] **Step 3:** Test JSON log fields and redaction of secrets, prompts, model output,
   prices, budgets, manager notes, and upstream errors.
- [x] **Step 4:** Implement process liveness separately from dependency readiness.
- [x] **Step 5:** Expose request count, error count, and latency without high-cardinality
   labels.

**Verification:** Run API and observability unit tests; start the API locally
and inspect health, metrics, and one sanitized request log.

**Dependencies:** T02.

**Requirements:** CR-04, CR-05, CR-12, CR-15; spec sections 13, 20, and 21.

**Complete when:** The process exposes safe health, error, log, and metrics
contracts without depending on Odoo, Bedrock, or AWS.

#### T04 — Build the minimal Procurement MCP server over real transport

**Files**

- Create `src/procurement/ports/erp.py`,
  `src/procurement/mcp_server/server.py`,
  `src/procurement/mcp_server/auth.py`,
  `src/procurement/mcp_server/schemas.py`, and
  `src/procurement/mcp_server/tools/candidates.py`.
- Create `tests/support/fake_odoo/adapter.py`,
  `tests/unit/mcp_server/test_candidates.py`, and
  `tests/integration/test_mcp_transport.py`.

**Work and tests**

- [x] **Step 1:** Test `list_replenishment_candidates` in isolation with strict inputs and
   bounded typed outputs.
- [x] **Step 2:** Test missing/wrong bearer credentials, malformed requests, response schema
   validation, timeout mapping, and safe errors.
- [x] **Step 3:** Start the actual MCP server and call it through the Python MCP client using
   Streamable HTTP; no direct function-call substitute is accepted.
- [x] **Step 4:** Add MCP call count, duration, failures, timeouts, and retries to metrics and
   structured logs.

**Verification:** Run the MCP unit suite and the real-transport integration
test.

**Dependencies:** T03.

**Requirements:** CR-05, CR-06, CR-12, CR-13, CR-15; spec section 11.

**Complete when:** A real client discovers and calls the first domain tool over
authenticated Streamable HTTP and receives a validated fictional candidate.

#### T05 — Implement the minimal LangGraph scan and asynchronous API

**Files**

- Create `src/procurement/ports/llm.py`,
  `src/procurement/ports/mcp.py`,
  `src/procurement/agent/state.py`,
  `src/procurement/agent/graph.py`,
  `src/procurement/agent/nodes/walking_skeleton.py`,
  `src/procurement/api/routes/scans.py`,
  `src/procurement/api/routes/internal.py`, and
  `src/procurement/api/services/scans.py`.
- Create `tests/support/fakes/llm.py`,
  `tests/unit/agent/test_walking_skeleton.py`,
  `tests/unit/api/test_scans.py`, and
  `tests/integration/test_api_agent_mcp.py`.
- Modify `tests/unit/test_architecture.py`.

**Work and tests**

- [x] **Step 1:** Test a coded LangGraph that calls MCP, invokes a fake structured LLM port,
   and returns one approval-ready read-only result.
- [x] **Step 2:** Test one MCP timeout path that produces a safe unresolved result.
- [x] **Step 3:** Extend the architecture test so API/agent cannot import the MCP server or
   Odoo implementation and MCP cannot import API/agent or the Bedrock implementation.
- [x] **Step 4:** Implement `POST /api/v1/scans` as `202 Accepted`, plus scan list/detail
   polling endpoints.
- [x] **Step 5:** Implement `POST /internal/v1/scans` with a separate narrow Cron credential;
   do not reuse a human session.
- [x] **Step 6:** Enforce one local scan lock and a 120-second non-human workflow deadline.
- [x] **Step 7:** Add scan, LLM, MCP, retry, and result metrics.

**Verification:** Run unit tests and the API → graph → real MCP transport
integration test.

**Dependencies:** T04.

**Requirements:** CR-03, CR-04, CR-05, CR-06, CR-12, CR-13.

**Complete when:** The API never holds a long request open and a polled scan
shows either a safe approval-ready fictional result or an explicit failure.

#### T06 — Build the minimal React user path

**Files**

- Create `frontend/package.json`, `frontend/package-lock.json`,
  `frontend/tsconfig.json`, `frontend/vite.config.ts`,
  `frontend/src/main.tsx`, `frontend/src/App.tsx`,
  `frontend/src/api/client.ts`, `frontend/src/pages/OverviewPage.tsx`,
  `frontend/src/pages/ScanPage.tsx`, and initial accessible styles.
- Create `frontend/tests/overview.test.tsx`,
  `frontend/tests/scan.test.tsx`, and
  `frontend/tests/api-client.test.ts`.

**Work and tests**

- [x] **Step 1:** Test loading, empty, success, manual-review, and safe-error states.
- [x] **Step 2:** Implement a manual scan button, 202 handling, bounded polling with cleanup,
   and scan result display.
- [x] **Step 3:** Avoid embedding configuration or tokens in the browser bundle.
- [x] **Step 4:** Meet basic keyboard, label, focus, and contrast checks.

**Verification:** Run frontend lint, type checks, unit tests, and production
build.

**Dependencies:** T05.

**Requirements:** CR-04, CR-14, CR-15; spec section 14.

**Complete when:** A local user can trigger and inspect the walking-skeleton
scan from a production-built React application.

#### T07 — Close the local walking-skeleton gate

**Files**

- Add `tests/integration/test_walking_skeleton.py`,
  `tests/integration/test_walking_skeleton_failure.py`, and
  `scripts/run-local-skeleton.sh`.
- Create `src/procurement/bootstrap/api.py`,
  `src/procurement/bootstrap/mcp.py`,
  `src/procurement/api/observability.py`, and
  `src/procurement/mcp_server/observability.py`.
- Modify `src/procurement/observability/logging.py`,
  `src/procurement/observability/metrics.py`, and
  `tests/unit/test_architecture.py`.
- Update `README.md` and `docs/implementation-status.md`.

**Work and tests**

- [x] **Step 1:** Test the full local happy path across actual API and MCP processes.
- [x] **Step 2:** Test a representative MCP timeout, including retry count, final error,
   logs, and metrics.
- [x] **Step 3:** Verify the interaction contains a LangGraph run and a real MCP transport
   call.
- [x] **Step 4:** Add API and MCP composition roots that construct only their owned adapters
   and configuration, then start the two real processes from the local script.
- [x] **Step 5:** Keep shared observability primitives framework-neutral and move API- and
   MCP-specific middleware and collectors into their owning service modules.
- [x] **Step 6:** Extend architecture tests for the composition-root and observability
   ownership rules.
- [x] **Step 7:** Document one command to run and one command to verify the skeleton.

**Verification:** Run `make test-unit`, `make test-integration`, and a manual
browser check.

**Dependencies:** T06.

**Requirements:** CR-03, CR-04, CR-05, CR-06, CR-12, CR-13, CR-14.

**Complete when:** Phase 1 is reproducible, tested, and demonstrable without
live AWS or Odoo.

### Phase 2 — Containers and real Odoo boundary

#### T08 — Containerize the three project services

**Files**

- Create `docker/api.Dockerfile`, `docker/mcp.Dockerfile`,
  `docker/frontend.Dockerfile`, `docker/nginx.conf`, and `.dockerignore`.
- Modify `pyproject.toml` to define fixed `stockai-api` and `stockai-mcp`
  process entry points.
- Create `tests/config/test_container_contracts.py`.

**Work and tests**

- [x] **Step 1:** Add configuration tests for non-root execution, fixed entry points, health
   checks, no development server, minimal build context, and no copied secret.
- [x] **Step 2:** Define `stockai-api` and `stockai-mcp` package entry points and make each
   backend image start only its corresponding composition root.
- [x] **Step 3:** Use multi-stage builds and pinned base-image digests.
- [x] **Step 4:** Ensure the frontend proxies `/api` and `/auth` to FastAPI on the same origin.
- [x] **Step 5:** Define writable paths explicitly so later read-only root filesystems work.

**Verification:** Build all three images, run image configuration tests, start
each image, and inspect health.

**Dependencies:** T07.

**Requirements:** CR-04, CR-09, CR-15; spec sections 14, 17, and 18.2.

**Complete when:** Immutable local images serve the same tested walking
skeleton.

#### T09 — Create the reproducible local Compose environment

**Files**

- Create `compose.yaml`, `compose.test.yaml`,
  `tests/support/fake_odoo/app.py`, and `tests/e2e/test_local_stack.py`.
- Update `.env.example`, `Makefile`, and `README.md`.

**Work and tests**

- [x] **Step 1:** Run frontend, API, MCP, and deterministic fake Odoo as separate services.
- [x] **Step 2:** Add explicit networks, health checks, bounded resources, and disposable test
   volumes.
- [x] **Step 3:** Test happy path, no-valid-response failure, malformed fake Odoo response,
   and service timeout.
- [x] **Step 4:** Keep credentials fictional and injected from ignored local environment
   files.

**Verification:** Run `docker compose config`, `make compose-validate`, and
`make test-e2e`.

**Dependencies:** T08.

**Requirements:** CR-06, CR-13; spec section 22.4.

**Complete when:** A clean workstation can launch and verify the local stack
with one documented command.

#### T10 — Verify the Odoo 19 JSON-2 contract before broad implementation

**Files**

- Create `compose.odoo.yaml`, `scripts/odoo/probe_contract.py`,
  `scripts/odoo/probe_bootstrap.py`, `tests/contract/conftest.py`,
  `tests/contract/test_odoo_json2.py`, and `docs/odoo-contract.md`.
- Update `.env.example` and `Makefile` with fictional contract-only settings and
  one bounded `odoo-contract` command.

**Work and tests**

- [x] **Step 1:** Test that Compose uses the approved immutable Odoo and
   PostgreSQL digests, an isolated network, health checks, disposable contract
   volumes, fictional credentials, and no published PostgreSQL port.
- [x] **Step 2:** Start a clean database and install `purchase`, `stock`,
   `purchase_stock`, `contacts`, `account`, and `analytic` without an Odoo UI or
   production-console step.
- [x] **Step 3:** Run `probe_bootstrap.py` through `odoo shell --no-http` to
   create one contract-only user and expiring key through the ORM, write the raw
   key only to a mode-`0600` disposable file, rerun it to prove no duplicate
   user/key, and use the key for JSON-2 tests.
- [x] **Step 4:** Probe JSON-2 database selection, bearer failures, safe error
   sanitization, `/doc`, `fields_get`, and integration-user ACLs for the exact
   Purchase/Inventory/Contacts/Accounting/Analytic models listed in
   `docs/odoo-contract.md`.
- [x] **Step 5:** Exercise reordering rules, supplier pricelists, PO
   origin/reference and standard actions, receipts/backorders, returns,
   analytic distribution, and `write_date` behavior with sanitized fictional
   records.
- [x] **Step 6:** Assert the discovered negative contracts: Community has no
   standard `account_budget`, standard PO actions accept no expected revision,
   and independent JSON-2 calls cannot provide an atomic compare-and-act.
   Record the approved `stockai.procurement.budget` and
   `action_stockai_{update_draft,cancel_draft,confirm}` extension contracts as
   T11A responsibilities rather than pretending they are built-ins.
- [x] **Step 7:** Convert every standard-runtime claim into an executable test,
   run from a newly created database, and always remove the disposable key file,
   database, containers, and volumes.

**Stop condition**

If Odoo 19 Community lacks an approved-spec capability, or JSON-2 cannot safely
perform the required operation, do not create an invented substitute. Stop,
document evidence, revise `docs/spec.md` and this plan, and obtain the required
approval.

**Stop-condition discovery (2026-08-07):** The official
`odoo@sha256:4872f23288454b724fd2d26c176a418276c2b3552e9aa752f9396b59d864b3a0`
image contains Purchase, Inventory, Contacts, Accounting, and Analytic add-ons
but does not contain `account_budget`. In addition, JSON-2 API-key generation
requires an already-valid key, so it cannot bootstrap the first integration key
by itself. Standard JSON-2 calls also cannot atomically compare an expected PO
revision and perform confirmation or cancellation. See
`docs/odoo-contract.md`.

**Approved resolution:** The user selected one project Odoo add-on for the
monthly budget model and atomic PO methods, plus a one-time ORM bootstrap Job,
on 2026-08-07. The exact revision received user and course-staff approval and
the user explicitly authorized T10 to resume. T11A implements those extensions.

**Verification:** `make odoo-contract` passed all 9 tests in 55.53 seconds
against one newly created database and wrote
`reports/junit/contract.xml`. Teardown removed the raw key, database,
containers, networks, and volumes.

**Dependencies:** T09.

**Requirements:** CR-02, CR-06, CR-13, CR-15; spec section 12.

**Complete when:** Every standard Odoo/JSON-2 claim needed by T11A and T11B has
a passing clean-database contract, every known Community limitation has a
negative contract, and each approved project extension has an exact owning
task and executable acceptance contract.

#### T11A — Build the StockAI Odoo image, add-on foundation, and bootstrap

**Files**

- Create `docker/odoo.Dockerfile` and the add-on under
  `odoo/addons/stockai_procurement/`, including `__manifest__.py`, model files
  for budgets and purchase-order extensions, integration/configuration groups,
  ACLs, and record rules.
- Create `odoo/bootstrap/bootstrap.py`, `scripts/odoo/seed.py`, and
  `scripts/odoo/verify_seed.py`.
- Create `tests/config/test_odoo_image_contract.py`,
  `tests/contract/test_stockai_odoo_addon.py`, and
  `tests/integration/test_odoo_bootstrap.py`.
- Update `compose.odoo.yaml`, `.dockerignore`, `.env.example`, `Makefile`, and
  `docs/odoo-contract.md`.

**Work and tests**

- [x] **Step 1:** Test a `stockai.procurement.budget` record with required
   company, product category, analytic account, first-of-month period, company
   currency, non-negative amount, active flag, tracked changes, and uniqueness
   for company/category/month. Test that only the configuration administrator
   can create, update, or archive it and the integration user is read-only.
- [x] **Step 2:** Implement the minimum budget model, configuration and
   integration groups, ACLs, and record rules. Keep manifest dependencies to
   the verified `purchase_stock`, `account`, `analytic`, and `mail` modules; do
   not implement budget arithmetic, React administration, preference models,
   or MCP budget tools in this task.
- [x] **Step 3:** Test and implement one-record-only public methods
   `action_stockai_update_draft(expected, changes)`,
   `action_stockai_cancel_draft(expected)`, and
   `action_stockai_confirm(expected)`. Under a row lock, each must compare the
   expected `write_date`, state, vendor, currency, and total, reject stale or
   unauthorized calls without a write, allowlist update fields, and invoke the
   standard Odoo business method instead of assigning `state`.
- [x] **Step 4:** Test a concurrent write between snapshot and action, direct
   method calls by unauthorized users, multi-record calls, invalid states,
   a repeat after an already-committed action, standard-method failures, and
   bounded conflict results with no duplicate transition.
- [x] **Step 5:** Build one non-root StockAI Odoo image from the approved
   official digest. Copy only the add-on and finite bootstrap code, retain the
   upstream entrypoint, install only the existing locked `boto3==1.43.62`
   bootstrap dependency, expose no secret in layers, and make local Compose
   use this image.
- [x] **Step 6:** Implement the finite ORM bootstrap Job contract: find/create
   the stable integration login, enforce only the approved group, generate a
   named key with an expiry no later than three calendar months only when absent
   or explicitly rotating, send the raw key to an injected local test sink or
   exact environment Secrets Manager ARN without stdout/stderr exposure, and
   remove temporary authority on every exit path.
- [x] **Step 7:** Run bootstrap twice and assert one user, one active named key,
   unchanged secret on the second run, functional JSON-2 authentication, and no
   secret material in container output. Test explicit rotation creates and
   verifies the replacement before revoking the old key.
- [x] **Step 8:** Seed idempotent fictional dev/prod records for the happy path,
   over-budget path, no-valid-offer path, receipts, returns, and open POs,
   including monthly budget rows, then rerun and compare stable record counts
   and references.

**Verification:** Build the StockAI Odoo image, run add-on/ACL/concurrency
contracts against a clean database, rerun bootstrap and seed twice, authenticate
with the resulting key, rotate it once, and verify no raw key appears in logs or
image history.

**Dependencies:** T10.

**Requirements:** CR-02, CR-05, CR-06, CR-11, CR-13, CR-15; spec sections 8.6,
11, 12, 18.2, and 20.

**Complete when:** One reproducible StockAI Odoo image supplies the approved
Community extensions, clean environments bootstrap without manual UI work, and
budget/action/security contracts pass against real Odoo.

#### T11B — Implement the JSON-2 adapter and real Odoo-backed MCP read

**Files**

- Create `src/procurement/adapters/odoo/client.py` and
  `src/procurement/adapters/odoo/mappers.py`.
- Create `tests/unit/adapters/odoo/test_client.py`,
  `tests/unit/adapters/odoo/test_mappers.py`, and
  `tests/integration/test_mcp_real_odoo.py`.
- Update `src/procurement/bootstrap/mcp.py` and `docs/odoo-contract.md`.

**Work and tests**

- [x] **Step 1:** Test JSON-2 bearer/database headers, 10-second read timeout,
   at most two transient retries with bounded backoff, no retry on permanent
   errors, safe Odoo-error mapping, and response-size limits.
- [x] **Step 2:** Test strict mapping and rejection of missing, mistyped,
   cross-company, malformed-decimal, malformed-datetime, and unexpected-state
   Odoo output.
- [x] **Step 3:** Implement the narrow client and mappers against only the
   executable T10 contracts; do not expose generic model/method passthrough to
   MCP tools.
- [x] **Step 4:** Replace the fixture implementation of
   `list_replenishment_candidates` with the real adapter in the MCP composition
   root while retaining the deterministic fake for unit and E2E scenarios.
- [x] **Step 5:** Call the candidate tool over authenticated Streamable HTTP
   against the seeded StockAI Odoo image and verify sanitized logs and bounded
   Odoo/MCP metrics.

**Verification:** Run unit tests, the real-Odoo adapter integration, and one
walking-skeleton scan whose MCP call reads a seeded candidate over JSON-2.

**Verification result:** `make check` passed Ruff, strict mypy over 89 source
files, 5 architecture tests, and all 188 unit tests. `make test-integration`
passed all 9 tests in 150.38 seconds. `make odoo-contract` passed all 20 tests
in 237.30 seconds and wrote `reports/junit/contract.xml`. The real-Odoo test
seeded the derived StockAI image, read a candidate through authenticated MCP
Streamable HTTP and JSON-2, completed the existing LangGraph walking skeleton,
observed bounded Odoo/MCP metrics, and found no credential in captured logs.

**Dependencies:** T11A.

**Requirements:** CR-02, CR-05, CR-06, CR-13, CR-15; spec sections 11 and 12.

**Complete when:** Local Odoo is reproducible and the walking skeleton makes a
real, validated Odoo-backed MCP read.

### Phase 3 — Cloud runtime adapters

#### T12 — Add the Bedrock GPT-OSS adapter and structured-output boundary

**Files**

- Create `src/procurement/adapters/aws/bedrock.py`,
  `src/procurement/agent/recommendation_schema.py`, and
  `src/procurement/agent/prompts/procurement_system.md`.
- Create `tests/unit/adapters/aws/test_bedrock.py`,
  `tests/unit/agent/test_recommendation_schema.py`, and
  `tests/unit/agent/test_prompt_boundary.py`.
- Update `src/procurement/bootstrap/api.py`, `scripts/run-local-skeleton.sh`,
  `tests/support/local_skeleton.py`, `compose.yaml`, `.env.example`, and
  `README.md`, and create `tests/unit/bootstrap/test_api.py` so the API
  composition root can select and document the deterministic local substitute
  or the real Bedrock adapter explicitly while existing local paths pin local
  mode.

**Work and tests**

- [x] **Step 1:** Test that only `openai.gpt-oss-20b-1:0` can be invoked.
- [x] **Step 2:** Test the 30-second attempt timeout, at most two transient retries with
   exponential backoff/jitter, one schema-repair attempt, and final safe
   fallback.
- [x] **Step 3:** Test ineligible identifiers, changed arithmetic, missing budget
   acknowledgement, oversized text, injection-like business data, and token
   metric extraction.
- [x] **Step 4:** Implement the system-prompt sections defined in specification 9.4 without
   requesting or exposing hidden chain-of-thought.
- [x] **Step 5:** Test an explicit `local|bedrock` API setting, reject every other value, and
   prove that Bedrock mode constructs the approved client, prompt, schema, and validator
   while local mode remains deterministic and requires no AWS access.
- [x] **Step 6:** Exercise API scan creation and polling through LangGraph with a mocked MCP
   read and mocked boto3 Bedrock boundary; assert the schema-bound advisory result and
   aggregate LLM success/failure, latency, and token metrics. Keep the first live model call
   deferred to T23 and the full offer-comparison fallback and its final metrics deferred to
   T28.

**Verification:** Run the focused adapter, schema, prompt, bootstrap, and graph
tests with a mocked boto3 Bedrock client, then run the complete local quality
suite. A real model call is deferred to the dev smoke test.

**Verification result:** `make check` passed lock and format checks, Ruff,
strict mypy over 97 source files, 5 architecture tests, and all 208 unit tests.
The focused T12 suite passed 20 mocked-provider tests covering the fixed model
and region, disabled SDK retries, bounded adapter retries/timeouts, schema
repair/fallback, strict semantic validation, untrusted-text delimiting, token
metadata, hidden-reasoning suppression, explicit local/Bedrock selection, and
safe successful, invalid-output, and unavailable API-to-LangGraph paths. Three
focused real-transport integration regressions passed, and all Compose files
rendered successfully. A production wheel build also confirmed that the
version-controlled Markdown system prompt is packaged. No live Bedrock call
was made, as required by this task.

**Dependencies:** T11B.

**Requirements:** CR-03, CR-05, CR-12, CR-13, CR-15; spec sections 9 and 19.

**Complete when:** The API composition root can explicitly select the local or
Bedrock implementation; the Bedrock runtime path is mocked-provider tested
through LangGraph and remains advisory, schema-bound, observable, and incapable
of authorizing writes or altering deterministic values.

#### T12A — Close pre-T13 validation and reproducibility gaps

This is a bounded audit-remediation task. It introduces no new product behavior
and does not change the approved architecture.

**Files**

- Create `docker/odoo-requirements.in`, `docker/odoo-requirements.txt`,
  `tests/config/test_makefile_contract.py`, `odoo/bootstrap/sinks.py`,
  `tests/unit/odoo/__init__.py`, and
  `tests/unit/odoo/test_bootstrap_sinks.py`.
- Update `Makefile`, `docker/odoo.Dockerfile`,
  `tests/config/test_odoo_image_contract.py`, `odoo/bootstrap/bootstrap.py`,
  `tests/integration/test_odoo_bootstrap.py`, and
  `docs/implementation-status.md`.

**Work and tests**

- [x] **Step 1:** Add a failing Makefile contract test proving that `make lint`
   invokes the existing `npm --prefix frontend run lint` command, then add that
   command without changing the frontend toolchain. Keep `actionlint` assigned
   to T21, where the workflow files first exist.
- [x] **Step 2:** Replace the Dockerfile's direct `boto3==1.43.62` installation
   with a Python 3.12 lock generated from the single direct requirement in
   `docker/odoo-requirements.in` by `uv pip compile --generate-hashes`. Install
   `docker/odoo-requirements.txt` with pip `--require-hashes`; test that every
   resolved distribution is exactly pinned and hashed, the Dockerfile consumes
   only that lock, and the derived image still builds from the approved Odoo
   digest.
- [x] **Step 3:** Extract only the existing file and Secrets Manager sink
   boundary into `odoo/bootstrap/sinks.py`. With a mocked boto3 client, test
   exact-ARN validation, ARN-derived region selection, missing-secret reads,
   non-empty reads, and writes whose `SecretId` is exactly the configured ARN.
   Assert invalid or empty responses fail safely and the raw secret never
   appears in stdout, stderr, or logs.
- [x] **Step 4:** Parameterize the real-Odoo seed contract over both `dev` and
   `prod`. For each environment, run seed and verification twice, assert stable
   summaries and counts, and assert distinct `STOCKAI-DEV` and `STOCKAI-PROD`
   fictional references.
- [x] **Step 5:** Run the focused configuration and sink unit tests, frontend
   lint, the clean derived-image/Odoo contract suite, and `make check`; record
   the executed evidence and any remaining named deferrals in
   `docs/implementation-status.md`.

**Verification:** Run `make lint`, the focused Makefile/image/sink tests,
`make odoo-contract`, and `make check`. The Odoo contract must rebuild the
derived image from the hash-locked dependency file and exercise both seed
environments. No live AWS call is made.

**Verification result:** `make check` passed lock and format checks, Ruff,
strict mypy over 100 source files, frontend ESLint, 5 architecture tests, and
all 219 unit tests. The 18 focused Makefile, image, bootstrap-composition, and
mocked sink tests passed. The first clean image build exposed Debian's
record-less `urllib3`; the final image installs the seven exactly pinned,
SHA-256-hashed distributions into `/usr/local` with `--ignore-installed`
instead of mutating Debian-managed packages. The final `make odoo-contract`
run rebuilt that image and passed all 23 tests in 399.82 seconds, including
bootstrap rotation, two stable runs for each distinct fictional `dev` and
`prod` seed, and the authenticated real MCP/Odoo interaction. No live AWS call
was made.

**Dependencies:** T11A and T12.

**Requirements:** CR-08, CR-11, CR-13, CR-15; spec sections 12, 18, 20, and 22.

**Complete when:** `make lint` enforces ESLint, the derived Odoo image has a
fully pinned and hash-verified Python dependency closure, both fictional seed
environments are idempotently verified, and the exact Secrets Manager sink is
covered with a secret-safe mocked AWS test. `actionlint` remains explicitly
deferred to T21.

#### T13 — Add DynamoDB repositories and LangGraph checkpoint persistence

**Files**

- Create `src/procurement/ports/repositories.py`,
  `src/procurement/adapters/aws/dynamodb.py`,
  `src/procurement/adapters/aws/checkpointer.py`, and
  `src/procurement/domain/audit.py`.
- Create `tests/unit/adapters/aws/test_dynamodb.py`,
  `tests/unit/adapters/aws/test_checkpointer.py`,
  `tests/unit/domain/test_audit.py`, and
  `tests/integration/test_dynamodb_local.py`.
- Update `pyproject.toml`, `uv.lock`, `src/procurement/agent/graph.py`,
  `src/procurement/agent/state.py`, `src/procurement/domain/models.py`,
  `src/procurement/api/services/scans.py`, `src/procurement/api/app.py`,
  `src/procurement/api/routes/scans.py`, `src/procurement/bootstrap/api.py`,
  `.env.example`, `compose.yaml`, `compose.test.yaml`, `README.md`,
  `docs/implementation-status.md`, `tests/support/local_skeleton.py`, and the
  affected graph, API, bootstrap, integration, and Compose topology tests to
  pin the approved checkpoint package, select memory or DynamoDB persistence
  explicitly, and wire the selected repositories and checkpointer through the
  real API runtime path.
- Add a pinned DynamoDB Local service to the Compose test profile.

**Work and tests**

- [x] **Step 1:** Test environment-prefixed keys, conditional case creation, idempotency,
   optimistic revisions, strongly consistent approval reads, audit
   immutability, TTL fields, and pagination.
- [x] **Step 2:** Implement separate checkpoint and application repositories behind ports,
   with an explicit in-memory substitute retained for deterministic local unit tests.
- [x] **Step 3:** Pass the immutable procurement case ID as the LangGraph `thread_id` and
   persist only sanitized graph state without duplicating Odoo master data.
- [x] **Step 4:** Wire the DynamoDB application repository and checkpoint saver through the
   API composition root and verify scan polling plus graph resume after API process
   restart using DynamoDB Local.

**Verification:** Run mocked unit tests and the real DynamoDB Local integration
test.

**Verification result:** `make check` passed lock and format checks, Ruff,
strict mypy over 108 source files, ESLint, 5 architecture tests, and all 242
unit tests. Eight relevant real-process integration regressions passed in
21.96 seconds, including two tests against the pinned DynamoDB Local 3.3.0
profile: a completed API scan and its sanitized LangGraph checkpoint survived
API process replacement, and real conditional writes returned the original
case for an identical idempotent request while rejecting conflicting reuse.
The base, test, and explicit DynamoDB-profile Compose models rendered, and the
default-versus-profile topology contract passed. No live AWS call was made.

**Dependencies:** T12A.

**Requirements:** CR-05, CR-13, CR-15, CR-16; spec sections 9.5 and 15.

**Complete when:** Scan and graph state survive process restart and conditional
writes prevent duplicate case creation.

#### T14 — Implement Cognito-backed opaque sessions, RBAC, and CSRF

**Files**

- Create `src/procurement/api/auth/cognito.py`,
  `src/procurement/api/auth/session.py`,
  `src/procurement/api/auth/csrf.py`,
  `src/procurement/api/auth/rbac.py`, and
  `src/procurement/api/routes/auth.py`.
- Create `src/procurement/bootstrap/cognito.py`.
- Create `tests/unit/api/auth/test_cognito.py`,
  `tests/unit/api/auth/test_session.py`,
  `tests/unit/api/auth/test_csrf.py`, and
  `tests/unit/api/auth/test_rbac.py`.
- Create `tests/unit/bootstrap/test_cognito.py`.
- Create `tests/support/local_identity.py` and
  `tests/support/authenticated_api.py` as test-only composition helpers that
  cannot be selected by runtime environment configuration.
- Update `src/procurement/api/app.py`, `src/procurement/api/routes/health.py`,
  `src/procurement/api/routes/scans.py`, `src/procurement/bootstrap/api.py`,
  `src/procurement/ports/repositories.py`, and
  `src/procurement/adapters/aws/dynamodb.py` to wire authentication through the
  real API composition root and persist sessions in the application table.
- Update `pyproject.toml`, `uv.lock`, `.env.example`, `compose.yaml`,
  `compose.test.yaml`, `Makefile`, `scripts/run-local-skeleton.sh`, `README.md`,
  `docs/implementation-status.md`, and affected unit, integration, and Compose
  tests.
- Update `frontend/src/App.tsx`, `frontend/src/api/client.ts`, and
  `frontend/src/styles.css`; create `frontend/src/pages/SignInPage.tsx` and
  `frontend/tests/auth.test.tsx`; update the affected frontend client tests.

**Work and tests**

- [x] **Step 1:** Test authorization-code state/nonce validation, callback errors, secure
   cookies, session rotation/expiry/logout, CSRF, disabled self-signup
   assumptions, and officer/manager roles.
- [x] **Step 2:** Store only opaque browser cookies; store session records in DynamoDB.
- [x] **Step 3:** Keep a test-only local identity adapter that cannot be enabled in dev or
   prod configuration.
- [x] **Step 4:** Protect manual scan and dependency-health endpoints and add
   `/api/v1/session`.
- [x] **Step 5:** Add an idempotent bootstrap command for fictional officer and manager users
   and groups without emitting temporary credentials.

**Verification:** Run API and frontend auth tests and inspect the production
bundle for tokens or secret configuration.

**Verification result:** `make check` passed lock and format checks, Ruff,
strict mypy over 123 source files, ESLint, 5 architecture tests, and all 258
unit tests. All 17 frontend tests, TypeScript checking, ESLint, and the
production Vite build passed. The production bundle scan found no Cognito
configuration or token/secret names. Eight real-process integration tests
passed, including two against pinned DynamoDB Local 3.3.0 proving that a hashed
opaque session contains no Cognito tokens and remains valid after API process
replacement. All 5 Compose checks passed in 139.70 seconds across the rendered
topology and four deterministic frontend-to-Odoo scenarios. Base, test, and
DynamoDB-profile Compose models rendered successfully. No live AWS call was
made.

**Dependencies:** T13.

**Requirements:** CR-04, CR-13, CR-15; spec sections 6, 13, 14, and 20.

**Complete when:** Dev/prod configuration cannot start with local bypass auth
and only authorized roles can invoke protected routes.

### Phase 4 — AWS, Kubernetes, CI/CD, and walking-skeleton promotion

#### T15 — Bootstrap remote Terraform state and GitHub OIDC

**Files**

- Create `infra/terraform/bootstrap/main.tf`,
  `infra/terraform/bootstrap/variables.tf`,
  `infra/terraform/bootstrap/outputs.tf`,
  `infra/terraform/bootstrap/versions.tf`,
  `infra/terraform/bootstrap/terraform.tfvars.example`, and
  `docs/runbooks/terraform-bootstrap.md`.
- Create `tests/infra/test_terraform_bootstrap.py`.

**Work and tests**

- [x] **Step 1:** Validate encrypted versioned state storage, public-access blocking,
   locking, retention protection, and narrowly scoped GitHub OIDC trust.
- [x] **Step 2:** Keep bootstrap state separate from application log storage.
- [x] **Step 3:** Parameterize account, repository, administrator CIDR, and state names;
   never commit account-specific values.
- [x] **Step 4:** Document the reproducible CLI bootstrap without AWS Console creation.

**Verification:** Run format, init, validate, static checks, and a reviewed plan
before any apply. After authorized apply, verify encryption, versioning,
locking, and OIDC claims.

**Verification result:** Eight red-green static contracts passed for dedicated
encrypted/versioned/private S3 state, encrypted on-demand DynamoDB locking,
retention protection, exact immutable GitHub pull-request and protected-
environment OIDC subjects, state-scoped role permissions, parameterized inputs,
and the CLI-only runbook. Terraform `1.15.8` formatting, initialization with
the locked HashiCorp AWS provider `6.58.0`, and provider-schema validation
passed. `make check` passed lock/format checks, Ruff, strict mypy over 125 source
files, ESLint, 5 architecture tests, and all 258 unit tests. `git diff --check`
passed. No account-specific tfvars or credentials were supplied, so the real
plan, apply, and post-apply AWS verification remain behind the separate
infrastructure approval gate; no live AWS call was made.

**Dependencies:** T14 and explicit infrastructure apply approval when the task
is executed.

**Requirements:** CR-10, CR-11, CR-15, CR-16; spec sections 16 and 18.

**Complete when:** All later Terraform roots can use protected remote state and
keyless GitHub authentication.

#### T16 — Provision the network, control plane, worker ASGs, and node IAM

**Files**

- Create `infra/terraform/modules/network/{main,variables,outputs}.tf`.
- Create `infra/terraform/modules/compute/{main,variables,outputs}.tf` and
  `infra/terraform/modules/compute/worker-user-data.sh.tftpl`.
- Create `infra/terraform/modules/node-iam/{main,variables,outputs}.tf`.
- Create `infra/terraform/platform/{main,variables,outputs,versions}.tf` and
  `infra/terraform/platform/terraform.tfvars.example`.
- Create `tests/infra/plan.py` and `tests/infra/test_platform_plan.py`.

**Interfaces**

- Consumes: T15 remote-state bucket/lock identifiers, administrator CIDR,
  owner-prefixed cluster name, owner tag, AMI ID, and the approved `us-east-1`
  region. The shared-account defaults are `weam-stockai` and `Owner = weam`.
- Produces: Terraform outputs `control_plane_instance_id`,
  `control_plane_private_ip`, `dev_worker_asg_name`, `prod_worker_asg_name`,
  `dev_worker_az`, `prod_worker_az`, `dev_worker_role_name`,
  `prod_worker_role_name`, `control_plane_role_name`, `alb_subnet_ids`, and
  `worker_security_group_id`.

**Work and tests**

- [x] **Step 1: Add failing Terraform-plan assertions**

  Parse `terraform show -json` through
  `tests.infra.plan.resources(plan: dict, resource_type: str) -> list[dict]`.
  Assert one `aws_instance` control plane, two `aws_launch_template` resources,
  and two `aws_autoscaling_group` resources whose effective settings are:

  ```python
  assert {(r["values"]["min_size"], r["values"]["desired_capacity"], r["values"]["max_size"])
          for r in resources(plan, "aws_autoscaling_group")} == {(1, 1, 3)}
  assert len(resources(plan, "aws_autoscaling_policy")) == 0
  assert len(resources(plan, "aws_eks_cluster")) == 0
  assert len(resources(plan, "aws_nat_gateway")) == 0
  ```

- [x] **Step 2: Run the focused test and confirm the missing resources fail**

  Run: `pytest tests/infra/test_platform_plan.py -v`

  Expected: FAIL because the platform root and ASG resources do not exist.

- [x] **Step 3: Implement the minimum network and compute resources**

  Create one VPC, two public subnets in different Availability Zones, routing,
  Internet Gateway, restricted control-plane administration, one fixed
  `t3.medium` control plane, and separate single-AZ dev/prod worker launch
  templates and ASGs. Use this exact capacity contract in the compute module:

  ```hcl
  variable "worker_capacity" {
    type = object({ min = number, desired = number, max = number })
    default = { min = 1, desired = 1, max = 3 }
    validation {
      condition = (
        (var.worker_capacity.min == 0 && var.worker_capacity.desired == 0 && var.worker_capacity.max == 3) ||
        (var.worker_capacity.min == 1 && var.worker_capacity.desired >= 1 && var.worker_capacity.desired <= 3 && var.worker_capacity.max == 3)
      )
      error_message = "use inactive 0/0/3 or active 1/<1..3>/3 capacity"
    }
  }
  ```

  Encrypt every root volume, cap it at 30 GB, use EC2 health checks, attach no
  scaling policy, and place each ASG only in the subnet/AZ selected for its
  environment. Prefix nameable resources with `weam-stockai-` and tag every
  taggable resource with `Owner = weam`. Configure planned instance refresh
  for launch-before-terminate overlap where capacity permits, while documenting
  that EC2 `InService` does not itself prove Kubernetes Ready or zero downtime.

- [x] **Step 4: Implement separate least-privilege node roles and network rules**

  Give the control plane no procurement-data permissions. Create distinct
  dev/prod worker roles and instance profiles, SSM managed-instance channels,
  environment tags, and no `AmazonEKSClusterPolicy`. Restrict SSH and the API
  server to the configured administrator CIDR and required node traffic; do
  not expose NodePort, MCP, or database ports publicly.

- [x] **Step 5: Add inactive-capacity and quota validations**

  Permit only the documented inactive `{ min = 0, desired = 0, max = 3 }`
  state, reject `desired < min`, and document that an apply must verify six
  vCPUs for the normal baseline plus the exact temporary dev capacity being
  tested.

- [x] **Step 6: Run Terraform and policy checks**

  Run: `terraform -chdir=infra/terraform/platform fmt -check`

  Run: `terraform -chdir=infra/terraform/platform init -backend=false`

  Run: `terraform -chdir=infra/terraform/platform validate`

  Run: `pytest tests/infra/test_platform_plan.py -v`

  Expected: PASS with one fixed control plane, two isolated worker ASGs, no
  scaling policies, no EKS, and no NAT Gateway.

- [x] **Step 7: Commit the independently reviewable foundation**

  ```bash
  git add infra/terraform/modules/network infra/terraform/modules/compute infra/terraform/modules/node-iam infra/terraform/platform tests/infra
  git commit -m "feat(infra): provision isolated worker ASGs"
  ```

**Verification:** Run Terraform checks and inspect the reviewed plan for count,
instance type, volume size, ingress, IAM actions, and monthly-cost assumptions.

**Verification result:** The focused test first failed because the platform
root and ASG resources were absent. Twelve final tests parse `terraform show -json`
and cover the approved topology and outputs, exact active/inactive
capacity, invalid-capacity rejection, isolated temporary environment overrides,
AZ/subnet isolation, encrypted bounded root volumes, numbered launch-template
refreshes, restricted ingress, separate SSM-only node roles, shared-account
`weam-stockai-` names and `Owner = weam` tags, and absence of EKS, NAT, and
scaling policies. Terraform `1.15.8` formatting and provider-schema validation
passed with locked AWS provider `6.58.0`; all 20
infrastructure tests passed. `make check` passed
lock/format checks, Ruff, strict mypy over 127 source files, ESLint, 5
architecture tests, and all 258 unit tests. The plan tests used fake credentials
and disabled AWS account calls only in an isolated temporary configuration
copy. No account-specific remote-backend plan, quota/cost check, apply, or live
AWS verification was performed; those retain the explicit infrastructure gate.

**Dependencies:** T15.

**Requirements:** CR-07, CR-10, CR-15, CR-16; spec sections 16.1, 17.1, and 23.

**Complete when:** Terraform reproducibly creates the normal three-instance
foundation with one fixed control plane and isolated dev/prod worker ASGs,
without EKS, automatic node scaling, or NAT Gateway.

#### T17 — Provision the ALB/ACM edge, environment AWS services, recovery, and budgets

**Files**

- Create `infra/terraform/modules/app-environment/{main,variables,outputs}.tf`.
- Create `infra/terraform/modules/edge/{main,variables,outputs}.tf` and
  `infra/terraform/edge/{main,variables,outputs,versions}.tf`.
- Create `infra/terraform/environments/dev/{main,variables,outputs,versions}.tf`
  and the matching files under `infra/terraform/environments/prod/`.
- Create `tests/infra/test_environment_plans.py`,
  `tests/infra/test_ingress_contract.py`, and
  `docs/runbooks/cost-and-shutdown.md`.

**Interfaces**

- Consumes: T16 VPC/subnet/security-group outputs, both ASG names and
  Availability Zones, six approved hostnames, Route 53 zone ID, and the fixed
  NGINX NodePort.
- Produces: `dev_target_group_arn`, `prod_target_group_arn`, ACM/ALB/DNS
  outputs, and an environment-keyed `data_volumes` object containing exact
  `odoo`, `postgresql`, and `prometheus` volume IDs and Availability Zones.

**Work and tests**

- [x] **Step 1: Write failing environment, edge, and volume plan tests**

  Assert separate DynamoDB, Secrets Manager, Cognito, Loki prefixes, and IAM
  resource scopes. Assert the normal plan grants no Secrets Manager write,
  while the explicit bootstrap variant grants only `PutSecretValue` on its
  environment's exact Odoo-key ARN and cannot target the other environment.
  Assert six encrypted `gp3` data volumes keyed exactly as:

  ```python
  expected = {
      ("dev", "odoo"), ("dev", "postgresql"), ("dev", "prometheus"),
      ("prod", "odoo"), ("prod", "postgresql"), ("prod", "prometheus"),
  }
  actual = {(r["values"]["tags"]["Environment"], r["values"]["tags"]["Workload"])
            for r in resources(plan, "aws_ebs_volume")}
  assert actual == expected
  assert all(r["values"]["encrypted"] for r in resources(plan, "aws_ebs_volume"))
  ```

  Assert each volume is 5 GiB and uses its matching ASG Availability Zone.

- [x] **Step 2: Run the focused tests and confirm they fail**

  Run: `pytest tests/infra/test_environment_plans.py tests/infra/test_ingress_contract.py -v`

  Expected: FAIL because environment, edge, and retained-volume resources do
  not exist.

- [x] **Step 3: Provision environment application services**

  Add separate checkpoint/application tables, PITR/TTL/retention, Secrets
  Manager entries, Cognito pools/clients/groups, encrypted Loki S3 prefixes,
  public-access blocking, and environment/resource-scoped IAM. Scope Bedrock
  invocation to `openai.gpt-oss-20b-1:0` only. Add a per-environment Odoo-key
  bootstrap policy scoped to `secretsmanager:PutSecretValue` on exactly that
  environment's Odoo-key ARN. A validated
  `enable_odoo_key_bootstrap = false` default controls its attachment to the
  matching worker role; no normal worker plan may contain that write action.

- [x] **Step 4: Provision the ALB, ACM, DNS, and ASG target membership**

  Create one internet-facing ALB across both public subnets, HTTP-to-HTTPS
  redirect, HTTPS host rules, and separate dev/prod instance target groups for
  the fixed NGINX NodePort. Attach each target group to only its environment
  ASG using `aws_autoscaling_attachment`; never enumerate worker instance IDs.
  Permit the NodePort and health check only from the ALB security group.

- [x] **Step 5: Provision retained data volumes and recovery**

  Create the six encrypted 5 GiB `gp3` volumes with `Environment`, `Workload`,
  `Cluster`, and `ManagedBy=Terraform` tags. Place each volume in its
  environment ASG's AZ. Configure DLM for seven daily crash-consistent
  snapshots of only the prod Odoo and PostgreSQL volumes; Prometheus and dev
  recovery use retained volumes without snapshot claims.

- [x] **Step 6: Provision budget and CloudWatch read contracts**

  Add $70 target and $90 review-ceiling notifications. Grant Grafana only the
  read-only CloudWatch metric-query actions needed for ALB, ASG, and later
  Lambda panels; application logs remain in Loki.

- [x] **Step 7: Validate both environment plans**

  Run Terraform format/init/validate/plan for `edge`, `environments/dev`, and
  `environments/prod`, then run:

  `pytest tests/infra/test_environment_plans.py tests/infra/test_ingress_contract.py -v`

  Expected: PASS for environment isolation, exact ASG target membership,
  certificate/DNS contracts, six retained volumes, snapshots, and excluded
  services.

- [x] **Step 8: Commit the independently reviewable services and edge**

  ```bash
  git add infra/terraform/modules/app-environment infra/terraform/modules/edge infra/terraform/edge infra/terraform/environments tests/infra docs/runbooks/cost-and-shutdown.md
  git commit -m "feat(infra): add edge services and retained data volumes"
  ```

**Verification:** Run plans for dev and prod and assert isolation, encryption,
retention, model ARN, ACM validation, DNS aliases, ALB listener/rules/ASG
attachments, all six volume placements, and absence of excluded AWS services.
After an authorized apply, verify the listener redirect and certificate
hostname; target health becomes an acceptance check after NGINX Ingress is
installed.

**Verification result:** The 14 focused plan tests first failed because the
environment and edge roots were absent, then passed after the minimum resources
were added. Terraform `1.15.8` formatting and provider-schema validation passed
for `edge`, `environments/dev`, and `environments/prod` with locked AWS provider
`6.58.0`; all 34 infrastructure tests passed. `make check` passed lock and
format checks, Ruff, strict mypy over 129 source files, ESLint, 5 architecture
tests, and all 258 unit tests. The Terraform plan tests used fake credentials,
disabled AWS account calls only in isolated temporary configuration copies, and
did not contact or mutate AWS. Account-specific remote-backend initialization,
the reviewed real plans, apply, cost confirmation, and post-apply ALB/ACM/DNS
verification remain behind the explicit infrastructure approval gate; target
health remains deferred until T18C installs NGINX Ingress.

**Dependencies:** T16.

**Requirements:** CR-08, CR-10, CR-15, CR-16; spec sections 15, 16, and 23.

**Complete when:** Every selected AWS application and edge service is
reproducible, environment-scoped where applicable, and justified by the
specification; public traffic has no direct worker path.

#### T18A — Automate the kubeadm node and cluster bootstrap

**Files**

- Create `infra/cluster/install-node.sh`,
  `infra/cluster/init-control-plane.sh`,
  `infra/cluster/join-worker.sh`,
  `infra/cluster/rotate-join-token.sh`,
  `infra/cluster/kubeadm-token-rotation.service`,
  `infra/cluster/kubeadm-token-rotation.timer`, and
  `docs/runbooks/cluster-bootstrap.md`.
- Create `infra/terraform/modules/cluster-bootstrap/{main,variables,outputs}.tf`
  and connect it from `infra/terraform/platform/main.tf`.
- Modify `infra/terraform/modules/compute/worker-user-data.sh.tftpl` to invoke
  the environment-aware join script.
- Create the pinned CNI resources under
  `deploy/kubernetes/cluster/network/`.
- Create `tests/infra/test_cluster_bootstrap.py`.

**Interfaces**

- Consumes: T16 control-plane instance/role, environment worker roles and ASG
  launch templates, cluster name, region, and private API endpoint.
- Produces: SSM parameter
  `/stockai/<cluster-name>/kubeadm/join-command`, finite-token rotation service,
  and workers whose kubelet node name equals EC2 private DNS and whose labels
  include `stockai.io/environment=dev|prod`.

**Work and tests**

- [x] **Step 1: Write failing bootstrap contract tests**

  Assert a Terraform-created SSM `SecureString`, exact control-plane
  `ssm:PutParameter`, exact worker `ssm:GetParameter`, no
  `AmazonEKSClusterPolicy`, `kubeadm token create --ttl 24h`, a 12-hour timer,
  strict join-command validation, private-DNS node naming, and environment
  labels/taints.

- [x] **Step 2: Run the tests and confirm the bootstrap contracts fail**

  Run: `pytest tests/infra/test_cluster_bootstrap.py -v`

  Expected: FAIL because the SSM parameter, rotation unit, and ASG join path do
  not exist.

- [x] **Step 3: Create the encrypted join-parameter and IAM boundary**

  Terraform creates the parameter with a non-secret placeholder and ignores
  only runtime value drift. The control plane may overwrite that exact ARN;
  both worker roles may decrypt/read that exact ARN. No plan, output, or log
  contains a live token.

- [x] **Step 4: Implement finite token rotation without shell evaluation**

  `rotate-join-token.sh` executes:

  ```bash
  join_command="$(kubeadm token create --ttl 24h --print-join-command)"
  aws ssm put-parameter --name "$parameter_name" --type SecureString --overwrite --value "$join_command"
  ```

  The systemd timer runs it after control-plane initialization and every 12
  hours. The script never enables command tracing and never prints
  `join_command`.

- [x] **Step 5: Implement environment-aware ASG worker join**

  Poll SSM with bounded backoff. Accept only a command matching the exact
  kubeadm endpoint/token/CA-hash grammar, split it into a Bash array, and append
  `--node-name "$private_dns"`; do not use `eval`. Configure kubelet labels and
  taints from the launch-template environment and reject any other value.

- [x] **Step 6: Pin and initialize the cluster and CNI**

  Pin Kubernetes, containerd, and the NetworkPolicy-capable CNI; keep
  kubeconfig restricted, business workloads off the control plane, and all
  steps idempotent after Terraform supplies outputs.

- [x] **Step 7: Validate scripts and a real replacement join**

  Run: `shellcheck infra/cluster/*.sh`

  Run: `pytest tests/infra/test_cluster_bootstrap.py -v`

  After authorized apply, replace one dev ASG instance and verify the new node
  uses private DNS, has only dev labels/taints and the dev role, reaches Ready,
  and never exposes the token in user-data, journal, or Terraform output.

- [x] **Step 8: Commit bootstrap automation**

  ```bash
  git add infra/cluster infra/terraform/modules/cluster-bootstrap infra/terraform/modules/compute infra/terraform/platform deploy/kubernetes/cluster/network tests/infra docs/runbooks/cluster-bootstrap.md
  git commit -m "feat(infra): automate secure ASG worker joins"
  ```

**Verification:** Run shell lint and CNI manifest checks, bootstrap the
authorized test cluster, inspect node roles/labels/taints, and run node/network
and restart checks.

**Implementation verification result:** The red run first failed on the absent
T18A scripts, systemd units, CNI resources, and Terraform module. The final six
focused contracts passed for the encrypted runtime-owned SSM parameter, exact
role-specific IAM, 24-hour token/12-hour rotation, strict non-`eval` join
validation, private-DNS node identity, environment labels/taints, pinned node
software and Calico, and automatic control-plane/worker user data. ShellCheck
`0.10.0`, Bash syntax checks, the pinned Calico Kustomization render, Terraform
`1.15.8` formatting/provider-schema validation, all 40 infrastructure tests,
and `make check` passed; the latter covered lock and formatting checks, Ruff,
strict mypy over 130 source files, ESLint, 5 architecture tests, and all 258
unit tests. The deterministic plans used fake credentials with refresh disabled
and performed no AWS mutation. The account-specific reviewed plan/apply and the
real dev replacement join in Step 7 remain behind the explicit infrastructure
approval gate and are not claimed as complete.

**Dependencies:** T17.

**Requirements:** CR-07, CR-08, CR-09, CR-10, CR-15; spec section 17.

**Complete when:** The self-managed cluster is reproducible, a replacement ASG
worker joins from a finite rotating SSM credential, and every worker is
hard-bound to its environment.

#### T18B — Automate bounded ASG worker termination cleanup

**Task status:** Complete with the accepted deferred live failure drill noted below.

**Files**

- Create `infra/terraform/modules/worker-lifecycle/main.tf`,
  `infra/terraform/modules/worker-lifecycle/variables.tf`, and
  `infra/terraform/modules/worker-lifecycle/outputs.tf`.
- Create `infra/terraform/modules/worker-lifecycle/lambda/node_cleanup.py` and
  `infra/terraform/modules/worker-lifecycle/lambda/requirements.txt`.
- Connect the module from `infra/terraform/platform/main.tf`.
- Create `tests/infra/test_node_cleanup.py`,
  `tests/infra/test_worker_lifecycle_plan.py`, and
  `docs/runbooks/worker-termination.md`.

**Interfaces**

- Consumes: T16 dev/prod ASG names, T16 control-plane instance ID, T18A's
  private-DNS kubelet naming contract, fixed region, and cluster name.
- Produces: `CleanupOutcome = clean | forced | failed`, CloudWatch metrics
  `StockAI/WorkerLifecycle:WorkerCleanupOutcome` and
  `StockAI/WorkerLifecycle:WorkerCleanupDuration`, and one termination hook per
  ASG with EventBridge-to-Lambda cleanup.
- Python functions:
  `parse_event(event: Mapping[str, Any]) -> TerminationEvent`,
  `cleanup_node(event: TerminationEvent, clients: AwsClients) -> CleanupResult`,
  and `handler(event: Mapping[str, Any], context: LambdaContext) -> dict[str, str]`.

**Work and tests**

- [x] **Step 1: Write failing Lambda unit tests**

  Cover valid dev/prod events, unknown ASG, malformed detail, EC2 private-DNS
  mapping, clean drain, drain timeout with forced deletion, SSM/control-plane
  failure, duplicate delivery, already-absent Node, heartbeats, and lifecycle
  completion. The core assertion is:

  ```python
  result = cleanup_node(event, fake_clients)
  assert result.outcome is CleanupOutcome.CLEAN
  fake_clients.autoscaling.complete_lifecycle_action.assert_called_once_with(
      AutoScalingGroupName="stockai-dev-workers",
      LifecycleHookName="stockai-worker-terminate",
      LifecycleActionResult="CONTINUE",
      InstanceId="i-0123456789abcdef0",
  )
  ```

- [x] **Step 2: Run Lambda tests and confirm they fail**

  Run: `pytest tests/infra/test_node_cleanup.py -v`

  Expected: FAIL because `node_cleanup.py` and its result types do not exist.

- [x] **Step 3: Implement strict event parsing and identity validation**

  Accept only `EC2 Instance-terminate Lifecycle Action` events from
  `aws.autoscaling`, require one of the two exact ASG names, resolve the EC2
  private DNS name and private IPv4 address, reject invalid node names or
  addresses, and verify the ASG/instance/environment contract before sending
  any command. A missing already-terminated instance is idempotent only after
  the signed event's ASG and instance fields pass validation.

- [x] **Step 4: Implement bounded SSM cleanup and heartbeat polling**

  Send `AWS-RunShellScript` only to the control plane with a script equivalent
  to:

  ```bash
  export KUBECONFIG=/etc/kubernetes/admin.conf
  internal_ip="$(kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')"
  environment="$(kubectl get node "$node_name" -o jsonpath='{.metadata.labels.stockai\.io/environment}')"
  [ "$internal_ip" = "$expected_private_ip" ] || exit 42
  [ "$environment" = "$expected_environment" ] || exit 43
  drain_rc=0
  kubectl cordon "$node_name" || true
  kubectl drain "$node_name" --ignore-daemonsets --delete-emptydir-data --force --timeout=120s || drain_rc=$?
  kubectl delete node "$node_name" --ignore-not-found=true
  printf 'CLEANUP_OUTCOME=%s\n' "$([ "$drain_rc" -eq 0 ] && printf clean || printf forced)"
  ```

  Live T18B preflight on 2026-08-11 confirmed that the control plane and both
  workers expose no `.spec.providerID`. The user approved replacing that
  unavailable check with the exact EC2-private-DNS plus private-IP and
  environment-label identity chain. This keeps the existing T18A node naming
  contract and avoids an unrelated kubelet/bootstrap replacement.

  Preserve the drain status, sanitize returned output, poll SSM while sending
  lifecycle heartbeats, and retry SSM's documented transient
  `InvocationDoesNotExist` read-after-write visibility race inside the existing
  poll bound. Attempt `CONTINUE` in `finally`. Never call `ABANDON`; the
  lifecycle hook itself supplies the ultimate fail-open bound.

  The first live dev drill on 2026-08-11 sent SSM command
  `3de296f3-c2bd-4942-a6a7-7c58842131fc`, which completed with response code
  zero and `CLEANUP_OUTCOME=clean`; the old Node disappeared and replacement
  `i-02ec53c048a80bb6f` joined Ready. Lambda nevertheless reported
  `ssm_unavailable` with zero heartbeats because its immediate invocation read
  raced SSM propagation. The approved minimal correction retries only that
  exact error and has a regression test reproducing the live sequence.

- [x] **Step 5: Make duplicate delivery and timeout behavior deterministic**

  Treat an absent Node as already clean, emit `forced` when drain fails but
  Node deletion is attempted, and emit `failed` when SSM cannot run. Configure
  Lambda timeout 240 seconds inside a 300-second lifecycle heartbeat timeout
  with default result `CONTINUE`.

- [x] **Step 6: Write failing Terraform lifecycle/IAM assertions**

  Assert two lifecycle hooks, one EventBridge rule filtered to both ASGs, one
  Lambda target/permission, a pre-created 14-day log group, Lambda alarms, and
  least privilege. `ssm:SendCommand` must name the control plane and
  `AWS-RunShellScript`; lifecycle heartbeat/completion must name only the two
  ASGs; `cloudwatch:PutMetricData` must use only the
  `StockAI/WorkerLifecycle` namespace. `ec2:DescribeInstances` is read-only `*`
  because AWS does not support a resource ARN for that action; application
  resources remain absent.

- [x] **Step 7: Run unit, package, and Terraform tests**

  Run: `pytest tests/infra/test_node_cleanup.py tests/infra/test_worker_lifecycle_plan.py -v`

  Run Terraform format/init/validate/plan for the platform root.

  Expected: PASS for clean/forced/failed/idempotent behavior, bounded lifecycle
  completion, EventBridge filtering, alarms, and IAM scope.

- [x] **Step 8a: Run the controlled normal dev termination acceptance test**

  Before application workloads exist, terminate the dev worker through its ASG
  and verify heartbeats, `clean`, old Node removal, replacement join, and
  correct labels/role. Retained-volume and application recovery are deferred
  to T23 after the full dev stack exists.

  On 2026-08-11 the normal live cleanup drill passed. The user explicitly
  deferred the unavailable-control-plane/SSM live drill and accepted T18B as
  finished with that limitation; its bounded fail-open behavior remains
  covered by automated tests and the operational runbook.

- **Accepted limitation: unavailable-control-plane/SSM live drill not run**

  Block control-plane SSM in a controlled test and verify bounded `failed`
  completion and the alert. This live drill was explicitly deferred and is not
  claimed as executed; its behavior is covered by automated tests.

- [x] **Step 9: Commit lifecycle automation**

  ```bash
  git add infra/terraform/modules/worker-lifecycle infra/terraform/platform tests/infra docs/runbooks/worker-termination.md
  git commit -m "feat(infra): automate worker termination cleanup"
  ```

**Verification:** Run Lambda unit tests, Terraform plan assertions, and the
controlled dev clean/fail-open drills. Inspect only sanitized Lambda/SSM logs.

**Dependencies:** T18A.

**Requirements:** CR-05, CR-07, CR-10, CR-12, CR-13, CR-15, CR-16; spec
sections 16.2, 17.10, 19, 20, 21, and 22.

**Complete when:** Both ASGs have tested, idempotent, bounded termination
cleanup; non-clean outcomes release the instance, alert, and have a verified
runbook without granting Lambda Kubernetes credentials.

#### T18C — Install and validate shared cluster controllers

**Task status:** Complete; live acceptance passed.

**Files**

- Create pinned resources under `deploy/kubernetes/cluster/ingress/`,
  `deploy/kubernetes/cluster/ebs-csi/`,
  `deploy/kubernetes/cluster/metrics/`, and
  `deploy/kubernetes/cluster/argocd/` for NGINX Ingress, the AWS EBS CSI driver,
  metrics-server, kube-state-metrics, Argo CD, cluster RBAC, and namespaces.
- Modify `infra/terraform/modules/node-iam/main.tf` and
  `tests/infra/test_platform_plan.py` for the control-plane EBS CSI policy.
- Create `tests/kubernetes/test_cluster_resources.py`.

**Interfaces**

- Consumes: T17 fixed NGINX NodePort/target groups, T18A worker labels/taints,
  and T16 control-plane infrastructure role.
- Produces: NGINX on every worker, EBS CSI topology key
  `topology.kubernetes.io/zone`, Kubernetes resource metrics for HPA, and Argo
  CD reconciliation without direct GitHub Actions deployment.

**Work and tests**

- [x] **Step 1: Write failing cluster-controller render tests**

  Assert pinned images, NGINX worker DaemonSet placement and exact NodePort,
  no cert-manager, EBS CSI controller placement on the control plane, no
  default dynamic StorageClass, metrics API availability, narrow RBAC, and no
  business workload toleration for the control-plane taint.

- [x] **Step 2: Run the focused test and confirm resources are absent**

  Run: `pytest tests/kubernetes/test_cluster_resources.py -v`

  Expected: FAIL because cluster-controller resources do not exist.

- [x] **Step 3: Install NGINX, metrics, and Argo CD resources**

  Run NGINX on every worker so each ASG target can pass the same NodePort
  health check. Install metrics-server and kube-state-metrics for HPA and
  observability. Install Argo CD with no GitHub Actions `kubectl` path. ACM
  terminates public TLS, so do not install cert-manager.

- [x] **Step 4: Install the pinned self-managed EBS CSI driver**

  Run the controller on the control plane with infrastructure-only,
  tag/resource-scoped EC2 volume operations; run the node DaemonSet on workers.
  Enable attach/detach/mount for the six pre-created volumes but create no
  dynamic provisioning path.

- [x] **Step 5: Render and validate controller health and boundaries**

  Run Kustomize rendering, Kubernetes schema checks, and:

  `pytest tests/kubernetes/test_cluster_resources.py -v`

  After authorized apply, verify EBS CSI registration/topology labels, metrics
  visibility, both ASG target groups healthy through NGINX, and no business pod
  on the control plane.

  On 2026-08-12 the deterministic Kustomize render tests, Kubernetes 1.35.5
  built-in schema validation, Terraform plan assertions, all
  infrastructure/Kubernetes tests, and the full repository check passed. At
  that point no live apply was authorized, so server-side admission and the
  live checks above were initially pending.

  Live acceptance on 2026-08-12 initially exposed that the kubeadm serving
  certificates contain no IP SANs. The approved minimal MVP compatibility
  correction added exactly one `--kubelet-insecure-tls` argument while keeping
  kubelet access cluster-restricted. After reapply, live acceptance passed for
  node metrics, EBS CSI, Argo CD, worker-only NGINX, and both ALB target groups.
  T32 retains the residual server-identity risk review.

- [x] **Step 6: Commit shared controllers**

  ```bash
  git add deploy/kubernetes/cluster infra/terraform/modules/node-iam tests/infra/test_platform_plan.py tests/kubernetes/test_cluster_resources.py
  git commit -m "feat(k8s): install ingress metrics and EBS CSI controllers"
  ```

**Verification:** Render and validate all controller resources, install them on
the authorized cluster, and run controller health/RBAC tests.

**Dependencies:** T18B.

**Requirements:** CR-07, CR-08, CR-09, CR-11, CR-15; spec sections 17.2 and
17.8.

**Complete when:** Shared controllers are healthy and ready for environment
desired state without possessing application secrets, and both ASG-maintained
ALB target groups have healthy ingress targets.

#### T19A — Define environment configuration, secrets, storage, and isolation

**Task status:** Complete; merged to `main`.

**Files**

- Create shared namespaces, ConfigMaps, service accounts, ExternalSecret
  contracts, static EBS CSI PV/PVC templates, and default-deny
  NetworkPolicies under `deploy/kubernetes/base/`.
- Create initial `deploy/kubernetes/overlays/dev/` and
  `deploy/kubernetes/overlays/prod/`.
- Create `scripts/config/sync_terraform_outputs.py` to place the six non-secret
  Terraform volume IDs and their Availability Zones into the matching reviewed
  overlay.
- Create `tests/kubernetes/test_environment_foundations.py`.

**Interfaces**

- Consumes: T17 `data_volumes` output, T18A environment labels/taints, and T18C
  EBS CSI driver/topology labels.
- Produces: PVC names `odoo-filestore`, `postgresql-data`, and
  `prometheus-data` in each namespace; every claim binds one exact retained
  Terraform volume and no StorageClass dynamically provisions storage.

**Work and tests**

- [x] **Step 1: Write failing storage and isolation render tests**

  For each environment, assert three static PVs with exact `volumeHandle`,
  `ReadWriteOnce`, `persistentVolumeReclaimPolicy: Retain`, matching
  `topology.kubernetes.io/zone`, and PVC `volumeName`. Assert Grafana has no PVC
  and no rendered StorageClass has a dynamic provisioner.

- [x] **Step 2: Run the focused render test and confirm it fails**

  Run: `pytest tests/kubernetes/test_environment_foundations.py -v`

  Expected: FAIL because the six static PV/PVC bindings do not exist.

- [x] **Step 3: Implement deterministic Terraform-output synchronization**

  `sync_terraform_outputs.py` accepts one JSON object with this exact shape:

  ```json
  {
    "dev": {"az": "us-east-1a", "odoo": "vol-dev-odoo", "postgresql": "vol-dev-pg", "prometheus": "vol-dev-prom"},
    "prod": {"az": "us-east-1b", "odoo": "vol-prod-odoo", "postgresql": "vol-prod-pg", "prometheus": "vol-prod-prom"}
  }
  ```

  It updates only the six `volumeHandle` fields and two zone patches, rejects
  missing/extra environments or workloads, and never reads secret outputs.

- [x] **Step 4: Bind all stateful data to retained EBS**

  Create static CSI PV/PVC pairs for Odoo filestore, PostgreSQL, and Prometheus
  in both environments. Use the ASG Availability Zone rather than a hostname,
  so a replacement worker in the same ASG can mount the volume. Keep Grafana
  on `emptyDir` and Loki retained objects in S3.

- [x] **Step 5: Add namespace configuration, secrets, policies, and budgets**

  Render distinct hosts/configuration/seed profile/secret references, hard
  environment placement, namespace-scoped External Secrets, and default-deny
  policies before documented allows. Budget the 30 GB root only for OS,
  Kubernetes, images, bounded Loki WAL/cache, and headroom; budget each initial
  state volume at 5 GiB.

- [x] **Step 6: Run rendering, schema, and mutation-scope checks**

  Run Kustomize/schema validation and:

  `pytest tests/kubernetes/test_environment_foundations.py -v`

  Expected: PASS with six exact retained bindings, no plaintext secrets, no
  cross-environment reference, no hostname-bound state, and no dynamic volume.

- [x] **Step 7: Commit the environment foundations**

  ```bash
  git add deploy/kubernetes/base deploy/kubernetes/overlays scripts/config/sync_terraform_outputs.py tests/kubernetes/test_environment_foundations.py
  git commit -m "feat(k8s): bind environment state to retained EBS"
  ```

**Verification:** Render both foundations, run schema/policy tests, assert no
plaintext secrets or cross-environment references, and inspect PV affinity.

**Dependencies:** T18C.

**Requirements:** CR-08, CR-09, CR-15; spec sections 15, 17.1, 17.5, and 17.7.

**Complete when:** Dev and prod have isolated, schedulable foundations but no
application workload has been deployed yet, and each stateful claim is bound
to the intended pre-provisioned EBS volume.

#### T19B — Define the complete non-observability application workloads

**Task status:** Complete locally; awaiting review and commit.

**Files**

- Create shared workloads under `deploy/kubernetes/base/` for frontend, API,
  MCP, Odoo, PostgreSQL, Odoo bootstrap Job, daily scan CronJob, Services,
  probes, resources, ingress routes, and documented NetworkPolicy allows.
- Complete application patches in both environment overlays.
- Create `tests/kubernetes/test_application_overlays.py`.

**Work and tests**

- [x] **Step 1:** Add one StockAI Odoo/PostgreSQL pair per environment and the
   T11A idempotent ORM bootstrap Job. The Deployment and Job use the same
   immutable StockAI Odoo digest. A protected Terraform apply temporarily
   attaches the exact-secret bootstrap policy, the Job updates only that ARN,
   a follow-up apply detaches the policy, and External Secrets materializes the
   resulting runtime key. Mount only `odoo-filestore` into Odoo and only
   `postgresql-data` into PostgreSQL; neither service writes durable data to
   worker root EBS.
- [x] **Step 2:** Add the daily `concurrencyPolicy: Forbid` CronJob with its private
   credential and source-restricted internal route.
- [x] **Step 3:** Add liveness/readiness/startup behavior, initial measured hypotheses for
   requests/limits, termination grace, rolling updates for stateless services,
   and single replicas for specified stateful services.
- [x] **Step 4:** Add CPU HPAs for frontend, FastAPI, and Procurement MCP, each with minimum
   one, maximum three, and a 50% average-utilization target. Add no node
   autoscaler or ASG scaling policy; insufficient capacity must produce
   visible pending pods until Terraform changes desired capacity.
- [x] **Step 5:** Expose only frontend/API and Odoo UI at this stage; Grafana is wired
   in T20A. Keep MCP, PostgreSQL, metrics, and internal dependencies private.
- [x] **Step 6:** Assert hard scheduling to any correctly labeled worker in the matching
   environment ASG for every business workload.

**Verification:** Render both overlays, run schema/policy tests, assert no
plaintext secrets, and calculate total requests against one `t3.medium`
worker.

**Dependencies:** T19A.

**Requirements:** CR-08, CR-09, CR-15; spec sections 12, 13, 14, and 17.

**Complete when:** Both overlays contain the complete non-observability
application stack with separate configuration, safe placement, and bounded pod
autoscaling on the environment workers.

#### T20A — Add environment-scoped metrics and S3-backed log collection

**Task status:** Complete locally; awaiting review and deployment-time smoke
verification.

**Files**

- Create observability resources under
  `deploy/kubernetes/base/observability/`.
- Create environment ConfigMaps under
  `deploy/kubernetes/overlays/dev/observability/` and
  `deploy/kubernetes/overlays/prod/observability/`.
- Create `tests/kubernetes/test_observability_collectors.py`.

**Work and tests**

- [x] **Step 1:** Deploy Prometheus, Grafana, Loki, Alertmanager, a lightweight blackbox
   exporter, and namespace-filtered Fluent Bit separately for dev and prod.
- [x] **Step 2:** Mount the environment’s statically bound EBS CSI claim into Prometheus,
   configure bounded retention to fit 5 GiB, and verify metric history survives
   Prometheus pod replacement.
- [x] **Step 3:** Provision Grafana data sources, folders, dashboards, and alerts from
   version-controlled ConfigMaps. Include Prometheus, Loki, and read-only
   CloudWatch metric data sources for ALB, ASG, and cleanup-Lambda metrics
   without embedding credentials. Use `emptyDir` for `/var/lib/grafana`; make
   manual UI edits unsupported and verify a replacement pod reconstructs the
   approved configuration.
- [x] **Step 4:** Configure Loki to write retained objects to only its environment’s S3
   prefix, with bounded WAL/cache and no sensitive audit data.
- [x] **Step 5:** Run any External Secrets controller that needs node-role credentials in a
   namespace-scoped, controller-class-limited mode on the matching environment
   worker; do not give the control-plane role application-secret access.
- [x] **Step 6:** Keep Prometheus/Loki retention and cardinality within the worker and volume
   budgets.
- [x] **Step 7:** Probe each public HTTPS hostname for status, latency, and certificate
   lifetime; keep probe labels bounded to environment and service.
- [x] **Step 8:** Expose Grafana through the approved ALB/ACM/NGINX HTTPS hostname while
   keeping Prometheus, Loki, and Alertmanager private.

**Verification:** Render resource totals, confirm environment isolation,
scrape one application metric, replace Prometheus and Grafana pods to verify
their different persistence contracts, and send a sanitized test log through
Fluent Bit → Loki → S3.

**Dependencies:** T19B.

**Requirements:** CR-08, CR-09, CR-12, CR-15, CR-16; spec section 21.

**Complete when:** Both full stacks have queryable metrics and S3-backed logs
without CloudWatch application logs.

#### T20B — Provision baseline dashboards and actionable internal alerts

**Task status:** Complete locally; awaiting review and deployment-time alert
verification.

**Files**

- Create dashboards under
  `deploy/kubernetes/base/observability/dashboards/`.
- Create alert rules under
  `deploy/kubernetes/base/observability/rules/`.
- Create `tests/kubernetes/test_observability_content.py` and
  `docs/runbooks/alerts.md`.

**Work and tests**

- [x] **Step 1:** Provision agent-health, LLM/MCP, Kubernetes/capacity, and dependency/edge
   dashboards with low-cardinality queries. The required panels include
   requests per minute split by success/error, request error rate,
   p50/p95/p99 latency, separate LLM input/output token counts, HPA replicas,
   pending pods, ASG desired/in-service capacity, correctly labeled Ready
   workers, replacement duration, volume attach errors, and clean/forced/failed
   cleanup outcomes.
- [x] **Step 2:** Provision initial pod failure, root/PV pressure, unhealthy ALB target or
   elevated ALB 5xx, ASG-versus-Ready-node mismatch beyond the replacement
   window, forced/failed cleanup, Lambda error/lifecycle timeout, public
   HTTPS/certificate-expiry, dependency failure, and Odoo-key-expiry alerts.
- [x] **Step 3:** Give every alert an owner-facing description, severity, evidence link, and
   concrete runbook action.
- [x] **Step 4:** Keep delivery internal to Grafana/Alertmanager for the MVP.

**Verification:** Validate every dashboard and rule automatically and load all
Grafana content. T34 reuses this evidence and live-fires only the three
representative application/dependency, capacity/infrastructure, and worker-
lifecycle alerts defined by the approved specification.

**Dependencies:** T20A.

**Requirements:** CR-12, CR-15; spec sections 21.4–21.6.

**Complete when:** The baseline platform is observable before the first cloud
walking-skeleton deployment and all supported Grafana content can be recreated
from Git without a Grafana data volume.

#### T21 — Implement deterministic CI checks and immutable release metadata

**Task status:** Complete locally; awaiting review and pull-request workflow
verification.

**Files**

- Create `.github/workflows/pr-checks.yml`,
  `.github/workflows/terraform-plan.yml`,
  `.github/workflows/terraform-apply.yml`,
  `scripts/release/create_manifest.py`,
  `scripts/release/verify_manifest.py`, and
  `tests/unit/release/test_manifest.py`.
- Create a schema under `deploy/releases/schema.json`.

**Work and tests**

- [x] **Step 1:** Test release metadata that binds source commit/tree, the
   complete named map of all four required project-image digests, build
   provenance, Scout result, dev validation status, and creation time. The
   schema must reject a missing frontend, API, MCP, or StockAI Odoo image.
- [x] **Step 2:** Add `actionlint` to `make lint` when the workflow files first
   exist, then run Python and React tests with JUnit/coverage summaries, builds,
   Compose validation, Terraform checks/plans, Kustomize/schema checks, secret
   scans, and action lint on every pull request.
- [x] **Step 3:** Run Docker Scout on pull requests targeting `main`, retain
   its report, and treat vulnerability findings as report-only while keeping
   release-integrity, test, secret-scan, and infrastructure validation failures
   blocking.
- [x] **Step 4:** Authenticate AWS plan jobs through read-only GitHub OIDC.
- [x] **Step 5:** Make path-filtered Terraform applies use protected GitHub environments and
   apply roles; never auto-apply an unreviewed plan.
- [x] **Step 6:** Retain reports as artifacts and make each failed stage clear in the job
   summary.

**Verification:** Exercise the workflows on a test pull request, including one
deliberate failing check, and unit-test manifest tampering.

**Local verification result:** Ten manifest tests and seven Makefile/workflow
contracts passed. `make check` passed formatting, Ruff, strict mypy over 141
source files, ESLint, architecture checks, actionlint 1.7.12, 268 Python unit
tests, and 17 React unit tests with JUnit and coverage output. All 12
real-transport integration tests and 5 deterministic Compose E2E tests passed,
and all four project images built. All 63 Terraform tests and 54 Kubernetes
tests passed; the five Terraform roots validated, all Compose files rendered,
and kubeconform reported 78 valid resources, 0 invalid resources, 0 errors, and
7 intentional CRD skips per environment. A live GitHub test pull request,
including one deliberate failing check, remains required before final T21
acceptance. No Docker Scout, AWS plan/apply, publication, or deployment ran.

**Dependencies:** T20B.

**Requirements:** CR-11, CR-13, CR-15; spec section 18.

**Complete when:** Pull requests cannot pass without complete, clearly reported
offline validation and release manifests reject changed artifacts.

#### T21A — Add guided provisioning for the approved AWS deployment

**Task status:** Approved by the user and course staff; implementation active.

**Files**

- Create `deploy/config/schema.json`, `deploy/config/deployment.json`,
  `scripts/infra/__init__.py`, `scripts/infra/discovery.py`,
  `scripts/infra/provision.py`, `tests/unit/infra/test_discovery.py`,
  `tests/unit/infra/test_provision.py`, and
  `docs/runbooks/infrastructure-provisioning.md`.
- Modify `.github/workflows/terraform-plan.yml`,
  `.github/workflows/terraform-apply.yml`, `Makefile`, `README.md`,
  `scripts/config/sync_terraform_outputs.py`, the five Terraform roots, and
  their existing infrastructure contract tests.
- Modify the T17 edge module and its tests only as required to remove the
  operator-email input and the two email-backed AWS Budget resources after a
  separately reviewed destructive plan confirms that no other edge resource
  changes.

**Interfaces**

- Consumes: a short-lived authenticated AWS CLI session, an authenticated
  GitHub CLI session with repository-administration permission, a user-owned
  lowercase Route 53 `domain_name`, its public `route53_zone_id`, the approved
  fixed `us-east-1` deployment region, and explicit confirmation of every
  saved Terraform plan. Docker Hub credentials remain external repository
  secrets and are never accepted or printed by the provisioning command.
- Produces: a reviewed non-secret `deploy/config/deployment.json`; ignored
  generated root input files; deterministic backend names/keys; automatic
  GitHub `dev`/`prod` environment creation; and these five automatically
  managed repository variables:
  `AWS_TERRAFORM_PLAN_ROLE_ARN`, `AWS_TERRAFORM_APPLY_ROLE_ARN`,
  `TERRAFORM_STATE_BUCKET`, `TERRAFORM_STATE_KEY_PREFIX`, and
  `TERRAFORM_LOCK_TABLE`.
- Removes from the workflow interface only after replacement tests pass:
  `TERRAFORM_PLATFORM_TFVARS_JSON`, `TERRAFORM_EDGE_TFVARS_JSON`,
  `TERRAFORM_DEV_TFVARS_JSON`, and `TERRAFORM_PROD_TFVARS_JSON`.
- Preserves: the existing self-managed kubeadm architecture, five separate
  Terraform roots/states, `us-east-1`, exact Bedrock model, explicit plan
  review, GitHub OIDC, Argo CD workload reconciliation, and every current
  StockAI resource/state identity. Fresh clones receive deterministic names;
  this task must not rename or recreate the existing deployment.

**Work and tests**

- [x] **Step 1: Freeze the two-input deployment contract with failing tests.**
  Define `deploy/config/schema.json` so the only typed operator values are
  `domain_name` and `route53_zone_id`. Test lowercase public-domain and Route
  53-zone validation, rejection of extra keys, atomic writes, secret-like
  input rejection, and a generated confirmed `administrator_cidr` field that
  cannot silently change on a rerun. Record the approved `us-east-1` region
  and existing-deployment compatibility identity as generated metadata rather
  than additional prompts.
- [x] **Step 2: Add bounded discovery with confirmation and no mutation.**
  Mock and test exact JSON parsing for `aws sts get-caller-identity`, immutable
  repository owner/name/IDs through `gh api`, the caller's public IPv4 `/32`,
  the controlled Canonical Ubuntu AMI source used by the current cluster, and
  two distinct available `us-east-1` Availability Zones. The command must show
  the detected CIDR and require confirmation or an explicit override; fail
  before Terraform when credentials, tools, quota, Route 53 authority, the
  approved AMI, two usable AZs, or exact Bedrock access cannot be verified.
- [x] **Step 3: Generate stable names and root inputs without four GitHub JSON
  blobs.** Test deterministic AWS-length-safe deployment, state-bucket,
  lock-table, state-key, cluster, Loki-bucket, and IAM names from immutable
  account/repository identity. Generate ignored `*.auto.tfvars.json` files
  from the deployment descriptor and discovery results. Read platform outputs
  for edge inputs and platform/edge outputs for dev/prod inputs through exact
  typed output adapters; never ask the operator to copy account IDs, AMIs,
  AZs, subnet/VPC/security-group IDs, ASG/role names, bucket ARNs, or volume
  coordinates. Existing state must pin the current names and cause a hard
  failure if generated identity would replace them.
- [x] **Step 4: Implement one resumable guided Terraform command.** Add
  `make infra-provision`, backed by `scripts/infra/provision.py`, to run
  preflight and then `bootstrap`, `platform`, `edge`, `environments/dev`, and
  `environments/prod` in dependency order. For each root, create a saved plan,
  display its summary, require an explicit typed approval, apply only that
  exact saved plan, record a non-secret completion checkpoint, and safely
  resume after interruption. Bootstrap remains local because an untrusted
  repository cannot grant itself initial AWS authority. A push or merge must
  never invoke this command or mutate Terraform infrastructure.
- [x] **Step 5: Configure GitHub automatically after bootstrap.** Mock `gh api`
  and `gh variable set` calls, then create/update `dev` and `prod` environments
  and the five generated repository variables from verified Terraform
  outputs. Never accept Docker Hub credentials, never write repository
  secrets, and never print token or Terraform-state content. Update the plan
  and apply workflows to generate root inputs from the committed descriptor,
  AWS caller identity, controlled discovery, and reviewed remote-state
  outputs; delete their references to the four `TERRAFORM_*_TFVARS_JSON`
  variables only in the same tested change.
- [x] **Step 6: Synchronize deployment outputs into Git desired state.** Feed
  the exact non-secret environment outputs into
  `sync_terraform_outputs.py`, including six EBS volume IDs/AZs, Cognito
  coordinates, Loki bucket coordinates, hostnames, and the exact Odoo secret
  ARN. Test that only approved Kustomize fields change, generated output is
  deterministic, no secret value enters Git, and a second run is a no-op.
- [x] **Step 7: Remove email-backed budgets behind explicit review.** The code,
  offline contract, and current-account saved edge plan completed. The approved
  plan contained exactly `0 add, 0 change, 2 destroy`; only the two Budget
  resources were destroyed. Its SHA-256 was
  `7e546cf0d47ef7961b0d0be6dad472ba62faa81bd3eb664e25688268e268d7b7`
  and a post-apply refresh plan reported `No changes`. First add
  Terraform plan tests proving that `budget_notification_email` and the two
  `aws_budgets_budget.monthly` resources are absent while existing cost,
  shutdown, quota, and pricing-review documentation remains. For the current
  deployment, require a saved plan showing only those two budget deletions
  before approval; any ALB, DNS, certificate, S3, ASG, IAM, DynamoDB, Cognito,
  Secrets Manager, EBS, or state change is a stop condition.
- [x] **Step 8: Document and exercise the fresh-clone and existing-deployment
  paths.** Test dry-run, rejected approval, interrupted/resumed run, unchanged
  rerun, malformed AWS/GitHub output, command timeout, and redacted failure.
  Document the exact normal experience: clone, authenticate AWS/GitHub, enter
  domain and hosted-zone ID, confirm detected CIDR, review saved plans, add
  only `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`, and let later T22/T24 GitOps
  workflows publish/promote workloads.

**Verification:** Run focused discovery/orchestration unit tests with fake AWS,
GitHub, public-IP, and Terraform processes; all existing Terraform plan tests;
workflow contract tests plus actionlint/ShellCheck; Terraform formatting and
provider-schema validation for all five roots; Kubernetes render/schema tests;
`make check`; and `git diff --check`. On the existing account, run a no-change
guided plan through every root and prove that the generated GitHub variables
match bootstrap outputs, the four JSON variables are unused, a second output
synchronization is empty, and no resource rename/replacement appears. Do not
claim a second fresh-account deployment unless one is actually performed.

**Dependencies:** T21 plus approved amendments to spec sections 16.1–16.3,
17.8, 18.3, 23, and the cost/budget decisions affected by removing email-backed
AWS Budgets.

**Requirements:** CR-07, CR-08, CR-10, CR-11, CR-13, CR-15, CR-16; spec
sections 16–18, 20, 22.5, and 23 after amendment.

**Complete when:** A fresh-clone operator supplies only the owned Route 53
domain and public hosted-zone ID, confirms a detected administration CIDR,
reviews the saved plans, and adds the two Docker Hub secrets; one resumable
command discovers/generates all other non-secret configuration, applies the
five roots in order without an unreviewed plan, configures GitHub automatically,
and synchronizes Kubernetes desired state. The existing StockAI deployment
plans with no resource identity replacements, and no workflow references the
four removed TFVARS JSON variables.

#### T21B — Add protected GitHub-managed infrastructure lifecycle

**Task status:** Proposed amendment reflecting the user- and
course-staff-approved scope; implementation remains blocked until this exact
plan text is reviewed and the user explicitly authorizes T21B implementation.

**Files**

- Create `.github/workflows/terraform-provision.yml` and
  `.github/workflows/terraform-destroy.yml` for separately protected, manual
  lifecycle operations.
- Create `scripts/infra/cluster_platform.py` and
  `tests/unit/infra/test_cluster_platform.py` for bounded SSM command
  construction, polling, redaction, and readiness verification.
- Modify `scripts/infra/provision.py` only to expose the existing discovery,
  generated-input, backend, and typed-output operations needed by a
  non-interactive GitHub runner; preserve the interactive local
  `make infra-provision` path.
- Modify `infra/terraform/bootstrap/main.tf`, its variables/outputs only when
  necessary, and `tests/infra/test_terraform_bootstrap.py` to add reviewed,
  resource-scoped lifecycle permissions to the existing GitHub apply role.
- Modify `tests/config/test_ci_workflows.py`,
  `docs/runbooks/infrastructure-provisioning.md`, `README.md`, and
  `docs/implementation-status.md` for lifecycle contracts and operator use.

**Interfaces**

- Consumes: the T21A deployment descriptor and generated root inputs; the
  existing bootstrap state bucket, lock table, GitHub OIDC provider, plan role,
  and apply role; a protected GitHub environment approval; and an exact manual
  confirmation identifying the requested operation and deployment.
- Produces: a manual provision path that applies `platform`, `edge`, `dev`, and
  `prod` in dependency order, then establishes and verifies the shared
  Kubernetes platform; and a separately protected destruction path that
  quiesces workloads and destroys `prod`, `dev`, `edge`, and `platform` in
  reverse dependency order.
- Preserves: the complete `bootstrap` root and state, GitHub OIDC foundation,
  Terraform state bucket and locking, the local T21A recovery path, and Argo
  CD as the only application-workload deployment authority.
- Excludes: automatic apply or destroy on pushes, merges, schedules, or pull
  requests; permanent AWS access keys; an SSH private-key repository secret;
  direct GitHub-runner `kubectl`; image build/promotion; and application
  workload installation.

**Work and tests**

- [ ] **Step 1: Freeze the manual lifecycle workflow contracts with failing
  tests.** Assert that both workflows use only `workflow_dispatch`, declare
  `id-token: write` with least other token permissions, use independent
  concurrency groups, select their distinct protected GitHub environments,
  reject missing or incorrect exact confirmations, and contain no push, pull
  request, schedule, static AWS key, SSH key, runner-side `kubectl`, or
  bootstrap apply/destroy path. Assert the provision order
  `platform -> edge -> dev -> prod` and destruction order
  `prod -> dev -> edge -> platform`, with failure stopping all later roots.
- [ ] **Step 2: Add reviewed least-privilege lifecycle permissions.** Extend
  the bootstrap-managed GitHub apply role with only the actions Terraform and
  the cluster-platform SSM step require for the existing StockAI network,
  compute, ASG, lifecycle, EBS, ALB/ACM/Route 53, S3, DynamoDB, Secrets
  Manager, Cognito, EventBridge, Lambda, CloudWatch, IAM, and SSM resources.
  Restrict resource-capable actions to deterministic StockAI names, paths,
  ARNs, and required ownership tags; isolate unavoidable AWS list/describe
  actions in reviewed read-only statements. Test that the plan role remains
  read-only, `iam:PassRole` names only approved StockAI roles, SSM commands
  target only the tagged control-plane instance, and neither role can mutate
  the bootstrap bucket, lock table, OIDC provider, or bootstrap roles.
- [ ] **Step 3: Implement saved-plan sequential provisioning.** Authenticate
  with the apply role through OIDC, verify the T21A descriptor against the AWS
  account and repository identity, generate ignored inputs, and for each of
  `platform`, `edge`, `dev`, and `prod` run remote-backend initialization,
  create a saved plan, publish its human-readable summary and checksum, and
  apply that exact saved plan only after the workflow's protected-environment
  approval. Pass typed Terraform outputs to dependent roots and synchronize
  the approved non-secret outputs into Kubernetes desired state. A rerun must
  be idempotent and a failed root must prevent all dependent roots and cluster
  installation.
- [ ] **Step 4: Complete the shared Kubernetes platform through SSM.** Wait
  with bounded polling for EC2 cloud-init, `/etc/kubernetes/admin.conf`, the
  StockAI control-plane completion marker, and the expected Ready control
  plane and dev/prod workers. Calico remains owned by the existing
  control-plane user data and must not be independently reinstalled. Send a
  pinned, non-secret SSM command to the tagged control plane that obtains the
  exact approved repository revision, renders
  `deploy/kubernetes/cluster`, applies the shared NGINX Ingress, EBS CSI,
  metrics-server, kube-state-metrics, and Argo CD resources, and waits for
  their readiness. Poll the command to a bounded terminal state, redact
  failures, remove temporary files, and publish only non-secret health
  evidence. Do not install dev/prod Argo CD `Application` resources or any
  StockAI application workload; those remain T22/T24 responsibilities.
- [ ] **Step 5: Implement separately protected destruction.** Require a
  destruction-specific environment and exact deployment/account confirmation.
  Through bounded SSM commands, suspend/remove Argo-managed environment
  applications and namespaces when present and wait for workload volume
  detachment. Generate and publish destruction plans before applying them in
  exact `prod`, `dev`, `edge`, `platform` order. Stop on an unexpected target
  or failed root, retain auditable summaries, and prove that the workflow has
  no code path to the bootstrap root, its state object, bucket, lock table,
  OIDC provider, or GitHub roles.
- [ ] **Step 6: Verify lifecycle safety and document recovery.** Run focused
  workflow, orchestration, IAM, Terraform, Kustomize, ShellCheck/actionlint,
  and complete repository checks. Exercise mocked no-change, rejected
  confirmation, rejected environment, interrupted root, SSM timeout, unhealthy
  controller, partial destruction, and safe rerun paths. Document protected
  environment setup, approval evidence, expected summaries, retry/recovery,
  the preserved-bootstrap boundary, and when to use the local
  `make infra-provision` fallback. A live destruction/recreation drill requires
  a separately reviewed plan and explicit user authorization; tests alone do
  not authorize it.

**Verification:** Run
`pytest tests/unit/infra/test_cluster_platform.py tests/infra/test_terraform_bootstrap.py tests/config/test_ci_workflows.py -v`,
all infrastructure and Kubernetes tests, actionlint, ShellCheck, Terraform
formatting/provider-schema validation, `make check`, and `git diff --check`.
For live acceptance, manually approve the provision workflow, verify all four
saved plans and checksums, confirm three Ready nodes and healthy shared
controllers, and prove a second run plans no changes. Exercise destruction
only after its independently reviewed destructive plan is explicitly approved.

**Dependencies:** T21A and the already implemented T18A–T18C cluster bootstrap
and shared-controller resources.

**Requirements:** CR-07, CR-08, CR-10, CR-11, CR-15, CR-16; spec sections
16–18, 20, 22.5, and 23.

**Complete when:** An authorized operator can use protected manual GitHub
workflows to provision the four non-bootstrap Terraform roots and establish a
healthy shared Kubernetes platform without static AWS or SSH credentials, or
to run the separately protected reverse-order destruction path, while normal
branch activity never mutates infrastructure, bootstrap remains preserved,
and Argo CD retains application deployment responsibility.

#### Remaining-work simplification and task mapping

This amendment changes only future work. T01–T21B history, implemented
architecture, existing tests, and safe Odoo atomic methods remain intact.

| Previous task | Revised task | Disposition |
|---|---|---|
| T22 | T22 | Build every project image and create one immutable dev release. |
| T23 | T23 | Bind real dev validation to the exact T22 release; retain one representative worker-replacement drill. |
| T24 | T24 | Prepare and promote the exact dev-tested digests without rebuild or direct deployment. |
| T25 + T26 + T27 | T25 | Merge forecast, duplicate/open-PO coverage, offers, quantity, performance, and budget into one deterministic-evidence slice. |
| T27A + T27B + T27C | T26 | Merge Odoo preference configuration, MCP resolution, enforcement, case snapshot, prompt binding, audit, and read-only UI. |
| New presentation task | T26A | Modernize the existing read-only scan detail using a decision-first summary and expandable evidence, without changing behavior or API contracts. |
| T28 | T27 | Keep contextual AI recommendation and safe fallback as a separate reasoning boundary. |
| T29 | T28 | Keep idempotent draft creation, persistence, evidence/revision binding, and manager pause separate. |
| T30 + reject/cancel/reconcile portions of T31 | T29 | Merge approval/confirmation and rejection/cancellation into one manager-decision lifecycle. |
| T31 request-change/update/reapproval behavior | Removed from MVP | Leave the implemented Odoo update primitive in place but do not expose or orchestrate it. |
| T32 | T32 | Verify existing security boundaries and close actual gaps with representative live rotation. |
| T33 | T33 | Run representative interruption, capacity, snapshot recovery, shutdown/restart, and cost scenarios. |
| T34 | T34 | Treat as the release-candidate acceptance and evidence gate. |
| T35 | T35 | Consolidate and rehearse the 15-minute demo and presentation. |

Type A simplifications remove duplicate work or task fragmentation without
changing product behavior. Type B simplifications change the approved
preference and manager-decision behavior; they are already reflected in the
2026-08-14 approved specification and require this plan's renewed approval.

#### T22 — Build and reconcile one complete four-image dev release

**Files**

- Create `.github/workflows/dev-images.yml`,
  `deploy/kubernetes/argocd/dev-application.yaml`,
  `scripts/release/content_identity.py`,
  `scripts/release/update_dev_overlay.py`,
  `tests/unit/release/test_content_identity.py`, and
  `tests/unit/release/test_update_dev_overlay.py`.
- Modify `deploy/releases/schema.json`,
  `scripts/release/create_manifest.py`,
  `scripts/release/verify_manifest.py`, `.github/workflows/pr-checks.yml`,
  `tests/unit/release/test_manifest.py`, `tests/config/test_ci_workflows.py`,
  and `tests/config/test_makefile_contract.py`.

**Interfaces**

- Consumes: a relevant non-bot push to `dev`, the four existing Docker build
  definitions, and the two Docker Hub secrets.
- Produces: one stable release ID, one deterministic application-content
  digest, four immutable `@sha256` image references, per-image provenance,
  immutable Scout/creation/source metadata, pending validation state, and the
  dev overlay desired-state commit.
- Excludes: changed-image detection, prior-digest carry-forward, mixed old/new
  releases, direct `kubectl`, and any application deployment outside Argo CD.

**Work and tests**

- [ ] **Step 1: Freeze the release schema before changing workflows.** Add
  failing schema/unit cases for the immutable release core, exact four-image
  map, stable release ID, single content digest, append-only validation-attempt
  collection, and canonical integrity digest. Reject mutable tags, extra or
  missing images, duplicate keys, tampered core fields, and passed validation
  without bound evidence.
- [ ] **Step 2: Implement one content identity.** Hash a canonical manifest of
  the declared release-relevant inputs for all four builds. Include Dockerfiles,
  lockfiles, source, frontend, Odoo add-on/bootstrap, and build configuration;
  exclude generated release records and overlay digest edits. Test order and
  timestamp independence, one relevant-file change, irrelevant generated-file
  changes, and feature/dev merge-commit equivalence.
- [ ] **Step 3: Build and publish all four images.** On every guarded relevant
  `dev` push, use BuildKit caching, build frontend/API/MCP/Odoo, push all four,
  resolve registry digests, collect provenance, and run Docker Scout as
  report-only. Build, push, digest, provenance, schema, and Kustomize failures
  remain blocking; Scout findings or scanner/report-upload errors do not.
- [ ] **Step 4: Create the immutable candidate.** Create the manifest only
  after all four digests, provenance, Scout outcome, source traceability, and
  creation metadata are known. Once created, no T23 operation may rewrite
  those fields.
- [ ] **Step 5: Update dev desired state without recursion.** Atomically update
  the four approved dev Kustomize image fields and release record, validate
  both overlays and the schema, then commit with actor/path/message guards so
  the bot-only desired-state commit cannot trigger a second build.
- [ ] **Step 6: Configure Argo CD and observe convergence.** Track the `dev`
  revision and dev overlay, query bounded authenticated Argo API sync/health
  status, and publish it as deployment evidence. Do not treat Argo health as
  T23 smoke evidence and do not run `kubectl` from Actions.
- [ ] **Step 7: Run focused and regression checks.** Run the release and
  workflow unit tests, `make build`, `make kubernetes-validate`,
  `make verify-release`, and the existing PR workflow assertions. Exercise a
  normal build, Scout findings, Scout tool error, bot-loop guard, manifest
  tampering, missing digest, and Argo observation timeout.

**Dependencies:** T21B and explicit T22 implementation authorization.

**Requirements:** CR-08, CR-11, CR-13, CR-15; spec sections 18 and 22.5.

**Complete when:** One relevant dev push publishes a coherent four-image
release, records one deterministic content identity and immutable release core,
updates only Git desired state, and Argo CD reconciles dev without any Actions
`kubectl` deployment.

#### T23 — Validate the exact dev release and prove worker replacement once

**Files**

- Create `scripts/release/record_validation.py`,
  `tests/unit/release/test_record_validation.py`,
  `tests/smoke/test_dev_skeleton.py`, `scripts/smoke/dev.sh`, and
  `docs/runbooks/dev-validation.md`.
- Modify `.github/workflows/dev-images.yml`, `deploy/releases/schema.json`,
  release verification code, dev dashboard links, and
  `docs/implementation-status.md`.

**Interfaces**

- Consumes: the Argo-reconciled T22 release and its stable release ID.
- Produces: append-only validation attempts bound to the stable release ID,
  exact four-digest map, Argo revision, smoke-run identity, timestamp, result,
  and evidence digest.
- Preserves: immutable T22 identity, content digest, images, provenance, Scout,
  source, and creation metadata.

**Work and tests**

- [ ] **Step 1: Lock validation transitions with failing tests.** Cover
  pending-to-passed, pending-to-failed, explicit failed-then-passed retry,
  wrong release ID, changed image map, changed Argo revision, missing smoke
  evidence, attempt rewriting, passed-evidence replacement, and downgrade from
  passed. A boolean alone must never make a release promotable.
- [ ] **Step 2: Implement atomic append-only evidence recording.** Validate
  the existing manifest, append one bounded attempt for the exact release,
  recompute document integrity without changing the release core, and stage
  validation before replacement. Once passed, reject silent evidence changes.
- [ ] **Step 3: Run the real dev walking-skeleton smoke.** Exercise public
  HTTPS, Cognito, FastAPI, compiled LangGraph, Bedrock GPT-OSS, real Streamable
  HTTP MCP, real Odoo read, DynamoDB persistence, frontend polling, metrics,
  sanitized logs, and Loki/S3 evidence. Bind the same correlation and release
  identifiers throughout.
- [ ] **Step 4: Perform one representative worker-replacement drill.** Record
  fictional Odoo/PostgreSQL data and a Prometheus sample, terminate the dev
  worker through its ASG, and verify lifecycle cleanup, old-Node removal,
  replacement join and environment identity, all three retained EBS
  reattachments, workload and ALB recovery, retained data, and Grafana
  reconstruction from Git.
- [ ] **Step 5: Publish reusable evidence.** Record smoke and worker-recovery
  evidence once in the release/runbook/status artifacts. Future feature release
  validation reruns the smoke path but does not repeat the worker drill unless
  worker/storage behavior changed or prior evidence is invalid.

**Verification:** Run `make smoke-dev`, focused validation tests, release
verification, and the controlled ASG replacement checklist. Inspect matching
release/correlation IDs in UI, API/MCP logs, metrics, DynamoDB, Odoo, Argo, and
the validation evidence.

**Dependencies:** T22 and authorized AWS/deployment access.

**Requirements:** CR-02 through CR-13, CR-15, and CR-16 as applicable to the
walking skeleton.

**Complete when:** The exact four-image release has immutable successful dev
evidence and the existing replacement-safe retained-storage design has one
complete reusable live proof.

#### T24 — Prepare and promote the exact dev-tested release to prod

**Files**

- Create `scripts/release/promote_dev.py`,
  `tests/unit/release/test_promote_dev.py`,
  `.github/workflows/main-promote.yml`,
  `deploy/kubernetes/argocd/prod-application.yaml`,
  `tests/smoke/test_prod_skeleton.py`, and
  `docs/runbooks/prod-promotion.md`.
- Modify `Makefile`, `.github/workflows/pr-checks.yml`, release verification,
  both overlays' release representation, and workflow regression tests.

**Interfaces**

- Consumes: a clean feature branch and the passed immutable release read from
  `origin/dev` without modifying that branch.
- Produces: local unstaged prod overlay/release changes containing the exact
  four dev-tested digests and provenance; after reviewed main merge, prod Argo
  reconciliation and smoke evidence.
- Excludes: commit, push, merge, branch modification, image rebuild/retag,
  mutable image selection, AWS/Kubernetes access from `make promote-dev`,
  workflow commits, and direct `kubectl` deployment.

**Work and tests**

- [ ] **Step 1: Write promotion safety tests.** Cover dirty worktree,
  `dev`/`main`, missing `origin/dev`, malformed/tampered manifest, mutable or
  missing image, missing/pending/failed validation, rewritten passed evidence,
  application-content mismatch, success, unchanged second run, and any failed
  validation leaving all target files unchanged.
- [ ] **Step 2: Implement read-only candidate selection.** Fetch or read the
  exact release object from `origin/dev` without checkout or mutation. Validate
  core integrity, append-only passed evidence, and the exact immutable image
  map.
- [ ] **Step 3: Compare application content rather than commit equality.** Use
  the same T22 input declaration and digest algorithm against the feature
  branch. Retain commit SHAs for traceability but allow legitimate merge and
  GitOps bot commit differences.
- [ ] **Step 4: Prepare outputs transactionally.** Render the prod release and
  overlay in a temporary workspace, copy the exact four digests/provenance,
  run schema and both Kustomize validations there, then replace local targets
  only after every check succeeds. Leave files unstaged for human review.
- [ ] **Step 5: Verify protected-main promotion.** The main PR runs the full
  suite, report-only Scout, release/schema/Kustomize checks, and exact
  dev-evidence comparison. After merge, the main workflow verifies without
  rebuilding or committing, observes prod Argo through its API, and runs the
  public prod smoke.
- [ ] **Step 6: Verify rollback semantics.** Document and test Git revert to a
  previously verified prod release; never rebuild an old version or select a
  mutable tag.

**Verification:** Run focused promotion tests, `make promote-dev`,
`make verify-release`, `make kubernetes-validate`, workflow assertions, prod
Argo observation, and `make smoke-prod`. Prove the dev and prod four-digest maps
are byte-for-byte identical.

**Dependencies:** T23.

**Requirements:** CR-08, CR-11, CR-12, CR-13, CR-15; spec section 18.

**Complete when:** Protected-main merge promotes exactly the dev-validated
release through Git and Argo CD, with no rebuild, retag, workflow commit,
`kubectl`, or direct deployment.

### Phase 5 — Remaining procurement vertical slices

Each task below delivers one demonstrable user-facing vertical slice and
updates domain, Odoo/MCP, graph, API, React, persistence, audit, tests,
documentation, metrics, logs, alerts, and dashboards only where relevant. The
system remains runnable after each task, and the approved T22–T24 path promotes
the complete four-image release.

#### T25 — Complete deterministic procurement evidence

**Files**

- Create focused policy modules under `src/procurement/domain/policy/` for
  forecast, duplicate coverage, offers, quantity, performance, and budget.
- Create or extend MCP evidence tools under
  `src/procurement/mcp_server/tools/`, Odoo port/adapter mappers, graph evidence
  nodes, `src/procurement/api/routes/cases.py`, and React case/evidence
  components.
- Add corresponding domain, adapter, MCP-tool, real-transport, API, React,
  observability, seed, and dev-smoke tests.

**Interfaces**

- Produces one authoritative `ProcurementEvidence` boundary containing the
  shortage timeline, open-PO coverage, eligible/rejected offers, per-offer
  quantity and normalized cost, performance evidence, and budget result.
- Later LLM code may compare this evidence but may not calculate, replace, or
  override any authoritative value.

**Work and tests**

- [ ] **Step 1: Define the complete typed evidence contract.** Add failing
  tests for exact decimals, currencies, dates, confidence/evidence counts,
  deterministic reason codes, environment binding, and serialization limits.
- [ ] **Step 2: Implement shortage and coverage policy.** Project 14 days from
  known movements, distinguish reorder trigger from need-by/stockout, and
  account for pending cases plus draft/confirmed incoming POs. Cover full,
  partial, and residual coverage, pagination, the 50-candidate limit, and at
  most three concurrent product workflows.
- [ ] **Step 3: Implement offer and quantity policy.** Enforce vendor approval
  and blocks, offer validity, price/currency, delivery by need-by, reorder
  maximum, arrival projection, MOQ, packaging/UoM rounding, normalized order
  cost, projected/excess inventory, and deterministic rejection reasons.
- [ ] **Step 4: Implement performance evidence.** Calculate the 365-day
  completed-order count, on-time rate, average positive lateness, receipt and
  return proxy, evidence counts, and insufficient-history status below three
  orders.
- [ ] **Step 5: Implement authoritative budget evidence.** Resolve the category,
  analytic account, and calendar-month budget; calculate confirmed commitment,
  remaining before/after, and exact overage. Over-budget remains eligible and
  requires explicit manager exception unless a separately specified
  deterministic hard policy excludes it.
- [ ] **Step 6: Integrate the vertical slice.** Expose the evidence through
  real MCP transport, graph state, case API, React detail/skip views, immutable
  audit, bounded metrics, and sanitized logs.
- [ ] **Step 7: Run focused and end-to-end checks.** Test timezone/month edges,
  missing/malformed Odoo data, duplicate conditional writes, concurrency,
  precision and rounding, prompt-injection-like vendor text, budget mismatch,
  real seeded Odoo evidence, and deterministic rejected/skip reasons.

**Verification:** Run focused unit/UI tests, `make test-integration`, the real
Odoo contract/adapter cases, `make smoke-dev`, and T22–T24 release validation.

**Dependencies:** T24.

**Requirements:** CR-02, CR-03, CR-04, CR-06, CR-12, CR-13, CR-15; spec
sections 7.1 and 8.1–8.6.

**Complete when:** Every candidate has one coherent authoritative evidence
record before LLM reasoning, every exclusion has a deterministic reason, and
duplicate or fully covered shortages cannot proceed.

#### T26 — Apply typed revisioned preferences end to end

**Files**

- Extend `odoo/addons/stockai_procurement/` with one current typed preference
  record per scope, ordered priorities, monotonic revision handling, Odoo
  tracking, constraints, access controls, menus, and forms.
- Create `src/procurement/domain/policy/preferences.py`,
  `src/procurement/adapters/odoo/preference_mapper.py`, and
  `src/procurement/mcp_server/tools/preferences.py`.
- Extend MCP schemas/registry, Odoo port, graph state/nodes, prompt renderer,
  case/evidence/audit schemas, DynamoDB mapping, API output, and
  `frontend/src/components/AppliedPreferences.tsx`.
- Add Odoo add-on, domain, mapper, MCP, real-transport, graph, prompt-boundary,
  persistence, API, React, observability, and dev-smoke tests.

**Interfaces**

- Consumes: company, category, product, and otherwise-eligible T25 offers.
- Produces: one validated effective preference snapshot containing profile ID,
  scope, server-managed revision, ordered supported criteria, bounded premium,
  enforcement mode, precedence source, and premium result.
- Excludes: effective dates, overlap rules, activation/version-history models,
  inheritance-preview UI, separate history administration, raw prompt editing,
  and manager mutation through React.

**Work and tests**

- [ ] **Step 1: Define the simple Odoo model and authorization tests.** Require
  one company record and at most one category/product record per scope. Only
  the configuration administrator may create/update/archive; server-side
  writes increment revision monotonically and Odoo tracks changes. Officers,
  managers, and the integration user cannot administer preferences.
- [ ] **Step 2: Implement and seed typed configuration.** Support the existing
  criterion enum, 0–100% max premium, and `advisory|hard`. Seed reliability-
  first company, delivery-first category, and price-first product records with
  reproducible revisions and no prompt editor.
- [ ] **Step 3: Resolve through MCP.** Resolve product → category → company,
  return only typed fields and precedence metadata, and independently validate
  scope, record identity, positive revision, unique criteria, percentage, and
  enforcement. Missing/malformed/unauthorized configuration returns a safe
  manual-review error and no default guess.
- [ ] **Step 4: Enforce premium deterministically.** Compare each otherwise-
  eligible normalized total with the cheapest eligible baseline. Record an
  advisory exceedance or remove above-cap offers in hard mode before LLM
  reasoning; reject non-positive escaped costs.
- [ ] **Step 5: Bind the immutable case snapshot.** Copy the exact resolved
  values and premium result into the case and evidence hash once. Later Odoo
  edits affect later scans only and cannot rewrite an in-flight snapshot.
- [ ] **Step 6: Render safe model context and read-only UI.** Pass only typed
  enums, numbers, identifiers, scope, and revision to the application-owned
  renderer. Show the applied scope/revision/criteria/premium/result/mode in
  React and audit without exposing editable controls.
- [ ] **Step 7: Verify the whole slice.** Cover precedence, monotonic revision,
  concurrent update, role denial, boundaries, advisory/hard behavior,
  malformed Odoo output, raw audit/business-text injection, snapshot stability,
  metric cardinality, container/release inclusion, and all three real seeded
  scenarios through Odoo, MCP, graph, API, and UI.

**Verification:** Run focused Odoo/Python/React tests, the Odoo contract,
`make test-integration`, `make smoke-dev`, and the complete four-image release
checks.

**Dependencies:** T25.

**Requirements:** CR-02, CR-03, CR-04, CR-05, CR-06, CR-08, CR-11, CR-12,
CR-13, CR-15; spec sections 6, 8.7, 9, 11–15, 20, and 22.

**Complete when:** An authorized administrator can maintain simple typed
preferences in Odoo, the exact effective revision is enforced and snapshotted
through MCP, and officers/managers see it read-only without any prompt or
eligibility bypass.

#### T26A — Modernize the read-only scan detail

**Files**

- Modify `frontend/src/pages/ScanPage.tsx`,
  `frontend/src/pages/OverviewPage.tsx`, `frontend/src/App.tsx`,
  `frontend/src/components/ProcurementEvidence.tsx`,
  `frontend/src/components/AppliedPreferences.tsx`, and
  `frontend/src/styles.css`.
- Create small dependency-free presentation components for the working
  navigation rail, reusable status icons, and accessible inventory chart under
  `frontend/src/components/`.
- Extend `frontend/tests/scan.test.tsx`; add a small presentation helper and
  focused tests for `App` navigation and `OverviewPage`. Keep shared date,
  quantity, percentage, currency, and relative-time formatting in
  `frontend/src/presentation.ts`.

**Approved design**

- Use the decision-first Executive Summary direction as the foundation.
- Add four icon-backed summary cards for coverage, shortage, offer, and
  recommendation. A green risk check appears only when the response contains
  no risk flags; real flags use warning treatment and remain visible.
- Borrow progressive disclosure from the Evidence Workspace direction. Show an
  accessible SVG inventory projection with a reorder-threshold reference line
  by default and retain exact daily values in an expandable table.
- Show eligible offers first. Label an offer `Only eligible offer` only when
  exactly one exists; never claim a best vendor without an authoritative
  selected-offer field. Put rejected offers in a separate disclosure.
- Present applied preferences as ordered priority chips, scope/revision and
  enforcement badges, premium policy, and compact per-offer outcomes.
- Add a desktop navigation rail with only working Home and Scans destinations,
  compact navigation on narrow screens, and a clearer home hero, truthful scan
  status counts, recent-scan cards, timestamps, loading skeletons, and empty
  states derived from current API data.
- Defer the Guided Timeline until a later explainability slice has real
  workflow events; never invent reasoning steps or expose hidden
  chain-of-thought.

**Interfaces**

- Consumes: the existing `Scan`, `ApprovalReadyResult`,
  `ProcurementEvidence`, `OfferEvidence`, and `AppliedPreferences` frontend
  types without changing their API shape.
- Produces: a responsive application shell and decision-first summary with
  truthful status, product, coverage, shortage, offer, recommendation, budget,
  and risk information visible first; a visual projection and complete exact
  evidence remain accessible without leaving the page.
- Excludes: backend/API changes, new workflow actions, approval controls,
  fabricated winners or values, inactive navigation, third-party icon/chart/UI
  dependencies, search, filtering, dark mode, notifications, and the guided
  explainability timeline. A later task may add the timeline only from real
  workflow events.

**Work and tests**

- [x] **Step 1: Lock the application shell and home behavior with failing React
  tests.** Assert that authenticated users receive only working Home and Scans
  navigation, can return home, see truthful counts derived from loaded scans,
  and retain the existing start-scan, loading, empty, and error behaviors.
- [x] **Step 2: Add minimal presentation formatting.** Display human-readable
  dates, relative timestamps, quantities, percentages, and currency using
  browser-native formatting; do not round away a material value or change the
  API/domain representation.
- [x] **Step 3: Lock and build the executive summary.** Test then implement the
  four icon-backed coverage, shortage, truthful offer, and recommendation
  cards; show a green no-risk state only for an empty risk array and warning
  treatment for actual flags.
- [x] **Step 4: Lock and build visual evidence.** Test then implement a
  dependency-free accessible SVG projection with a reorder threshold and an
  expandable exact-value table. Show eligible offers first, rejected offers
  separately, and never infer a best vendor when more than one remains.
- [x] **Step 5: Modernize applied preferences.** Render ordered priority chips,
  scope/revision and enforcement badges, premium cap, baseline cost, and
  truthful offer outcomes using the current typed response only.
- [ ] **Step 6: Verify and release the affected surface.** Local verification is
  complete: all React tests, frontend lint, frontend production build, and
  repository diff checks passed. Remaining: publish the changed frontend
  release, wait for dev Argo `Synced` and `Healthy`, and verify the exact dev
  release through the existing smoke and desktop/narrow browser checks.

**Verification:** Run `npm test -- tests/scan.test.tsx`, `npm run lint`, and
`npm run build` from `frontend/`, followed by `git diff --check`. After release
reconciliation, run `make smoke-dev` and inspect the scan detail at desktop and
narrow viewport widths. Do not run Odoo contracts or backend integration suites
unless a shared contract unexpectedly changes.

**Dependencies:** T26.

**Requirements:** CR-02, CR-04, CR-13, CR-14, CR-15; spec sections 5, 7, 8,
10, 14, 20, and 26.

**Complete when:** Officers and managers can understand the read-only
recommendation and its key decision facts at a glance, inspect all exact
supporting evidence on demand, and use the page at desktop and narrow widths
without any backend or workflow behavior change.

#### T27 — Produce a contextual AI recommendation with safe fallback

**Files**

- Add/finalize graph nodes for evidence assembly, reasoning, output validation,
  manual review, and final audit under `src/procurement/agent/`.
- Finalize `src/procurement/agent/prompts/procurement_system.md` and structured
  recommendation schemas.
- Add rationale/risk/uncertainty React components and LLM/MCP dashboard panels.
- Add graph, mocked-LLM, real-transport, prompt-boundary, API, React,
  observability, and live Bedrock smoke tests.

**Interfaces**

- Consumes: the complete T25 deterministic evidence and T26 immutable typed
  preference snapshot.
- Produces: `recommend` with one eligible offer ID and bounded explanation, or
  `manual_review`; it produces no ERP write authority.

**Work and tests**

- [ ] **Step 1: Freeze the structured-output boundary.** Test valid recommend,
  manual review, unknown/ineligible offer, copied-number mismatch, omitted
  warning, hard-policy bypass, malformed schema, and untrusted business text.
- [ ] **Step 2: Invoke Bedrock with bounded context.** Supply only eligible
  alternatives, authoritative quantities/costs/performance/budget, and safe
  typed preferences. Preserve the fixed system prompt and selected model.
- [ ] **Step 3: Validate every output against evidence.** The LLM cannot change
  eligibility, quantity, price, budget arithmetic, evidence hash, or hard
  preference enforcement and cannot call Odoo writes.
- [ ] **Step 4: Implement bounded retry and fallback.** Apply the approved
  transient retries and one schema repair. On repeated timeout, failure, or
  invalid output, show the deterministic comparison, enter manual review, and
  create no draft.
- [ ] **Step 5: Expose explanation and observability.** Show concise trade-offs,
  risks, uncertainty, evidence limitations, budget/preference acknowledgement,
  and emit bounded token/latency/retry/invalid/fallback metrics and sanitized
  logs.
- [ ] **Step 6: Verify with mocked and real model paths.** Exercise differing
  company/category/product priorities, malicious text, timeout, repair,
  fallback, and one live selected-model invocation in dev.

**Verification:** Run agent/prompt/UI tests, `make test-integration`, live
Bedrock dev smoke, observability queries, and release promotion checks.

**Dependencies:** T26A.

**Requirements:** CR-02, CR-03, CR-05, CR-12, CR-13, CR-15; spec sections
4.3, 8.7, 9, and 19.

**Complete when:** Bedrock contributes contextual judgment inside the
deterministic safe set, and every invalid or unavailable-model path falls back
without a draft.

##### Approved T27 live-repair and demonstration amendment — 2026-08-16

The user approved the focused design in
`docs/superpowers/specs/2026-08-16-t27-live-repair-and-demo-design.md`. The
detailed execution plan is
`docs/superpowers/plans/2026-08-16-t27-live-repair-and-demo.md`. This amendment
retains the existing single-result T27 architecture and strict deterministic
validation while repairing the observed GPT-OSS flat-output/warning/token
boundary, preserving truthful historical approval-ready presentation, applying
only the T27-supported portions of the three supplied UI references, and
reconciling exactly four idempotent fictional Odoo scenarios with three offers
per product. It adds no draft, approval, confirmation, multi-result scan API,
or direct production mutation.

#### T28 — Create one idempotent draft and pause for manager decision

**Files**

- Create `src/procurement/mcp_server/tools/create_draft.py`,
  `src/procurement/mcp_server/idempotency.py`, and graph draft/interrupt nodes.
- Extend case/checkpoint/evidence/revision/audit repositories, case API output,
  and React recommendation detail.
- Add unit, MCP transport, concurrency, ambiguous-write, restart/resume, Odoo,
  API, UI, observability, and dev-smoke tests.

**Interfaces**

- Consumes: one validated T27 recommendation and its exact T25/T26 evidence.
- Produces: at most one traceable Odoo draft, case/evidence hash, current PO
  revision, durable checkpoint, and `PendingApproval` state.

**Work and tests**

- [ ] **Step 1: Write idempotency and ambiguity tests.** Cover repeat and
  concurrent calls, response loss after Odoo commit, process termination after
  write, conflicting case/reference, revision changes, restart, and no long-
  held HTTP request.
- [ ] **Step 2: Create from authoritative inputs only.** Permit only the
  validated offer, deterministic quantity/date/cost, exact preference snapshot,
  and evidence hash. Store the stable case ID in Odoo origin/reference.
- [ ] **Step 3: Coordinate Odoo and DynamoDB idempotency.** Use conditional
  application records and the existing atomic Odoo contract. On timeout or
  ambiguous response, inspect both systems before retry and enter
  `RECONCILIATION_REQUIRED` when safe resolution is unavailable.
- [ ] **Step 4: Persist and interrupt.** Record PO ID/revision, immutable
  evidence, checkpoint, and audit, then return control without holding an HTTP
  request while waiting for a manager.
- [ ] **Step 5: Expose safe UI and observability.** Show the draft link,
  revision, evidence summary, and pending state; emit bounded create,
  idempotency, ambiguity, reconciliation, and wait metrics/logs.
- [ ] **Step 6: Verify restart-safe behavior.** Run focused tests, real MCP
  transport, real Odoo dev creation, process restart/resume, and release smoke.

**Dependencies:** T27.

**Requirements:** CR-02, CR-03, CR-05, CR-06, CR-12, CR-13, CR-15; spec
sections 7, 9, 11, and 19.

**Complete when:** A valid recommendation creates at most one evidence-bound
draft and waits durably for a manager with ambiguous writes reconciled before
retry.

#### T29 — Complete the approve/confirm and reject/cancel lifecycle

**Files**

- Create approval/decision domain and service modules,
  `src/procurement/mcp_server/tools/confirm.py`,
  `src/procurement/mcp_server/tools/cancel_draft.py`, and
  `src/procurement/api/routes/decisions.py`.
- Add approve/reject React controls, budget-exception UI, audit timeline,
  approval/confirmation/cancellation metrics, dashboards, alerts, and tests.
- Do not create a request-change API, graph branch, or React action; retain the
  already implemented Odoo update method without wiring it into the MVP.

**Interfaces**

- Approve consumes: authenticated manager, exact case/vendor/quantity/amount,
  budget state and exception fields, immutable evidence hash, and current PO
  revision. It produces an immutable approval record before confirmation.
- Reject consumes: authenticated manager, exact case/PO revision, bounded
  reason, and idempotency key. It produces immutable rejection evidence and a
  cancelled draft or explicit reconciliation state.

**Work and tests**

- [ ] **Step 1: Freeze authorization and binding tests.** Cover officer denial,
  environment mismatch, altered vendor/quantity/amount/budget/evidence,
  stale/current PO revision, expired/replayed decision, concurrent decisions,
  and idempotent repeats.
- [ ] **Step 2: Persist approval immutably.** Store the authenticated manager,
  exact decision payload, exception state/justification, evidence hash, PO
  revision, timestamp, and expiry using a conditional write. Never mutate or
  reuse an approval for another revision.
- [ ] **Step 3: Enforce the budget exception.** Over-budget remains eligible,
  but confirmation requires an explicit exception flag and non-empty bounded
  justification unless a separately specified deterministic hard policy has
  already excluded the offer.
- [ ] **Step 4: Independently revalidate before confirmation.** MCP performs a
  strongly consistent approval read and matches every bound field plus the
  current Odoo revision immediately before calling only
  `action_stockai_confirm(expected)`. Odoo locks, rereads, compares, and calls
  its standard confirmation in one transaction.
- [ ] **Step 5: Implement rejection and cancellation.** Persist the immutable
  rejection, call only `action_stockai_cancel_draft(expected)`, preserve the
  audit record, and close after idempotent cancellation. Reconcile ambiguous
  cancel/confirm results before any write retry.
- [ ] **Step 6: Expose the bounded manager UI.** Present approve, budget
  exception, and reject only. Remove request-change states/endpoints/actions
  from contracts and tests; show exact evidence/revision and chronological
  immutable audit.
- [ ] **Step 7: Run safety and real-environment verification.** Exercise happy,
  over-budget, rejection, stale, replay, role, concurrency, response-loss,
  restart/reconcile, alert, and real Odoo paths. Confirm no supplier contact,
  payment, legal ordering, or autonomous approval occurs.

**Verification:** Run focused decision/API/UI/Odoo tests,
`make test-integration`, `make smoke-dev`, `make smoke-prod`, audit inspection,
safety-alert evidence, and exact release promotion.

**Dependencies:** T28.

**Requirements:** CR-02, CR-05, CR-06, CR-12, CR-13, CR-15; spec sections 6,
7.3, 8.6, 11.3, 13, and 19.

**Complete when:** Every confirmation uses a current immutable independently
revalidated manager approval, every rejection cancels safely, and no
request-change/update/reapproval product path exists.

### Phase 6 — Security, resilience, acceptance, and presentation

#### T32 — Verify security boundaries and close remaining gaps

**Files**

- Modify application headers/limits/redaction, Kubernetes NetworkPolicy/RBAC/
  security contexts, External Secrets, IAM documentation, and tests only where
  verification finds an actual gap.
- Create or finalize `docs/runbooks/secret-rotation.md` and
  `docs/runbooks/security-incident.md`.
- Update `docs/implementation-status.md` with the T18C residual-risk decision.

**Work and tests**

- [ ] **Step 1: Verify existing workload and network controls.** Test default-
  deny paths, documented allows, namespace RBAC/service accounts, non-root,
  dropped capabilities, seccomp, read-only roots, explicit writable volumes,
  and no cross-environment access.
- [ ] **Step 2: Verify application boundaries.** Test browser headers, request
  limits, CSRF, session fixation, role escalation, preference administration,
  immutable approval, untrusted MCP output, business-data/prompt injection,
  stable safe errors, and recursive redaction.
- [ ] **Step 3: Perform representative live rotation.** Rotate the Odoo
  integration key through the protected exact-secret policy window and rotate
  one application/session credential without logging values. Automatically
  test and document the same delivery/reload mechanism for MCP/Cron, database,
  and Grafana credential classes instead of repeating equivalent live drills.
- [ ] **Step 4: Review IAM and repository security.** Confirm OIDC rather than
  long-lived AWS keys, accepted single-region role naming, accurate plan/apply
  descriptions, bootstrap explicit denies, environment permissions, secret
  scanning, dependency/image/configuration reports, and known broad T21B
  course-account residual risk.
- [ ] **Step 5: Resolve the T18C risk decision.** Either implement an approved
  automated CA-signed kubelet serving-certificate lifecycle or explicitly
  accept and document the bounded restricted-network
  `--kubelet-insecure-tls` limitation. Do not ignore it.
- [ ] **Step 6: Close only demonstrated gaps and rerun security checks.** Add
  focused regressions for each fix and record accepted residual risks. Do not
  redesign working Cognito, DynamoDB, Odoo, Kubernetes, or IAM architecture.

**Verification:** Run `make security-scan`, authorization suites,
NetworkPolicy/policy/render checks, representative dev rotations, IAM review,
and relevant regression suites.

**Dependencies:** T29.

**Requirements:** CR-09, CR-13, CR-15; spec sections 11.3, 17.5, 20, and 22.

**Complete when:** Existing trust boundaries have executable evidence, actual
gaps are closed, representative rotation works, and every residual risk is
explicit rather than silently ignored.

#### T33 — Run representative resilience, capacity, recovery, and cost drills

**Files**

- Create or finalize focused scenarios under `tests/resilience/` and
  `tests/load/`, plus `docs/runbooks/recovery.md` and
  `docs/runbooks/active-periods.md`.
- Modify workload/resource hypotheses, alerts, dashboards, and
  `docs/implementation-status.md` only when measured evidence requires it.

**Work and tests**

- [ ] **Step 1: Verify detailed behavior offline.** Run automated timeout,
  retry/no-retry, 120-second case bound, graceful termination, checkpoint,
  idempotency, reconciliation-before-write-retry, lifecycle, and graph-state
  tests. Do not reproduce every state as a separate live SIGTERM drill.
- [ ] **Step 2: Run two interruption scenarios.** Interrupt one active
  read/reasoning workflow and one persisted/waiting or write-sensitive boundary;
  verify readiness failure, bounded 45-second shutdown, checkpoint/reconcile,
  and safe resume or manual review.
- [ ] **Step 3: Demonstrate HPA plus manual capacity.** Load frontend/API/MCP,
  verify HPA demand and scale-down, capture pending pods when one worker is
  insufficient, apply only dev ASG desired capacity 1→2 through reviewed
  Terraform, verify the new correctly labeled worker and scheduling, then
  restore desired one. Do not add a node autoscaler.
- [ ] **Step 4: Reuse T23 worker recovery evidence.** Verify that the recorded
  T23 release/evidence remains applicable; rerun only a focused check if worker
  or retained-volume behavior changed.
- [ ] **Step 5: Exercise snapshot recovery.** When practical, restore the prod
  Odoo/PostgreSQL tagged snapshots into an isolated reviewed recovery path,
  verify expected fictional data and application consistency, and document
  cleanup. If a real restore is blocked by a documented external constraint,
  stop and obtain approval rather than substituting an unclaimed result.
- [ ] **Step 6: Run one shutdown/restart drill.** Through reviewed Terraform,
  set worker ASGs to min/desired zero, stop the fixed control plane, then
  restart it, verify finite token rotation, restore desired workers, observe
  the warming state, and complete an authorized scan. Do this once only.
- [ ] **Step 7: Record resource and cost evidence.** Measure steady one-worker
  resources, temporary two-worker capacity, shutdown, retained ALB/storage,
  and the current $70 target/$90 manual review threshold. Preserve actionable
  pending-capacity and cost/runbook evidence without inventing AWS Budget.

**Stop condition:** Stop for an approved resource/design revision if a complete
environment cannot fit below the specified safety margin, HPA/manual capacity
cannot work as documented, a worker cannot join with the correct identity,
state cannot reattach/restore, shutdown cannot recover safely, or any write
cannot reconcile. Do not add Cluster Autoscaler or weaken safety as a shortcut.

**Verification:** Run `make test-resilience`, focused load tests, the two live
interruption scenarios, reviewed capacity change, snapshot recovery evidence,
one shutdown/restart drill, and dashboard/event inspection.

**Dependencies:** T32.

**Requirements:** CR-05, CR-09, CR-12, CR-13, CR-16; spec sections 17, 19,
22.5, and 23.

**Complete when:** Representative live scenarios plus detailed automated tests
support the claimed graceful interruption, HPA/manual capacity, retained-state,
snapshot recovery, non-24/7 operation, and cost behavior without duplicated
drills.

#### T34 — Accept the release candidate and assemble requirement evidence

**Files**

- Create/finalize `tests/acceptance/`, `docs/runbooks/demo-health.md`, and the
  CR-01–CR-16 evidence index in `docs/implementation-status.md`.
- Modify existing dashboards, alert rules, and `docs/runbooks/alerts.md` only
  for acceptance gaps; do not recreate the T20B observability implementation.

**Work and tests**

- [ ] **Step 1: Run the complete automated suite once.** Execute all quality,
  unit, integration, UI, Compose, Odoo contract, infrastructure, Kubernetes,
  security, resilience, release, and build targets and retain actual reports.
- [ ] **Step 2: Run final dev/prod acceptance.** Verify public smoke paths,
  environment isolation, Argo health, immutable exact-release evidence, and
  byte-identical dev-tested prod image digests.
- [ ] **Step 3: Verify observability.** Confirm required request/error/latency,
  LLM/token, MCP/retry/timeout, procurement-safety, Kubernetes/resource/HPA,
  dependency/edge, ASG/Ready-node, lifecycle, storage, and log signals using
  real data; confirm redaction and Loki/S3 retention.
- [ ] **Step 4: Validate alerts proportionally.** Validate every rule
  automatically, then live-fire one application/dependency alert, one
  capacity/infrastructure alert, and one worker-lifecycle alert. Reuse valid
  T20B/T23/T33 evidence and do not manufacture every failure again.
- [ ] **Step 5: Map CR-01 through CR-16.** Link each requirement to actual test,
  workflow, Terraform, Argo, smoke, dashboard, runbook, or live-drill evidence;
  record known residual risks and never mark planned-only evidence complete.

**Verification:** Archive actual JUnit/coverage/scan/render/plan/smoke/release/
Argo/dashboard evidence and review the complete requirement matrix.

**Dependencies:** T33.

**Requirements:** CR-01 through CR-16.

**Complete when:** The exact release candidate passes required checks, every
course requirement has truthful evidence, representative alerts work, and no
earlier acceptance work is needlessly repeated.

#### T35 — Prepare and rehearse the final demo and presentation

**Files**

- Create `docs/demo/demo.md` and `docs/demo/evidence.md`.
- Do not create six separate documents unless course staff later requires
  separate submission artifacts.

**Work and tests**

- [ ] **Step 1: Record the manual baseline.** Time the documented manual
  replenishment workflow at least three times and record method, fictional
  inputs, results, and comparison limitations in `docs/demo/demo.md`.
- [ ] **Step 2: Assemble the 15-minute script.** Include introduction, problem
  and value, architecture, deterministic/LLM boundary, MCP/Odoo, AWS/Terraform,
  self-managed Kubernetes, testing, security, observability, GitHub Actions,
  Argo CD, same-digest promotion, and AI-agent reflection.
- [ ] **Step 3: Rehearse the user-facing scenarios.** Demonstrate happy path,
  company/category/product preference behavior, over-budget exception,
  immutable manager approval, rejection/cancellation as time permits, draft
  and confirmation, audit, metrics/logs, and safe fallback.
- [ ] **Step 4: Build truthful evidence and fallback.** Put screenshots,
  exported evidence references, pipeline/release provenance, skills used,
  reflection, and failure fallback in `docs/demo/evidence.md`. Do not present
  recorded evidence as live and do not generate a substitute video.
- [ ] **Step 5: Rehearse twice.** Run at least two timed rehearsals, one with a
  safe injected failure, resolve critical blockers, and keep the final path
  within 15 minutes. Reuse the single T33 shutdown evidence rather than adding
  another cold-start drill solely for presentation.

**Verification:** Review both consolidated documents, run two timed rehearsals,
and verify every live link, account, fictional seed, dashboard, workflow, Argo
view, and fallback artifact before presentation day.

**Dependencies:** T34.

**Requirements:** CR-02 and CR-14; spec section 24.

**Complete when:** The user can explain every major decision and deliver the
full live interaction, observability, pipeline, reflection, and fallback within
15 minutes.

## 9. Phase exit gates

| Gate | Required evidence | System condition |
|---|---|---|
| G0 — Planning | User approval, course-staff PR approval, explicit user implementation instruction | Implementation may start |
| G0R — T10 Odoo revision | User review of the exact 2026-08-07 spec/plan, course-staff PR approval, explicit user resume instruction | T10 may resume under the selected add-on and ORM-bootstrap design |
| G1 — Local skeleton | Unit/integration reports and manual browser check | Local API → LangGraph → real MCP transport → result works |
| G2 — Odoo boundary | Executable Odoo contract, repeatable seed, live MCP read | No unresolved Odoo contract assumption |
| G3 — Container | Image builds, Compose E2E, image contract checks | Local system runs from pinned containers |
| G4 — Platform | Guided T21A configuration plus protected T21B lifecycle evidence, Terraform/cluster/Kustomize/CI validation, and bounded clean/fail-open node-replacement drills | Reproducible AWS, automatically synchronized configuration, isolated worker ASGs, and a healthy shared Kubernetes platform |
| G5 — Dev skeleton | Exact-release Bedrock/Odoo/MCP/DynamoDB/Cognito smoke, append-only validation evidence, retained-volume replacement, and observability evidence | Full walking skeleton healthy, exact-release validated, and replacement-safe in dev |
| G6 — Prod skeleton | Same-digest proof, Argo health, prod smoke | Promotion workflow proven |
| G7 — Functional MVP | T25–T29 vertical slices and safety tests | Deterministic evidence, revisioned preferences, contextual recommendation, idempotent draft, approve/confirm, and reject/cancel work end to end |
| G8 — Release candidate | T32 security evidence, T33 representative resilience/recovery/cost drills, and T34 CR-01–CR-16 acceptance | MVP is submission-ready without duplicated acceptance work |
| G9 — Presentation | Two timed rehearsals and fallback evidence | Fifteen-minute demo is ready |

No stretch work may begin before G9.

## 10. Requirements-to-task traceability

| Requirement | Primary implementation tasks | Acceptance evidence |
|---|---|---|
| CR-01 Planning gates | Current plan, T34 | Approved spec/plan PRs and explicit implementation instruction |
| CR-02 Business problem/value | T11A–T11B, T25–T29, T26A, T35 | Timed baseline, preference-aware approval-ready latency, understandable decision-first UI, approve/reject live workflow |
| CR-03 Coded LLM framework | T05, T12, T25–T29 | LangGraph tests, deployed graph, real model evidence |
| CR-04 HTTP API/UI | T03, T05, T06, T14, T25–T29, T26A | API/UI tests, live dashboard, decision-first scan detail, typed Odoo preference UI |
| CR-05 Reliability contracts | T02–T05, T12, T18B, T23, T25–T29, T32–T33 | Errors, preference validation, immutable approval, retries, fallback, lifecycle bounds, reconciliation, representative shutdown tests |
| CR-06 Real MCP interaction | T04, T07, T11A–T11B, T25–T29 | Ten-tool Streamable HTTP tests and demo traces |
| CR-07 Self-managed EC2 Kubernetes | T16, T18A–T18C, T21A–T21B | Terraform state, protected provisioning, ASG/node inventory, finite join, controlled replacement, no EKS |
| CR-08 Complete dev/prod | T17, T19A–T24 | Generated separate configuration, full-stack overlays, namespaces, Argo apps, smoke |
| CR-09 Workload quality | T18A–T20B, T32, T33 | Probes, resources, HPA, retained CSI volumes, secrets, graceful shutdown/drain evidence |
| CR-10 Terraform | T15–T18B, T21A–T21B | Validated/applied ASG, lifecycle, storage, edge, service state, protected orchestration/destruction, and reproducible runbooks |
| CR-11 CI/CD/GitOps | T21–T24, T26 | Complete four-image dev releases, one content identity, exact validation evidence, Argo reconciliation, same-digest promotion |
| CR-12 Observability | T03–T05, T18B, T20A–T20B, T23, T25–T29, T32–T34 | Application/ASG/cleanup metrics, logs, S3 objects, dashboards, automatically validated rules, representative live alerts |
| CR-13 Automated testing | Every behavior task; T34 audit | Unit/integration/UI/smoke/JUnit/coverage evidence |
| CR-14 Presentation | T06, T23, T26A, T29, T34, T35 | Understandable scan detail, timed live demo, dashboard, pipeline, reflection |
| CR-15 Security | T02–T04, T11A–T11B, T12–T29, T32–T33 | IAM/RBAC/CSRF/idempotency/redaction/network/preference/immutable-approval tests |
| CR-16 Decision/AWS justification | T15–T18B, T21A–T21B, T23, T33–T35 | Plans, protected deployment evidence, lifecycle/cost evidence, implementation status, explanation |

## 11. Test coverage map

| Behavior | Unit | Integration | Deployed smoke or acceptance |
|---|---|---|---|
| Forecast/trigger/need-by | T25 | T25 real MCP transport | T25 real Odoo |
| Duplicate/full/partial coverage | T25 | T25 concurrency | T25 seeded open PO |
| Offer eligibility/quantity | T25 | T25 MCP + Odoo adapter | T25 vendor comparison |
| Performance evidence | T25 | T25 MCP + Odoo adapter | T25 seeded receipts/returns |
| Budget and overage | T11A model/ACL; T25 policy/UI | T11A Odoo contract; T25 MCP + Odoo adapter | T25/T29 exception path |
| Preference revision and Odoo authorization | T26 | T26 add-on/container/release tests | T26 Odoo administration smoke |
| Preference resolution and premium | T26 | T26 real MCP + Odoo add-on | T26 company/category/product scenarios |
| Preference snapshot/prompt/UI binding | T26 | T26 graph/API/React tests | T26 real MCP and read-only applied revision |
| Scan-detail presentation | T26A | T26A focused React accessibility/state tests | T26A exact dev smoke and responsive browser check |
| LLM recommendation/fallback | T12/T27 mocked | T27 graph + MCP | T27 real Bedrock |
| Draft/idempotency | T28 | T28 concurrency/ambiguous result | T28 real Odoo |
| Approval/confirmation | T11A atomic Odoo contract; T29 approval/MCP | T11A/T29 stale/replay/role/exception | T29 real Odoo |
| Reject/cancel/reconcile | T29 | T29 failures/restarts | T29 real Odoo |
| API/auth/CSRF | T03/T05/T14/T25–T29 | T14/T25–T29 | T23/T24/T26/T29 |
| React states/actions | T06/T14/T25–T29/T26A | Local browser E2E | Dev/prod browser smoke |
| MCP tools | T04/T11B/T25–T29 | All ten over Streamable HTTP | Real Odoo demo traces |
| AWS repositories | T12–T14 mocked | DynamoDB Local | Dev/prod AWS smoke |
| Worker bootstrap and termination | T18A/T18B mocks | Terraform/event/IAM/SSM integration checks | Clean and fail-open dev replacement drills |
| Kubernetes/config, ingress, and storage | T17–T20B static | Terraform/render/policy/resource tests | ASG/ACM/ALB/NGINX health, six EBS bindings/reattachment, Grafana reconstruction, Argo recovery |
| Release identity/validation/promotion | T22/T24 | T22–T24 workflow/release tests | Exact dev evidence and same-digest prod smoke |
| Security/shutdown/load | T32/T33 | Automated fault injection | Representative rotations, interruptions, HPA/capacity, snapshot restore, and one shutdown/restart drill |

## 12. Risk-driven stop conditions

Implementation stops for review when any of these occurs:

- Odoo 19 Community, JSON-2, or the narrowly approved StockAI add-on cannot
  provide a required standard or extension contract.
- The StockAI Odoo add-on cannot enforce budget uniqueness, atomic
  revision-bound PO actions, one typed current preference per scope,
  server-managed monotonic revisions, Odoo change tracking, or least-privilege
  administration without materially broader authority than approved.
- The selected Bedrock model is unavailable in the approved region/account or
  violates the expected IAM invocation contract.
- The complete stack cannot fit safely on each `t3.medium`/30 GB worker after
  reducing nonessential retention/cardinality/caches.
- The frontend, API, or MCP HPA cannot demonstrate safe 50% CPU scale-up and
  scale-down through the documented normal/manual capacity sequence without
  unacceptable pending/OOM behavior.
- A worker cannot join with the correct environment identity from the finite
  rotating SSM credential, or dev/prod roles/labels/taints can cross.
- A termination can remain stuck beyond the 300-second bound, skip lifecycle
  completion without default release, or execute cleanup for an unapproved
  ASG/node identity.
- ACM validation, ALB hostname routing, ASG target registration, or
  ALB-source-only NodePort access cannot be proven without broader exposure.
- Odoo/PostgreSQL/Prometheus state cannot reattach to a same-AZ replacement,
  Prometheus cannot retain the agreed bounded history within 5 GiB, or Grafana
  cannot be fully reconstructed from Git after pod loss.
- Environment-specific node roles or scheduling do not prevent a prod workload
  from using dev permissions, or vice versa.
- External Secrets cannot access only the intended environment secrets without
  broadening the control-plane role.
- A write cannot be reconciled safely after an ambiguous timeout.
- A manager approval cannot be immutably persisted and independently
  revalidated against the exact evidence hash and current PO revision before
  confirmation.
- Dev and prod cannot use the identical immutable four-image digest map, or
  release promotion cannot distinguish content identity from traceability
  commit differences.
- A new requirement would add an unapproved external integration, AWS service,
  high-impact action, or materially different architecture.

The response is to preserve evidence, update the specification and plan, obtain
approval, and only then continue. It is never to silently weaken the
requirement or add an undocumented workaround.

## 13. Conditional post-MVP backlog

This section preserves the broader product direction requested during
brainstorming. It is not part of the MVP completion criteria and is not
authorized until G9 is complete with submission time remaining.

Each stretch slice requires a small design update, tests, observability, dev
validation, same-artifact prod promotion, and user approval before it starts.
The order is:

1. **S01 — Event-driven monitoring and PO consolidation.** Add safe Odoo event
   ingestion and cross-product/same-vendor consolidation while retaining the
   daily reconciliation scan.
2. **S02 — Operational notifications.** Add one manager notification adapter
   first, then consider email, Slack, and Teams behind the same interface.
   Notifications must never become an approval bypass.
3. **S03 — Supplier discovery and document/contract evidence.** Add
   allowlisted supplier sources, contract/document-management retrieval,
   provenance, expiry checks, and human onboarding. No discovered vendor may
   become approved autonomously.
4. **S04 — Calendar and accounting integrations.** Add calendar scheduling and
   accounting reconciliation only when each has a specific measurable
   workflow. Avoid generic connector demonstrations.
5. **S05 — Additional ERP adapters.** Implement one second ERP adapter against
   the stable Procurement MCP contract to test the abstraction. Only then
   consider SAP, Oracle, NetSuite, Microsoft Dynamics, and other ERP systems.
6. **S06 — Multi-company preference isolation and policy authoring.** Add
   tenant-aware administration only after the single-company profile,
   category/product inheritance, audit, and authorization boundaries are
   proven.
7. **S07 — Bounded manager change and reapproval.** If real user evidence
   justifies it, design a small allowlisted request-change path using the
   existing atomic Odoo update primitive, new immutable evidence, approval
   invalidation, recomputation, and reapproval. It requires a separate spec and
   plan amendment and is not implied by the MVP API.
8. **S08 — Real node autoscaling.** If Phase 1 and G9 are complete with time
   remaining, revise and re-approve the specification and plan before adding
   Cluster Autoscaler to manage the existing dev/prod ASGs. Keep all HPAs and
   validate scale-up, scale-down, drain, cost, IAM, and retained-volume behavior.

Supplier communication, payment, autonomous vendor approval, unbounded budget
exceptions, and real legal ordering require a new safety design and are not
implicitly authorized by finishing the MVP.

## 14. Plan review checklist

The user and course staff should confirm:

- The task order matches the course’s walking-skeleton-first strategy.
- Each task is small enough to review and has concrete files and checks.
- All ten MVP MCP tools and their real transport are covered; the existing
  Odoo update primitive is not exposed as an MVP tool.
- Preference administration is structured and revisioned, never a raw prompt
  editor, and product/category/company precedence is unambiguous.
- Preference configuration and case approval are separately authorized, with
  read-only applied preference evidence visible to officers and managers.
- Odoo feasibility is tested before broad dependence on its data model.
- Every confirmation has an immutable manager approval independently
  revalidated against the exact evidence hash and current PO revision; reject
  safely cancels, and request-change/update/reapproval is outside the MVP.
- Dev and prod are complete, isolated, and constrained to their approved
  single-AZ worker ASGs, labels/taints, roles, and target groups.
- Normal active capacity is one fixed control plane plus one desired worker in
  each ASG; Phase 1 has no ASG scaling policy or node autoscaler.
- The Terraform-managed ALB/ACM/Route 53 path is reproducible, uses
  environment-specific ASG target membership, and leaves no public worker
  application port.
- Odoo filestore, PostgreSQL, and Prometheus receive dedicated retained EBS in
  each environment; Grafana is Git-provisioned and survives by reconstruction,
  not by persisting `/var/lib/grafana`.
- Finite kubeadm token rotation, private-DNS node identity, and termination
  lifecycle cleanup are bounded, least-privilege, idempotent, observable, and
  covered by clean/fail-open recovery drills.
- All AWS services are justified and provisioned through Terraform.
- GitHub Actions never deploys workloads directly.
- Every relevant dev release builds all four images, uses one deterministic
  content identity, locks its release core, and receives append-only exact-
  release validation evidence before the exact digests reach prod.
- Tests cover happy paths, failures, malformed outputs, timeouts, retries,
  fallbacks, concurrency, and ambiguous writes.
- Logs reach Loki and encrypted S3 without leaking sensitive procurement data.
- Dashboards and alerts cover application, LLM, MCP, Kubernetes, dependencies,
  ALB/HTTPS edge health, exact request/latency/error/token panels, and
  procurement safety.
- Cost, disk, memory, non-24/7 operation, retained-volume replacement, and prod
  snapshot recovery limitations are tested with representative non-duplicated
  live drills and detailed automated coverage.
- Stretch integrations remain blocked until the submission-ready MVP is
  complete.

## 15. Next approval gate

T01–T21B history and authorization remain governed by their recorded approval
and live-infrastructure gates in `docs/implementation-status.md`. This amendment
does not retroactively change completed work.

On 2026-08-14 the user approved the focused simplification design and exact
written `docs/spec.md` amendment and confirmed renewed course-staff approval.
This exact `docs/plan.md` amendment must now be reviewed and approved by the
user and course staff. After both approvals, the user must explicitly authorize
T22 implementation; approval of this plan does not itself authorize code,
workflow, infrastructure, deployment, test, or live-environment changes.

Protected Terraform apply/destroy operations, live AWS changes, worker
capacity changes, snapshot restore, and the shutdown/restart drill retain their
separate explicit execution approvals even after implementation authorization.
