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

**Status:** T21 implementation and local verification complete; awaiting
review and pull-request workflow verification

**Date:** 2026-08-09

**Source design:** User- and course-staff-approved `docs/spec.md` dated 2026-08-07

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
PR #29 at `1e1b98d`. T21 implementation and local verification are complete;
its live pull-request workflow checks remain pending.

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
- Effective recommendation preferences resolve product, then category, then
  company scope and are snapshotted by immutable version on each case.
- Hard eligibility and approval policy always outrank advisory preferences;
  configured hard price-premium limits are enforced deterministically.
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
  revision-bound PO methods, typed preference models, constraints, access
  control, administration views, and immutable preference history. It contains
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
10. Copy or cherry-pick the generated, verified release manifest back to the
    feature branch and open its pull request to `main`.
11. Merge only after required tests, validation, and Docker Scout checks pass.
12. Let the main workflow promote the same immutable digest; let prod Argo CD
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
| `make smoke-dev` | Public HTTPS, auth, real Bedrock, real MCP, real Odoo, DynamoDB, audit, metrics, and logs |
| `make smoke-prod` | Same critical path against prod with prod-only fictional seed data |
| `make test-resilience` | HPA/manual ASG capacity, clean/fail-open termination, retained-volume reattachment, inactive/startup warming, and recovery drills |
| `make verify-release` | Verify source revision, image digests, attestations, dev evidence, and prod promotion identity |

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

**Verification:** Validate dashboard and rule syntax, load every dashboard,
fire one safe test alert from each alert category, and follow its runbook.

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
- [x] **Step 3:** Run Docker Scout on pull requests targeting `main`.
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

#### T22 — Implement dev build, GitOps update, and Argo CD reconciliation

**Files**

- Create `.github/workflows/dev-images.yml`,
  `deploy/kubernetes/argocd/dev-application.yaml`,
  `scripts/release/update_dev_overlay.py`, and
  `tests/unit/release/test_update_dev_overlay.py`.

**Work and tests**

- [ ] **Step 1:** On relevant `dev` pushes, build only changed project images, publish
   immutable Docker Hub digests, create provenance, run Docker Scout, and
   update the dev overlay and release manifest.
- [ ] **Step 2:** Prevent workflow loops on bot-only desired-state commits.
- [ ] **Step 3:** Configure dev Argo CD to track the `dev` revision and dev overlay.
- [ ] **Step 4:** Query Argo CD through its authenticated API for sync/health status; do not
   use `kubectl` in GitHub Actions.
- [ ] **Step 5:** Define how the generated release manifest is copied or cherry-picked back
   to the originating feature branch before its main pull request.

**Verification:** Run a no-change path, one-image path, four-image path,
tampered digest path, Argo failure path, and successful dev reconciliation.

**Dependencies:** T21.

**Requirements:** CR-08, CR-11, CR-15; spec section 18.3.

**Complete when:** A dev push changes Git desired state and Argo CD, not GitHub
Actions, performs deployment.

#### T23 — Deploy and validate the cloud walking skeleton in dev

**Files**

- Create `tests/smoke/test_dev_skeleton.py`,
  `scripts/smoke/dev.sh`, and
  `docs/runbooks/dev-validation.md`.
- Update dev dashboard panels and `docs/implementation-status.md`.

**Work and tests**

- [ ] **Step 1:** Apply approved Terraform and bootstrap the cluster through reproducible CLI
   automation.
- [ ] **Step 2:** Reconcile the complete dev stack through Argo CD.
- [ ] **Step 3:** Seed fictional dev Odoo and bootstrap fictional Cognito users.
- [ ] **Step 4:** Exercise real Cognito login, real Bedrock GPT-OSS, real MCP transport, real
   Odoo candidate read, DynamoDB persistence, frontend polling, metrics, logs,
   and S3 Loki objects.
- [ ] **Step 5:** Record image digests, Argo status, smoke evidence, resource use, and cost
   observations in the release manifest.
- [ ] **Step 6:** Record fictional Odoo/PostgreSQL data and a Prometheus sample, terminate the
   dev worker through its ASG, and verify clean lifecycle completion, old Node
   removal, automatic replacement/join, dev labels/taint/role, reattachment of
   all three dev EBS volumes, application readiness, retained data, and restored
   ALB target health. Verify Grafana reconstructs from Git rather than EBS.

**Verification:** Run `make smoke-dev`; inspect the same correlation ID in UI,
API/MCP logs, metrics, DynamoDB audit, and Odoo.

**Dependencies:** T22 and authorized AWS apply/deployment.

**Requirements:** CR-02 through CR-13, CR-15, CR-16 as applicable to the
walking skeleton.

**Complete when:** Dev proves the full infrastructure and integration chain
with no unapproved write behavior.

#### T24 — Promote the identical walking-skeleton artifact to prod

**Files**

- Create `.github/workflows/main-promote.yml`,
  `deploy/kubernetes/argocd/prod-application.yaml`,
  `scripts/release/update_prod_overlay.py`,
  `tests/unit/release/test_update_prod_overlay.py`,
  `tests/smoke/test_prod_skeleton.py`, and
  `docs/runbooks/prod-promotion.md`.

**Work and tests**

- [ ] **Step 1:** Make the merge to protected `main` the explicit production decision.
- [ ] **Step 2:** Verify the release manifest and promote the exact dev-tested digests without
   rebuilding.
- [ ] **Step 3:** Update prod desired state in Git and let prod Argo CD reconcile `main`.
- [ ] **Step 4:** Use separate prod Cognito, tables, secrets, Odoo/PostgreSQL, seed,
   observability, hostnames, retained EBS volumes, ASG, role, labels/taint, and
   Availability Zone placement.
- [ ] **Step 5:** Query Argo CD through its API and run public prod smoke tests without
   `kubectl` in Actions.
- [ ] **Step 6:** Document rollback as a Git revert to a previously verified release manifest.

**Verification:** Prove digest identity across dev and prod, prod namespace
isolation, prod smoke success, and rollback of a deliberately bad health-check
configuration in a controlled exercise.

**Dependencies:** T23.

**Requirements:** CR-08, CR-10, CR-11, CR-12, CR-15; spec section 18.

**Complete when:** The minimal system is healthy in both namespaces and the
required promotion path has been exercised end to end.

### Phase 5 — Remaining procurement vertical slices

Each capability in this phase updates domain code, MCP, graph, API, UI, tests,
documentation, logs, metrics, and dashboard panels as relevant. A capability
may be split into ordered, independently reviewable tasks when one task would
otherwise be too large; the system remains runnable after each task. Each
completed task is validated in dev and promoted as the same immutable artifact
before the next dependent task begins.

#### T25 — Add replenishment projection and duplicate prevention

**Files**

- Create `src/procurement/domain/policy/forecast.py` and
  `src/procurement/domain/policy/duplicates.py`.
- Create MCP tools
  `src/procurement/mcp_server/tools/forecast.py` and
  `src/procurement/mcp_server/tools/open_purchase_orders.py`.
- Add graph nodes under `src/procurement/agent/nodes/inventory.py`.
- Add case list/detail routes under `src/procurement/api/routes/cases.py`.
- Add React overview, scan detail, case queue, forecast, and skip-reason
  components.
- Add corresponding unit, real-transport integration, and dev smoke tests.

**Behavior**

- [ ] **Step 1:** Implement 14-day projection from known stock movements only.
- [ ] **Step 2:** Distinguish reorder trigger date from need-by/stockout date.
- [ ] **Step 3:** Check pending cases, drafts, and confirmed incoming POs.
- [ ] **Step 4:** Handle full coverage, partial coverage, residual quantities, pagination,
   a 50-candidate limit, and at most three concurrent product workflows.
- [ ] **Step 5:** Audit skipped and duplicate-blocked cases.

**Verification:** Test date/timezone edges, missing movements, concurrency,
partial coverage, duplicate conditional writes, Odoo mapping, UI display,
metrics, and dev real-Odoo results.

**Dependencies:** T24.

**Requirements:** CR-02, CR-03, CR-06, CR-12, CR-13, CR-15; spec sections 7.1,
8.1, and 8.2.

**Complete when:** The system detects only uncovered shortages and cannot
create two active cases for one shortage.

#### T26 — Add approved-offer, quantity, and vendor-performance comparison

**Files**

- Create policy modules `offers.py`, `quantity.py`, and `performance.py`.
- Create MCP tools `offers.py` and `vendor_performance.py`.
- Add graph evidence nodes and React vendor comparison/evidence-confidence
  components.
- Add unit, Odoo adapter, MCP transport, API, React, and dev smoke tests.

**Behavior**

- [ ] **Step 1:** Enforce approved/unblocked vendor tags, offer validity, required price and
   currency, lead time, and delivery by need-by date.
- [ ] **Step 2:** Calculate quantity separately per offer using arrival projection, reorder
   maximum, MOQ, and packaging/UoM rounding.
- [ ] **Step 3:** Return normalized current order cost, projected inventory, and excess
   inventory without claiming landed cost.
- [ ] **Step 4:** Compute 365-day on-time rate, average positive lateness, return proxy,
   evidence counts, and insufficient-history status below three orders.
- [ ] **Step 5:** Display rejected offers with safe deterministic reasons.

**Verification:** Cover all eligibility branches, currency/decimal precision,
MOQ/packaging edges, missing data, history window edges, prompt injection-like
vendor text, and real Odoo evidence.

**Dependencies:** T25.

**Requirements:** CR-02, CR-03, CR-06, CR-12, CR-13, CR-15; spec sections
8.3–8.5.

**Complete when:** Every eligible offer has authoritative computed values and
every excluded offer has a deterministic reason.

#### T27 — Add category budget status and exception presentation

**Files**

- Create `src/procurement/domain/policy/budget.py` and
  `src/procurement/mcp_server/tools/budget.py`.
- Add budget evidence to graph state, case API schemas, React recommendation
  details, metrics, dashboard panels, and audit events.
- Add unit, Odoo contract, MCP transport, API, React, and dev smoke tests.

**Behavior**

- [ ] **Step 1:** Read the matching `stockai.procurement.budget` record and map
   product category to its approved analytic account and calendar-month period.
- [ ] **Step 2:** Calculate budget, current confirmed commitments, remaining before/after,
   and exact overage in authoritative code.
- [ ] **Step 3:** Keep an over-budget offer eligible but mark it as requiring explicit
   manager exception and justification.
- [ ] **Step 4:** Reject malformed, mismatched-period, or mismatched-currency budget data.

**Verification:** Test month boundaries, no budget record, exact budget,
overage, currency errors, UI warning prominence, sanitized logs, and the real
Odoo budget scenario.

**Dependencies:** T26.

**Requirements:** CR-02, CR-06, CR-12, CR-13, CR-15; spec sections 8.6 and 14.

**Complete when:** Every proposed amount has an authoritative budget result and
an overage cannot be visually or structurally hidden.

#### T27A — Add the versioned Odoo preference model and administration UI

**Files**

- Extend `odoo/addons/stockai_procurement/` with profile/version and
  ordered-priority models, constraints, access controls, menus, forms, and
  inheritance preview.
- Update the existing StockAI Odoo image digest in Compose and Kubernetes
  overlays through the already-tested four-image release workflow.
- Add Odoo add-on model/view/access tests and a dev Odoo administration smoke
  test.

**Behavior**

- [ ] **Step 1:** Require one active company profile and resolve optional overrides in strict
   product → category → company order for the single-company MVP.
- [ ] **Step 2:** Store immutable versions containing effective dates, required change
   reason, every supported criterion exactly once in a unique order, a 0–100%
   maximum price premium, non-overlapping scope periods, and `advisory` or
   `hard` enforcement.
- [ ] **Step 3:** Give only the Odoo Procurement configuration administrator permission to
   activate versions. Do not grant that role case approval, raw system-prompt
   editing, or PO automation, and keep the seeded configuration administrator
   and Procurement manager as separate identities.
- [ ] **Step 4:** Provide a structured inheritance preview and activate new immutable
   versions without deleting or mutating historical versions.
- [ ] **Step 5:** Seed a reliability-first company profile, delivery-first category
   override, and price-first product override in both fictional environments.

**Verification:** Test scope precedence, inheritance preview, effective-date
and overlap constraints, immutable history, role denial, 0–100% boundaries,
unsupported/duplicate criteria, absence of a prompt editor, seeded profiles,
and immutable StockAI Odoo image build, scan, render, and dev deployment.

**Dependencies:** T27.

**Requirements:** CR-02, CR-04, CR-08, CR-11, CR-13, CR-15; spec sections 6,
8.7, 12, 14.2, 18.2, 20, and 22.

**Complete when:** An authorized administrator can safely manage and audit
typed preference versions in Odoo, unauthorized roles cannot, and all four
required project images follow the tested GitOps promotion contract.

#### T27B — Resolve and enforce preferences through the real MCP boundary

**Files**

- Create `src/procurement/domain/policy/preferences.py`,
  `src/procurement/adapters/odoo/preference_mapper.py`, and
  `src/procurement/mcp_server/tools/preferences.py`.
- Extend the Odoo port, MCP schemas, tool registry, error taxonomy, metrics,
  and fake Odoo gateway.
- Add domain, adapter, isolated MCP-tool, real Streamable HTTP, malformed
  response, timeout, and real-Odoo contract tests.

**Behavior**

- [ ] **Step 1:** Implement `get_procurement_preferences` with company, category, product,
   and as-of inputs and a typed inheritance trace and immutable version output.
- [ ] **Step 2:** Independently validate scope precedence, effective dates, unique supported
   criteria, 0–100% premium, enforcement enum, and authorization metadata.
- [ ] **Step 3:** Calculate premium against the cheapest otherwise-eligible normalized total
   cost with explicit decimal handling; reject any zero/non-positive offer
   that escaped the earlier offer boundary.
- [ ] **Step 4:** Return advisory exceedance as evidence; remove above-cap offers
   deterministically in hard mode before any LLM comparison.
- [ ] **Step 5:** Return a safe preference-configuration error for missing, overlapping,
   expired, malformed, or unauthorized data; never guess a default.

**Verification:** Test every inheritance branch, boundary and decimal case,
advisory/hard behavior, no otherwise-eligible offer, malformed/untrusted Odoo
responses, retry/timeout rules, low-cardinality metrics, and the real seeded
Odoo add-on through Streamable HTTP.

**Dependencies:** T27A.

**Requirements:** CR-02, CR-05, CR-06, CR-12, CR-13, CR-15; spec sections 8.7,
9.2, 11, 12, 19, 20, and 22.

**Complete when:** The real MCP boundary returns one validated effective
profile and deterministic premium result, and invalid configuration cannot
reach LLM reasoning.

#### T27C — Bind preferences to cases, prompting, audit, and read-only UI

**Files**

- Add the graph preference-resolution node, fixed typed preference renderer,
  recommendation schema fields, case evidence/hash fields, API schemas, audit
  events, and `frontend/src/components/AppliedPreferences.tsx`.
- Add graph, prompt-boundary, API, DynamoDB, audit, React, integration,
  observability, and dev Bedrock smoke tests.

**Behavior**

- [ ] **Step 1:** Call the real preference MCP tool before reasoning and route any safe
   configuration error to manual review without creating a draft.
- [ ] **Step 2:** Pass only typed enums, numbers, identifiers, and version metadata to the
   application-owned system-prompt renderer; never interpolate Odoo free text
   or change reasons.
- [ ] **Step 3:** Snapshot profile ID, scope, version, ordered criteria, premium result, and
   enforcement mode into the case and evidence hash.
- [ ] **Step 4:** Retain that snapshot for in-flight manager change requests and reapproval;
   newly activated versions affect only later scans.
- [ ] **Step 5:** Show the applied snapshot and inheritance source read-only in the React
   recommendation view and immutable audit trail.
- [ ] **Step 6:** Emit preference-resolution failure and advisory-premium-exceedance metrics
   without profile IDs or versions as metric labels.

**Verification:** Test malformed profile manual review, injection-like change
reasons, fixed prompt rendering, evidence-hash binding, active-case stability,
read-only UI display, audit order, metric cardinality, and all three seeded
company/category/product scenarios with real Odoo, MCP, and Bedrock in dev.

**Dependencies:** T27B.

**Requirements:** CR-02, CR-03, CR-04, CR-05, CR-06, CR-12, CR-13, CR-15;
   spec sections 8.7, 9, 10.4, 13, 14, 20, 21, and 22.

**Complete when:** Every recommendation uses and displays one immutable
preference snapshot, and neither business text nor configuration can expand
the safe action space.

#### T28 — Complete contextual recommendation reasoning and safe fallback

**Files**

- Add graph nodes for hard policy, evidence gathering, reasoning, output
  validation, manual review, and final audit.
- Finalize `src/procurement/agent/prompts/procurement_system.md` and structured
  schemas.
- Add recommendation rationale/risk/uncertainty UI components and LLM/MCP
  dashboard panels.
- Add unit, integration, and live Bedrock smoke tests.

**Behavior**

- [ ] **Step 1:** Give Bedrock only eligible, bounded, sanitized alternatives, authoritative
   calculations, and the machine-generated validated preference section.
- [ ] **Step 2:** Allow `recommend` or `manual_review`; validate the selected offer and every
   copied number against evidence.
- [ ] **Step 3:** Apply the effective advisory priority order and surface contextual
   cost/delivery/reliability/quality/order/payment/evidence trade-offs without
   fixed-score overclaiming.
- [ ] **Step 4:** On repeated Bedrock failure or invalid output, show deterministic comparison
   and create no draft.
- [ ] **Step 5:** Emit token, latency, retry, invalid-output, and fallback metrics.

**Verification:** Test valid recommendation, manual review, ineligible
identifier, altered arithmetic, omitted warning, applied-profile
acknowledgement, different company/category/product priorities, malicious
business text, timeout/retries, schema repair, fallback, and live
selected-model invocation.

**Dependencies:** T27C.

**Requirements:** CR-02, CR-03, CR-05, CR-12, CR-13, CR-15; spec sections 4.3,
8.7, 9, and 19.

**Complete when:** The LLM contributes real contextual judgment but cannot
expand the eligible set, change facts, or cause a write.

#### T29 — Create idempotent draft POs and pause for human approval

**Files**

- Create `src/procurement/mcp_server/tools/create_draft.py`,
  `src/procurement/mcp_server/idempotency.py`, and graph draft/interrupt nodes.
- Extend checkpoint, case, evidence-hash, revision, audit, API, and React
  recommendation detail behavior.
- Add unit, MCP transport, concurrency, restart/resume, Odoo, API, UI, and dev
  smoke tests.

**Behavior**

- [ ] **Step 1:** Create one draft PO per product using only a validated eligible offer and
   deterministic quantity.
- [ ] **Step 2:** Store case ID in Odoo origin/reference and use DynamoDB conditional
   idempotency records.
- [ ] **Step 3:** On a write timeout, reconcile DynamoDB and Odoo before any retry.
- [ ] **Step 4:** Bind the evidence hash—including the immutable applied-preference
   snapshot—and PO revision, checkpoint, and interrupt without holding an HTTP
   request open.
- [ ] **Step 5:** Enter `RECONCILIATION_REQUIRED` on ambiguous results.

**Verification:** Test repeated requests, concurrent scans, response loss after
Odoo commit, process termination after write, revision changes, checkpoint
resume, draft link display, audit, and metrics.

**Dependencies:** T28.

**Requirements:** CR-02, CR-03, CR-05, CR-06, CR-12, CR-13, CR-15; spec
sections 7, 9, 11, and 19.

**Complete when:** Eligible cases create at most one traceable draft and wait
safely for a human.

#### T30 — Add revision-bound approval, budget exception, and PO confirmation

**Files**

- Create approval domain/service modules,
  `src/procurement/mcp_server/tools/confirm.py`, and
  `src/procurement/api/routes/decisions.py`.
- Add React manager decision and budget-exception components.
- Add approval/confirmation audit, metrics, dashboards, alerts, and unit,
  integration, Odoo, API, UI, and dev smoke tests.

**Behavior**

- [ ] **Step 1:** Allow only authenticated managers to approve the exact current case,
   vendor, quantity, amount, budget state, evidence hash, and PO revision.
- [ ] **Step 2:** Require an explicit exception flag and non-empty bounded justification for
   every over-budget approval.
- [ ] **Step 3:** Have MCP perform a strongly consistent approval read and independent exact
   match before Odoo confirmation.
- [ ] **Step 4:** Call only
   `purchase.order.action_stockai_confirm(expected)` so Odoo locks and compares
   the current revision-critical snapshot before invoking `button_confirm` in
   the same transaction. Reject wrong role, stale/expired/replayed approval,
   changed draft, missing exception, or environment mismatch.
- [ ] **Step 5:** Confirm only a fictional Odoo PO; do not contact a supplier or move money.

**Verification:** Run the happy path and over-budget path end to end, plus
every approval-safety failure, concurrency/replay tests, ambiguous confirm
reconciliation, audit inspection, and safety alert.

**Dependencies:** T29.

**Requirements:** CR-02, CR-05, CR-06, CR-12, CR-13, CR-15; spec sections 6,
7.3, 8.6, and 11.3.

**Complete when:** There is no code path that confirms without a current,
strongly revalidated manager approval.

#### T31 — Add rejection, change requests, cancellation, and reconciliation

**Files**

- Create MCP tools `update_draft.py` and `cancel_draft.py`.
- Add graph nodes for rejection, bounded changes, recomputation, cancellation,
  and reconciliation.
- Complete decision routes and React reject/change/audit timeline screens.
- Add unit, transport, Odoo, API, UI, restart, concurrency, and dev smoke tests.

**Behavior constraint:** Draft changes and cancellation must call only
`purchase.order.action_stockai_update_draft(expected, changes)` and
`purchase.order.action_stockai_cancel_draft(expected)`. The MCP allowlists
change fields, Odoo performs its row-lock/revision check and standard business
action, and ambiguous responses enter reconciliation rather than being blindly
retried.

**Behavior**

- [ ] **Step 1:** Reject with a bounded reason, preserve the decision, and idempotently cancel
   the draft.
- [ ] **Step 2:** Accept only supported structured change fields plus an untrusted bounded
   note.
- [ ] **Step 3:** Invalidate prior recommendation/approval, recompute all policy, safely
   update the same draft, increment revision, retain the case’s snapshotted
   preference version, and require reapproval.
- [ ] **Step 4:** Route unsupported or ambiguous requests to manual review.
- [ ] **Step 5:** Reconcile create/update/cancel/confirm results before any write retry.
- [ ] **Step 6:** Expose a chronological immutable audit timeline.

**Verification:** Test stale change requests, unsafe vendor selection,
quantity/date changes, invalidated approval, update conflict, ambiguous
cancellation, restart during reconcile, audit ordering, and all final states.

**Dependencies:** T30.

**Requirements:** CR-02, CR-05, CR-06, CR-12, CR-13, CR-15; spec sections 7.3,
11.2, 13, and 19.

**Complete when:** All manager actions are safe, revision-aware, recoverable,
and visible in the UI and audit.

### Phase 6 — Security, resilience, acceptance, and presentation

#### T32 — Perform security and secrets hardening

**Files**

- Add security headers, request limits, input constraints, and redaction tests.
- Finalize NetworkPolicies, namespace RBAC, service accounts, pod security
  contexts, External Secrets resources, and IAM policies.
- Create `docs/runbooks/secret-rotation.md`,
  `docs/runbooks/security-incident.md`, and security test cases.

**Work and tests**

- [ ] **Step 1:** Verify default-deny network flows and only documented allow paths.
- [ ] **Step 2:** Verify containers run non-root, drop capabilities, use seccomp, and use
   read-only roots with explicit writable volumes where supported.
- [ ] **Step 3:** Rotate Odoo key, MCP/Cron tokens, session secret, database
   credentials, and Grafana credentials without logging old/new values. For
   the Odoo key, attach the exact-secret bootstrap policy through the protected
   Terraform gate, run and verify rotation, detach it on success or failure,
   and assert the normal worker plan again has no Secrets Manager write.
- [ ] **Step 4:** Test browser security headers, CSRF, session fixation, role escalation,
   preference-configuration authorization, input limits, untrusted MCP output,
   prompt injection-like profile text/business data, and error leakage.
- [ ] **Step 5:** Inspect image/dependency/secret/configuration scans and record accepted
   residual risks.

**Verification:** Run `make security-scan`, authorization suites, NetworkPolicy
connectivity checks, secret rotation in dev, and an IAM least-privilege review.

**Dependencies:** T31.

**Requirements:** CR-09, CR-15; spec sections 11.3, 17.5, and 20.

**Complete when:** Trust boundaries have executable evidence and no critical
unresolved security finding remains.

#### T33 — Validate retries, shutdown, capacity, HPA, recovery, and cost

**Files**

- Finalize lifecycle/retry modules and workload probes/resources.
- Create `tests/resilience/`, `tests/load/`,
  `docs/runbooks/recovery.md`, and `docs/runbooks/active-periods.md`.
- Update alerts and resource dashboards.

**Work and tests**

- [ ] **Step 1:** Verify exact read/write/LLM timeouts and retries, permanent-error
   no-retry behavior, 120-second case limit, and reconciliation before write
   retry.
- [ ] **Step 2:** Send SIGTERM during reads, reasoning, draft creation, human wait, and
   confirmation; verify immediate readiness failure, checkpoint/reconciliation,
   and completion within the 45-second grace period.
- [ ] **Step 3:** Load the seeded scenario, confirm p95 approval-ready time, and separately
   drive frontend, API, and MCP CPU above 50%. Verify each HPA scales from one
   toward its maximum of three and scales down after load. First capture
   pending pods at insufficient one-worker capacity; then change only the dev
   ASG desired-capacity input through a reviewed Terraform apply, verify the
   additional worker joins and pending pods schedule, and restore desired one.
   Record this as manual capacity, never automatic node scaling.
- [ ] **Step 4:** Measure every pod’s CPU/memory/disk use on one `t3.medium` worker; adjust
   hypotheses without removing required services. Verify pending-pod
   visibility and alerts if the normal ASG capacity cannot schedule a requested
   replica.
- [ ] **Step 5:** Exercise inactive operation by applying worker `min = 0` and `desired = 0`,
   then stopping the fixed control plane. Restart the control plane, verify
   finite token rotation, restore worker desired one through Terraform, and
   confirm the warming transition. Do not stop an ASG-managed worker directly.
- [ ] **Step 6:** Exercise prod Odoo/PostgreSQL snapshot recovery, retained EBS
   reattachment after worker replacement, Prometheus history across pod
   restart, Grafana reconstruction from Git after pod deletion, and
   reproducible seed fallback.
- [ ] **Step 7:** Verify public DNS aliases, HTTP-to-HTTPS redirect, ACM hostname validation,
   ALB target health/host routing, and the absence of direct public worker
   application access.
- [ ] **Step 8:** Verify clean, forced, failed, timeout, and duplicate termination-cleanup
   outcomes; lifecycle release remains bounded, non-clean outcomes alert, and
   the stale-node/EBS-detach runbook is executable.
- [ ] **Step 9:** Record normal desired-one cost, temporary test-capacity cost, and retained
   ALB/storage cost; confirm the $70 target/$90 review-ceiling alerts.

**Stop condition**

If a complete environment cannot fit safely below 85% memory, the three
stateless HPAs cannot demonstrate safe scale-up/scale-down under the documented
normal/manual capacity sequence, any worker cannot join with the correct
environment identity, lifecycle cleanup can remain stuck beyond 300 seconds,
retained state cannot reattach to a replacement, or Prometheus exceeds its
5 GiB volume budget, stop stretch work and revise resource values or the
approved architecture before production promotion. Do not add Cluster
Autoscaler or an ASG scaling policy as an unapproved workaround.

**Verification:** Run `make test-resilience`, inspect Kubernetes events and
dashboards, perform the controlled recovery/capacity/termination drills, and
update evidence.

**Dependencies:** T32.

**Requirements:** CR-05, CR-09, CR-12, CR-13, CR-16; spec sections 17, 19, and
23.

**Complete when:** The deployed MVP meets its specified timing, safety,
shutdown, resource, recovery, and cost constraints.

#### T34 — Complete observability and requirements acceptance

**Files**

- Finalize all dashboards and alert rules under the observability paths.
- Complete `tests/acceptance/` and `docs/implementation-status.md`.
- Finalize `docs/runbooks/alerts.md` and create
  `docs/runbooks/demo-health.md`.

**Work and tests**

- [ ] **Step 1:** Verify metrics for requests, errors, latency, LLM/MCP failures/timeouts,
   retries, tokens, scan/case outcomes, approval latency, duplicates, safety
   attempts, preference-resolution failures, advisory-premium exceedances, pod
   restarts, CPU/memory, disk, dependencies, ASG desired/in-service capacity,
   Ready workers, replacement duration, volume attach errors, and worker
   cleanup outcomes.
   Specifically verify requests per minute split by success/error, error rate,
   p50/p95/p99 request latency, and separate LLM input/output token panels.
- [ ] **Step 2:** Verify log fields and redaction from every service, Loki queryability, S3
   objects, and dev 14-day/prod 90-day lifecycle configuration.
- [ ] **Step 3:** Fire every actionable alert safely, including ASG/Ready-node mismatch,
   forced/failed cleanup, Lambda error/lifecycle timeout, ALB target/5xx, and
   EBS/PV failures, and confirm its runbook action.
- [ ] **Step 4:** Run the complete unit, integration, UI, Compose, infrastructure, security,
   resilience, dev smoke, prod smoke, and immutable-release verification.
- [ ] **Step 5:** Walk CR-01 through CR-16 and attach actual evidence; do not mark a
   requirement complete based only on planned files.

**Verification:** Run all Make targets and archive real JUnit, coverage, scan,
smoke, dashboard, Argo, Terraform, and release evidence.

**Dependencies:** T33.

**Requirements:** CR-01 through CR-16.

**Complete when:** Every mandatory requirement has evidence, every alert is
actionable, and no required check is merely asserted.

#### T35 — Prepare and rehearse the final demo and presentation

**Files**

- Create `docs/demo/script.md`, `docs/demo/seed-scenarios.md`,
  `docs/demo/manual-baseline.md`, `docs/demo/presentation-outline.md`,
  `docs/demo/ai-agent-reflection.md`, and `docs/demo/fallback-plan.md`.

**Work and tests**

- [ ] **Step 1:** Time the documented manual replenishment workflow at least three times and
   record the baseline method and result.
- [ ] **Step 2:** Rehearse the happy path, preference-override path, and over-budget
   exception path from clean, environment-specific fictional seed data.
- [ ] **Step 3:** Show Odoo preference administration, applied preference/version, draft and
   confirmation, UI evidence, immutable audit, Grafana metrics/logs/alerts,
   GitHub Actions, Argo CD, and immutable digest promotion.
- [ ] **Step 4:** Fit introduction, value, architecture, agent/MCP, live demo,
   infrastructure/observability/testing, pipeline, and AI-agent reflection into
   15 minutes.
- [ ] **Step 5:** Prepare a truthful failure fallback using pre-recorded screenshots or
   exported evidence; do not use generated video and do not present fallback
   evidence as live.
- [ ] **Step 6:** Perform one cold-start rehearsal after the cluster was intentionally
   stopped and verify warming/scan behavior.

**Verification:** Conduct at least two timed rehearsals, one with an injected
safe failure, and resolve all critical demo blockers.

**Dependencies:** T34.

**Requirements:** CR-02, CR-14; spec section 24.

**Complete when:** The user can explain every major decision and deliver the
full presentation and live interaction within 15 minutes.

## 9. Phase exit gates

| Gate | Required evidence | System condition |
|---|---|---|
| G0 — Planning | User approval, course-staff PR approval, explicit user implementation instruction | Implementation may start |
| G0R — T10 Odoo revision | User review of the exact 2026-08-07 spec/plan, course-staff PR approval, explicit user resume instruction | T10 may resume under the selected add-on and ORM-bootstrap design |
| G1 — Local skeleton | Unit/integration reports and manual browser check | Local API → LangGraph → real MCP transport → result works |
| G2 — Odoo boundary | Executable Odoo contract, repeatable seed, live MCP read | No unresolved Odoo contract assumption |
| G3 — Container | Image builds, Compose E2E, image contract checks | Local system runs from pinned containers |
| G4 — Platform | Terraform/cluster/Kustomize/CI validation plus bounded clean/fail-open node-replacement drills | Reproducible AWS, isolated worker ASGs, and full dev/prod desired state |
| G5 — Dev skeleton | Real Bedrock/Odoo/MCP/DynamoDB/Cognito smoke, retained-volume replacement, and observability evidence | Full walking skeleton healthy and replacement-safe in dev |
| G6 — Prod skeleton | Same-digest proof, Argo health, prod smoke | Promotion workflow proven |
| G7 — Functional MVP | All Phase 5 tasks, including the three small preference tasks T27A–T27C, and safety tests | Preference-aware, approval-gated fictional PO workflow works end to end |
| G8 — Release candidate | Security, resilience, resource, cost, observability, and CR-01–CR-16 evidence | MVP is submission-ready |
| G9 — Presentation | Two timed rehearsals and fallback evidence | Fifteen-minute demo is ready |

No stretch work may begin before G9.

## 10. Requirements-to-task traceability

| Requirement | Primary implementation tasks | Acceptance evidence |
|---|---|---|
| CR-01 Planning gates | Current plan, T34 | Approved spec/plan PRs and explicit implementation instruction |
| CR-02 Business problem/value | T11A–T11B, T25–T27, T27A–T27C, T28–T31, T35 | Timed baseline, preference-aware approval-ready latency, live workflow |
| CR-03 Coded LLM framework | T05, T12, T27C, T28–T31 | LangGraph tests, deployed graph, real model evidence |
| CR-04 HTTP API/UI | T03, T05, T06, T14, T25–T27, T27A–T27C, T28–T31 | API/UI tests, live dashboard, Odoo preference UI |
| CR-05 Reliability contracts | T02–T05, T12, T18B, T27B–T27C, T28–T33 | Errors, preference validation, retries, fallback, lifecycle bounds, reconciliation, shutdown tests |
| CR-06 Real MCP interaction | T04, T07, T11A–T11B, T25–T27, T27A–T27C, T28–T31 | Streamable HTTP tests and demo traces |
| CR-07 Self-managed EC2 Kubernetes | T16, T18A–T18C | Terraform state, ASG/node inventory, finite join, controlled replacement, no EKS |
| CR-08 Complete dev/prod | T17, T19A–T24 | Separate full-stack overlays, namespaces, Argo apps, smoke |
| CR-09 Workload quality | T18A–T20B, T32, T33 | Probes, resources, HPA, retained CSI volumes, secrets, graceful shutdown/drain evidence |
| CR-10 Terraform | T15–T18B | Validated/applied ASG, lifecycle, storage, edge, service state and reproducible runbooks |
| CR-11 CI/CD/GitOps | T21–T24, T27A | Four-image PR/dev/main flows, Argo reconciliation, digest identity |
| CR-12 Observability | T03–T05, T18B, T20A–T20B, T25–T27, T27A–T27C, T28–T34 | Application/ASG/cleanup metrics, logs, S3 objects, dashboards, fired alerts |
| CR-13 Automated testing | Every behavior task; T34 audit | Unit/integration/UI/smoke/JUnit/coverage evidence |
| CR-14 Presentation | T06, T23, T30, T34, T35 | Timed live demo, dashboard, pipeline, reflection |
| CR-15 Security | T02–T04, T11A–T11B, T12–T24, T25–T27, T27A–T27C, T28–T33 | IAM/RBAC/CSRF/idempotency/redaction/network/preference/approval tests |
| CR-16 Decision/AWS justification | T15–T18B, T23, T33–T35 | Plans, lifecycle/cost evidence, implementation status, explanation |

## 11. Test coverage map

| Behavior | Unit | Integration | Deployed smoke or acceptance |
|---|---|---|---|
| Forecast/trigger/need-by | T25 | T25 real MCP transport | T25 real Odoo |
| Duplicate/full/partial coverage | T25 | T25 concurrency | T25 seeded open PO |
| Offer eligibility/quantity | T26 | T26 MCP + Odoo adapter | T26 vendor comparison |
| Performance evidence | T26 | T26 MCP + Odoo adapter | T26 seeded receipts/returns |
| Budget and overage | T11A model/ACL; T27 policy/UI | T11A Odoo contract; T27 MCP + Odoo adapter | T27/T30 exception path |
| Preference versioning and Odoo authorization | T27A | T27A add-on/container/release tests | T27A Odoo administration smoke |
| Preference resolution and premium | T27B | T27B real MCP + Odoo add-on | T27B company/category/product scenarios |
| Preference case/prompt/UI binding | T27C | T27C graph/API/React tests | T27C real Bedrock and read-only case view |
| LLM recommendation/fallback | T12/T28 mocked | T28 graph + MCP | T28 real Bedrock |
| Draft/idempotency | T29 | T29 concurrency/ambiguous result | T29 real Odoo |
| Approval/confirmation | T11A atomic Odoo contract; T30 approval/MCP integration | T11A/T30 stale/replay/role/exception | T30 real Odoo |
| Reject/change/reconcile | T31 | T31 failures/restarts | T31 real Odoo |
| API/auth/CSRF | T03/T05/T14/T25–T27/T27C/T28–T31 | T14/T25–T27/T27C/T28–T31 | T23/T24/T27C/T30 |
| React states/actions | T06/T14/T25–T27/T27C/T28–T31 | Local browser E2E | Dev/prod browser smoke |
| MCP tools | T04/T11B/T25–T27/T27B/T28–T31 | All eleven over Streamable HTTP | Real Odoo demo traces |
| AWS repositories | T12–T14 mocked | DynamoDB Local | Dev/prod AWS smoke |
| Worker bootstrap and termination | T18A/T18B mocks | Terraform/event/IAM/SSM integration checks | Clean and fail-open dev replacement drills |
| Kubernetes/config, ingress, and storage | T17–T20B static | Terraform/render/policy/resource tests | ASG/ACM/ALB/NGINX health, six EBS bindings/reattachment, Grafana reconstruction, Argo recovery |
| Security/shutdown/load | T32/T33 | Fault injection | Dev drills and prod-safe checks |

## 12. Risk-driven stop conditions

Implementation stops for review when any of these occurs:

- Odoo 19 Community, JSON-2, or the narrowly approved StockAI add-on cannot
  provide a required standard or extension contract.
- The StockAI Odoo add-on cannot enforce budget uniqueness, atomic
  revision-bound PO actions, immutable preference versions, non-overlapping
  effective profiles, or least-privilege administration without materially
  broader authority than approved.
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
- A PO can be confirmed without a strongly consistent, exact,
  revision-bound manager approval.
- Dev and prod cannot use the identical immutable image digests.
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
7. **S07 — Real node autoscaling.** If Phase 1 and G9 are complete with time
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
- All eleven MCP tools and their real transport are covered.
- Preference administration is structured and versioned, never a raw prompt
  editor, and product/category/company precedence is unambiguous.
- Preference configuration and case approval are separately authorized, with
  read-only applied preference evidence visible to officers and managers.
- Odoo feasibility is tested before broad dependence on its data model.
- Every PO remains manager-approved and revision-bound.
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
- The exact dev-tested image digests are promoted to prod.
- Tests cover happy paths, failures, malformed outputs, timeouts, retries,
  fallbacks, concurrency, and ambiguous writes.
- Logs reach Loki and encrypted S3 without leaking sensitive procurement data.
- Dashboards and alerts cover application, LLM, MCP, Kubernetes, dependencies,
  ALB/HTTPS edge health, exact request/latency/error/token panels, and
  procurement safety.
- Cost, disk, memory, non-24/7 operation, and recovery limitations are tested
  and presented honestly.
- Stretch integrations remain blocked until the submission-ready MVP is
  complete.

## 15. Next approval gate

The original planning gate and subsequent approved revisions authorized T01
through T09, which are complete. T10 triggered its approved stop condition.
The user approved the exact remediation revision, confirmed course-staff
approval, and explicitly authorized T10 implementation to resume on 2026-08-07.
T10 through T14 are approved and merged; T14 merged through PR #18 at
`fd6ba1d`. The user explicitly authorized T15 on 2026-08-09, and T15 was
reviewed and merged through PR #19 at `f89a089`. The user explicitly authorized
T16 on 2026-08-11, and T16 was reviewed and merged through PR #20 at
`debbb5f`. The user explicitly authorized T17 on 2026-08-11; its offline
Terraform plan contracts and provider-schema validation are complete and await
user review. Remote-backend initialization with account-specific inputs, the
reviewed real plans, apply, quota/cost checks, and post-apply AWS verification
retain the separate explicit infrastructure approval gate.
