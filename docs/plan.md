# AI Procurement Agent — Implementation Plan

**Status:** Draft for user review

**Date:** 2026-07-25

**Source design:** `docs/spec.md`

**Current gate:** Planning only; implementation is not authorized

## 1. Approval status and purpose

User and course-staff approval of `docs/spec.md` were confirmed by the user
on 2026-07-25. That approval authorized creation of this implementation plan.

This plan does not authorize application code, tests, containers, Terraform,
Kubernetes manifests, or CI/CD work. Before Task T01 begins:

1. The user must review and explicitly approve this plan.
2. Course staff must approve this plan through a pull request.
3. The user must then separately and explicitly authorize implementation.

If an implementation discovery conflicts with the approved specification, work
must stop, the affected design and plan sections must be revised, and the
required approval must be obtained before work resumes.

## 2. Planning approach

The dedicated `writing-plans` skill was not available in this workspace. This
document uses the required equivalent workflow:

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

## 3. Fixed implementation constraints

The following decisions come from the approved specification and must not be
silently changed:

- Python, FastAPI, and LangGraph implement the API and agent.
- A custom Python Procurement MCP server uses Streamable HTTP.
- Amazon Bedrock model `openai.gpt-oss-20b-1:0` is the only LLM.
- React, TypeScript, and Vite build a separate frontend served by NGINX.
- Odoo 19 Community and PostgreSQL are separate per environment.
- DynamoDB stores checkpoints and application state; S3 stores Loki objects.
- Cognito provides identity; backend-managed opaque sessions protect the UI.
- AWS resources are provisioned with Terraform in `us-east-1`.
- Kubernetes is self-managed with kubeadm on three `t3.medium` EC2 instances,
  each with no more than 30 GB root EBS.
- Dev and prod run complete, separate stacks on hard-pinned worker nodes.
- Kustomize defines application desired state.
- GitHub Actions builds and validates; Argo CD deploys. Actions never deploy
  workloads with `kubectl`.
- Every purchase order requires a revision-bound manager approval.
- Supplier contact, payment, and real legal ordering remain outside the MVP.

The implementation may choose exact dependency patch versions only after
compatibility checks, but it must pin them in lock files and immutable image
digests. The intended baseline is Python 3.12, a current supported Node LTS,
`uv` for Python locking, and `npm` lock files for the frontend. A compatibility
failure is resolved in the plan or specification rather than by an unrecorded
toolchain change.

## 4. Planned repository layout

The implementation will use one Python distribution with two independently
started services. This shares typed domain contracts without combining the API
and MCP security boundaries.

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
│   └── terraform/
│       ├── bootstrap/
│       ├── modules/
│       ├── platform/
│       └── environments/
│           ├── dev/
│           └── prod/
├── scripts/
├── src/
│   └── procurement/
│       ├── adapters/
│       │   ├── aws/
│       │   └── odoo/
│       ├── agent/
│       ├── api/
│       ├── bootstrap/
│       ├── domain/
│       ├── mcp_server/
│       ├── observability/
│       └── ports/
├── tests/
│   ├── integration/
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
  MCP, Odoo, AWS, or LangGraph imports.
- `procurement.ports` defines interfaces used by the agent.
- `procurement.agent` depends on domain types and ports, never on the MCP
  server implementation or Odoo adapter.
- `procurement.mcp_server` owns domain tool schemas, authorization, validation,
  Odoo access, write idempotency, and independent approval verification.
- `procurement.api` owns HTTP, sessions, CSRF, RBAC, and graph orchestration.
- `frontend` uses only the versioned API and never receives AWS, Odoo, or
  Cognito tokens.

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
| `make build` | Frontend build and three OCI image builds |
| `make compose-validate` | Render Compose configuration and verify service health |
| `make terraform-validate` | Format, initialize without apply, validate, lint, and run static checks for every root |
| `make kubernetes-validate` | Render both Kustomize overlays and run schema/policy checks |
| `make security-scan` | Dependency, secret, filesystem, configuration, and image checks; Docker Scout remains required in CI |
| `make smoke-dev` | Public HTTPS, auth, real Bedrock, real MCP, real Odoo, DynamoDB, audit, metrics, and logs |
| `make smoke-prod` | Same critical path against prod with prod-only fictional seed data |
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

1. Add an initially failing import-boundary test.
2. Configure pinned runtime and development dependencies for FastAPI,
   LangGraph, the Python MCP SDK, Pydantic, boto3, HTTP clients, pytest,
   Ruff, and mypy.
3. Define stable Make targets without adding application behavior.
4. Make the boundary test pass with the empty deep-module structure.

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

1. Test bounded identifiers, amounts, currencies, dates, quantities, evidence
   references, revisions, and case-state transitions.
2. Implement the states in specification section 7.2 without business policy.
3. Define the stable error envelope and retryability classification.
4. Reject unknown states, invalid transitions, unbounded text, negative money,
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

1. Test `/health/live`, `/health/ready`, `/health/dependencies`, and `/metrics`.
2. Test the safe error envelope and correlation-ID propagation.
3. Test JSON log fields and redaction of secrets, prompts, model output,
   prices, budgets, manager notes, and upstream errors.
4. Implement process liveness separately from dependency readiness.
5. Expose request count, error count, and latency without high-cardinality
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

1. Test `list_replenishment_candidates` in isolation with strict inputs and
   bounded typed outputs.
2. Test missing/wrong bearer credentials, malformed requests, response schema
   validation, timeout mapping, and safe errors.
3. Start the actual MCP server and call it through the Python MCP client using
   Streamable HTTP; no direct function-call substitute is accepted.
4. Add MCP call count, duration, failures, timeouts, and retries to metrics and
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

**Work and tests**

1. Test a coded LangGraph that calls MCP, invokes a fake structured LLM port,
   and returns one approval-ready read-only result.
2. Test one MCP timeout path that produces a safe unresolved result.
3. Implement `POST /api/v1/scans` as `202 Accepted`, plus scan list/detail
   polling endpoints.
4. Implement `POST /internal/v1/scans` with a separate narrow Cron credential;
   do not reuse a human session.
5. Enforce one local scan lock and a 120-second non-human workflow deadline.
6. Add scan, LLM, MCP, retry, and result metrics.

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

1. Test loading, empty, success, manual-review, and safe-error states.
2. Implement a manual scan button, 202 handling, bounded polling with cleanup,
   and scan result display.
3. Avoid embedding configuration or tokens in the browser bundle.
4. Meet basic keyboard, label, focus, and contrast checks.

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
- Update `README.md` and `docs/implementation-status.md`.

**Work and tests**

1. Test the full local happy path across actual API and MCP processes.
2. Test a representative MCP timeout, including retry count, final error,
   logs, and metrics.
3. Verify the interaction contains a LangGraph run and a real MCP transport
   call.
4. Document one command to run and one command to verify the skeleton.

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
- Create `tests/config/test_container_contracts.py`.

**Work and tests**

1. Add configuration tests for non-root execution, fixed entry points, health
   checks, no development server, minimal build context, and no copied secret.
2. Use multi-stage builds and pinned base-image digests.
3. Ensure the frontend proxies `/api` and `/auth` to FastAPI on the same origin.
4. Define writable paths explicitly so later read-only root filesystems work.

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

1. Run frontend, API, MCP, and deterministic fake Odoo as separate services.
2. Add explicit networks, health checks, bounded resources, and disposable test
   volumes.
3. Test happy path, no-valid-response failure, malformed fake Odoo response,
   and service timeout.
4. Keep credentials fictional and injected from ignored local environment
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
  `tests/contract/test_odoo_json2.py`, and `docs/odoo-contract.md`.

**Work and tests**

1. Start pinned Odoo 19 Community and PostgreSQL images locally.
2. Probe authentication, the required Purchase/Inventory/Contacts/accounting
   models, reordering rules, vendor pricelists, receipts, returns, analytic
   budgets, PO origin/reference, revision detection, and PO state actions.
3. Record exact model names, fields, methods, permissions, and representative
   sanitized payloads.
4. Verify whether an integration API key and fictional identities can be
   bootstrapped idempotently without manual production-console resource
   creation.
5. Convert every verified contract into an executable contract test.

**Stop condition**

If Odoo 19 Community lacks an approved-spec capability, or JSON-2 cannot safely
perform the required operation, do not create an invented substitute. Stop,
document evidence, revise `docs/spec.md` and this plan, and obtain the required
approval.

**Verification:** Run the contract suite against the pinned local Odoo image
from a clean database.

**Dependencies:** T09.

**Requirements:** CR-02, CR-06, CR-13, CR-15; spec section 12.

**Complete when:** Every Odoo claim needed by the MVP has a passing executable
contract or has triggered the stop condition.

#### T11 — Implement idempotent Odoo bootstrap, seed data, and the JSON-2 adapter

**Files**

- Create `src/procurement/adapters/odoo/client.py`,
  `src/procurement/adapters/odoo/mappers.py`,
  `src/procurement/bootstrap/odoo.py`,
  `scripts/odoo/seed.py`, and `scripts/odoo/verify_seed.py`.
- Create `tests/unit/adapters/odoo/test_client.py`,
  `tests/unit/adapters/odoo/test_mappers.py`,
  `tests/integration/test_odoo_bootstrap.py`, and
  `tests/integration/test_mcp_real_odoo.py`.

**Work and tests**

1. Test JSON-2 authentication, 10-second read timeout, transient retry, no
   retry on permanent errors, strict mapping, and untrusted-output rejection.
2. Implement idempotent fictional dev/prod seed profiles for the happy path,
   over-budget path, no-valid-offer path, history, returns, and open-PO
   coverage.
3. Bootstrap the least-privilege integration user and rotating key without
   logging credentials; remove temporary bootstrap authority after success.
4. Replace the fixture implementation of
   `list_replenishment_candidates` with the real adapter while retaining the
   fake for deterministic tests.

**Verification:** Re-run bootstrap twice, compare resulting records, then call
the MCP candidate tool over Streamable HTTP against real Odoo.

**Dependencies:** T10.

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

**Work and tests**

1. Test that only `openai.gpt-oss-20b-1:0` can be invoked.
2. Test the 30-second attempt timeout, at most two transient retries with
   exponential backoff/jitter, one schema-repair attempt, and final safe
   fallback.
3. Test ineligible identifiers, changed arithmetic, missing budget
   acknowledgement, oversized text, injection-like business data, and token
   metric extraction.
4. Implement the system-prompt sections defined in specification 9.4 without
   requesting or exposing hidden chain-of-thought.

**Verification:** Run all tests with a mocked boto3 Bedrock client. A real model
call is deferred to the dev smoke test.

**Dependencies:** T11.

**Requirements:** CR-03, CR-05, CR-12, CR-13, CR-15; spec sections 9 and 19.

**Complete when:** Model responses are advisory, schema-bound, observable, and
incapable of authorizing writes or altering deterministic values.

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
- Add a DynamoDB Local test profile to `compose.test.yaml`.

**Work and tests**

1. Test environment-prefixed keys, conditional case creation, idempotency,
   optimistic revisions, strongly consistent approval reads, audit
   immutability, TTL fields, and pagination.
2. Implement separate checkpoint and application repositories behind ports.
3. Persist sanitized graph state without duplicating Odoo master data.
4. Verify graph resume after API process restart using DynamoDB Local.

**Verification:** Run mocked unit tests and the real DynamoDB Local integration
test.

**Dependencies:** T12.

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
- Update `frontend/src/api/client.ts` and add
  `frontend/src/pages/SignInPage.tsx`.

**Work and tests**

1. Test authorization-code state/nonce validation, callback errors, secure
   cookies, session rotation/expiry/logout, CSRF, disabled self-signup
   assumptions, and officer/manager roles.
2. Store only opaque browser cookies; store session records in DynamoDB.
3. Keep a test-only local identity adapter that cannot be enabled in dev or
   prod configuration.
4. Protect manual scan and dependency-health endpoints and add
   `/api/v1/session`.
5. Add an idempotent bootstrap command for fictional officer and manager users
   and groups without emitting temporary credentials.

**Verification:** Run API and frontend auth tests and inspect the production
bundle for tokens or secret configuration.

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

1. Validate encrypted versioned state storage, public-access blocking,
   locking, retention protection, and narrowly scoped GitHub OIDC trust.
2. Keep bootstrap state separate from application log storage.
3. Parameterize account, repository, administrator CIDR, and state names;
   never commit account-specific values.
4. Document the reproducible CLI bootstrap without AWS Console creation.

**Verification:** Run format, init, validate, static checks, and a reviewed plan
before any apply. After authorized apply, verify encryption, versioning,
locking, and OIDC claims.

**Dependencies:** T14 and explicit infrastructure apply approval when the task
is executed.

**Requirements:** CR-10, CR-11, CR-15, CR-16; spec sections 16 and 18.

**Complete when:** All later Terraform roots can use protected remote state and
keyless GitHub authentication.

#### T16 — Provision network, EC2, EBS, security groups, and node IAM

**Files**

- Create modules under `infra/terraform/modules/network/`,
  `infra/terraform/modules/compute/`, and
  `infra/terraform/modules/node-iam/`.
- Create the `infra/terraform/platform/` root and
  `tests/infra/test_platform_plan.py`.

**Work and tests**

1. Test one VPC, two public subnets, routing, Internet Gateway, stable
   addresses, and exactly three `t3.medium` instances with root volumes no
   larger than 30 GB.
2. Test one control-plane role without application-data permissions and
   separate, least-privilege dev/prod worker roles.
3. Test security groups: restricted SSH/control-plane access, public HTTPS
   only, and no public MCP or database ports.
4. Add explicit labels/tags used by cluster bootstrap, local PVs, DLM, cost
   reporting, and environment scheduling.

**Verification:** Run Terraform checks and inspect the reviewed plan for count,
instance type, volume size, ingress, IAM actions, and monthly-cost assumptions.

**Dependencies:** T15.

**Requirements:** CR-07, CR-10, CR-15, CR-16; spec sections 16.1, 17.1, and 23.

**Complete when:** Terraform reproducibly creates the constrained three-node
foundation without EKS, NAT Gateway, or managed load balancer.

#### T17 — Provision environment AWS services, DNS, recovery, and budgets

**Files**

- Create `infra/terraform/modules/app-environment/`,
  `infra/terraform/environments/dev/`, and
  `infra/terraform/environments/prod/`.
- Create `tests/infra/test_environment_plans.py` and
  `docs/runbooks/cost-and-shutdown.md`.

**Work and tests**

1. Test separate DynamoDB checkpoint/application tables, PITR/TTL/retention,
   Secrets Manager entries, Cognito pools/clients/groups, and IAM resource
   scopes for each environment.
2. Test encrypted S3 Loki prefixes/lifecycle, no public access, and separation
   from Terraform state.
3. Reference the existing Route 53 zone and create only approved dev/prod
   records without managing or destroying domain registration.
4. Add prod DLM snapshots with seven daily recovery points.
5. Add AWS Budget target/ceiling notifications at $50/$75. Treat email
   confirmation as an external human verification, not a console-created
   resource.
6. Scope Bedrock permission to the selected model only.

**Verification:** Run plans for dev and prod and assert isolation, encryption,
retention, model ARN, DNS names, and absence of excluded AWS services.

**Dependencies:** T16.

**Requirements:** CR-08, CR-10, CR-15, CR-16; spec sections 15, 16, and 23.

**Complete when:** Every selected AWS application service is reproducible,
environment-scoped, and justified by the specification.

#### T18A — Automate the kubeadm node and cluster bootstrap

**Files**

- Create `infra/cluster/install-node.sh`,
  `infra/cluster/init-control-plane.sh`,
  `infra/cluster/join-worker.sh`,
  `infra/cluster/bootstrap-cluster.sh`, and
  `docs/runbooks/cluster-bootstrap.md`.
- Create the pinned CNI resources under
  `deploy/kubernetes/cluster/network/`.
- Create `tests/infra/test_cluster_bootstrap.py`.

**Work and tests**

1. Pin Kubernetes, containerd, and CNI versions.
2. Make bootstrap idempotent and non-interactive after Terraform outputs are
   supplied; do not use the AWS Console.
3. Restrict kubeconfig, SSH, API server, and join-token handling.
4. Label and taint dev/prod workers; keep business workloads off the control
   plane.
5. Install the NetworkPolicy-capable CNI and verify all three nodes become
   ready.
6. Verify cluster stop/start and document the warming transition.

**Verification:** Run shell lint and CNI manifest checks, bootstrap the
authorized test cluster, inspect node roles/labels/taints, and run node/network
and restart checks.

**Dependencies:** T17.

**Requirements:** CR-07, CR-08, CR-09, CR-10, CR-15; spec section 17.

**Complete when:** The self-managed cluster is reproducible and each worker is
hard-bound to its environment.

#### T18B — Install and validate shared cluster controllers

**Files**

- Create pinned resources under `deploy/kubernetes/cluster/` for NGINX Ingress,
  cert-manager, metrics-server, kube-state-metrics, Argo CD, cluster RBAC, and
  namespace definitions.
- Create `tests/kubernetes/test_cluster_resources.py`.

**Work and tests**

1. Schedule only lightweight shared controllers on the control plane and
   retain the business-workload taint.
2. Configure ingress, HTTP-01 certificate issuance, metrics APIs, and Argo CD
   without granting application data permissions to the control-plane role.
3. Apply narrow cluster RBAC and pin every upstream controller image.
4. Verify controller health, certificate test issuance, metrics visibility,
   and the absence of business pods on the control plane.

**Verification:** Render and validate all controller resources, install them on
the authorized cluster, and run controller health/RBAC tests.

**Dependencies:** T18A.

**Requirements:** CR-07, CR-08, CR-09, CR-11, CR-15; spec sections 17.2 and
17.8.

**Complete when:** Shared controllers are healthy and ready for environment
desired state without possessing application secrets.

#### T19A — Define environment configuration, secrets, storage, and isolation

**Files**

- Create shared namespaces, ConfigMaps, service accounts, ExternalSecret
  contracts, static local PV/PVC templates, and default-deny NetworkPolicies
  under `deploy/kubernetes/base/`.
- Create initial `deploy/kubernetes/overlays/dev/` and
  `deploy/kubernetes/overlays/prod/`.
- Create `tests/kubernetes/test_environment_foundations.py`.

**Work and tests**

1. Render two namespaces with different hosts, non-secret configuration, seed
   profile, secret references, storage paths, and hard node placement.
2. Configure static local PVs with `Retain` and environment worker affinity.
3. Materialize only namespace-scoped Secrets Manager values; never render
   plaintext secret values.
4. Establish default-deny ingress/egress before adding documented workload
   flows.
5. Calculate reserved storage and initial namespace resource budgets against
   one `t3.medium`/30 GB worker.

**Verification:** Render both foundations, run schema/policy tests, assert no
plaintext secrets or cross-environment references, and inspect PV affinity.

**Dependencies:** T18B.

**Requirements:** CR-08, CR-09, CR-15; spec sections 15, 17.1, 17.5, and 17.7.

**Complete when:** Dev and prod have isolated, schedulable foundations but no
application workload has been deployed yet.

#### T19B — Define the complete non-observability application workloads

**Files**

- Create shared workloads under `deploy/kubernetes/base/` for frontend, API,
  MCP, Odoo, PostgreSQL, Odoo bootstrap Job, daily scan CronJob, Services,
  probes, resources, ingress routes, and documented NetworkPolicy allows.
- Complete application patches in both environment overlays.
- Create `tests/kubernetes/test_application_overlays.py`.

**Work and tests**

1. Add one Odoo/PostgreSQL pair per environment and the idempotent bootstrap
   Job.
2. Add the daily `concurrencyPolicy: Forbid` CronJob with its private
   credential and source-restricted internal route.
3. Add liveness/readiness/startup behavior, initial measured hypotheses for
   requests/limits, termination grace, rolling updates for stateless services,
   and single replicas for specified stateful services.
4. Add FastAPI HPA min 1/max 2 only.
5. Expose only frontend/API and Odoo UI at this stage; Grafana is wired
   in T20A. Keep MCP, PostgreSQL, metrics, and internal dependencies private.
6. Assert hard scheduling to the matching worker for every business workload.

**Verification:** Render both overlays, run schema/policy tests, assert no
plaintext secrets, and calculate total requests against one `t3.medium`
worker.

**Dependencies:** T19A.

**Requirements:** CR-08, CR-09, CR-15; spec sections 12, 13, 14, and 17.

**Complete when:** Both overlays contain the complete non-observability
application stack with separate configuration and safe placement.

#### T20A — Add environment-scoped metrics and S3-backed log collection

**Files**

- Create observability resources under
  `deploy/kubernetes/base/observability/`.
- Create environment ConfigMaps under
  `deploy/kubernetes/overlays/dev/observability/` and
  `deploy/kubernetes/overlays/prod/observability/`.
- Create `tests/kubernetes/test_observability_collectors.py`.

**Work and tests**

1. Deploy Prometheus, Grafana, Loki, Alertmanager, and namespace-filtered Fluent
   Bit separately for dev and prod.
2. Configure Loki to write retained objects to only its environment’s S3
   prefix, with bounded WAL/cache and no sensitive audit data.
3. Run any External Secrets controller that needs node-role credentials in a
   namespace-scoped, controller-class-limited mode on the matching environment
   worker; do not give the control-plane role application-secret access.
4. Keep Prometheus/Loki retention and cardinality within the worker budget.
5. Expose Grafana through the approved authenticated HTTPS hostname while
   keeping Prometheus, Loki, and Alertmanager private.

**Verification:** Render resource totals, confirm environment isolation, scrape
one application metric, and send a sanitized test log through Fluent Bit →
Loki → S3.

**Dependencies:** T19B.

**Requirements:** CR-08, CR-09, CR-12, CR-15, CR-16; spec section 21.

**Complete when:** Both full stacks have queryable metrics and S3-backed logs
without CloudWatch application logs.

#### T20B — Provision baseline dashboards and actionable internal alerts

**Files**

- Create dashboards under
  `deploy/kubernetes/base/observability/dashboards/`.
- Create alert rules under
  `deploy/kubernetes/base/observability/rules/`.
- Create `tests/kubernetes/test_observability_content.py` and
  `docs/runbooks/alerts.md`.

**Work and tests**

1. Provision agent-health, LLM/MCP, Kubernetes, and dependency dashboards with
   low-cardinality queries.
2. Provision initial pod failure, disk pressure, certificate expiry,
   dependency failure, and Odoo-key-expiry alerts.
3. Give every alert an owner-facing description, severity, evidence link, and
   concrete runbook action.
4. Keep delivery internal to Grafana/Alertmanager for the MVP.

**Verification:** Validate dashboard and rule syntax, load every dashboard,
fire one safe test alert from each alert category, and follow its runbook.

**Dependencies:** T20A.

**Requirements:** CR-12, CR-15; spec sections 21.4–21.6.

**Complete when:** The baseline platform is observable before the first cloud
walking-skeleton deployment.

#### T21 — Implement deterministic CI checks and immutable release metadata

**Files**

- Create `.github/workflows/pr-checks.yml`,
  `.github/workflows/terraform-plan.yml`,
  `.github/workflows/terraform-apply.yml`,
  `scripts/release/create_manifest.py`,
  `scripts/release/verify_manifest.py`, and
  `tests/unit/release/test_manifest.py`.
- Create a schema under `deploy/releases/schema.json`.

**Work and tests**

1. Test release metadata that binds source commit/tree, all three image
   digests, build provenance, Scout result, dev validation status, and creation
   time.
2. Run Python and React tests with JUnit/coverage summaries, builds, Compose
   validation, Terraform checks/plans, Kustomize/schema checks, secret scans,
   and action lint on every pull request.
3. Run Docker Scout on pull requests targeting `main`.
4. Authenticate AWS plan jobs through read-only GitHub OIDC.
5. Make path-filtered Terraform applies use protected GitHub environments and
   apply roles; never auto-apply an unreviewed plan.
6. Retain reports as artifacts and make each failed stage clear in the job
   summary.

**Verification:** Exercise the workflows on a test pull request, including one
deliberate failing check, and unit-test manifest tampering.

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

1. On relevant `dev` pushes, build only changed project images, publish
   immutable Docker Hub digests, create provenance, run Docker Scout, and
   update the dev overlay and release manifest.
2. Prevent workflow loops on bot-only desired-state commits.
3. Configure dev Argo CD to track the `dev` revision and dev overlay.
4. Query Argo CD through its authenticated API for sync/health status; do not
   use `kubectl` in GitHub Actions.
5. Define how the generated release manifest is copied or cherry-picked back
   to the originating feature branch before its main pull request.

**Verification:** Run a no-change path, one-image path, three-image path,
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

1. Apply approved Terraform and bootstrap the cluster through reproducible CLI
   automation.
2. Reconcile the complete dev stack through Argo CD.
3. Seed fictional dev Odoo and bootstrap fictional Cognito users.
4. Exercise real Cognito login, real Bedrock GPT-OSS, real MCP transport, real
   Odoo candidate read, DynamoDB persistence, frontend polling, metrics, logs,
   and S3 Loki objects.
5. Record image digests, Argo status, smoke evidence, resource use, and cost
   observations in the release manifest.

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

1. Make the merge to protected `main` the explicit production decision.
2. Verify the release manifest and promote the exact dev-tested digests without
   rebuilding.
3. Update prod desired state in Git and let prod Argo CD reconcile `main`.
4. Use separate prod Cognito, tables, secrets, Odoo/PostgreSQL, seed,
   observability, hostnames, local PVs, and worker placement.
5. Query Argo CD through its API and run public prod smoke tests without
   `kubectl` in Actions.
6. Document rollback as a Git revert to a previously verified release manifest.

**Verification:** Prove digest identity across dev and prod, prod namespace
isolation, prod smoke success, and rollback of a deliberately bad health-check
configuration in a controlled exercise.

**Dependencies:** T23.

**Requirements:** CR-08, CR-10, CR-11, CR-12, CR-15; spec section 18.

**Complete when:** The minimal system is healthy in both namespaces and the
required promotion path has been exercised end to end.

### Phase 5 — Remaining procurement vertical slices

Each task in this phase updates domain code, MCP, graph, API, UI, tests,
documentation, logs, metrics, and dashboard panels together. Each task is
validated in dev and promoted as the same immutable artifact before the next
dependent task begins.

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

1. Implement 14-day projection from known stock movements only.
2. Distinguish reorder trigger date from need-by/stockout date.
3. Check pending cases, drafts, and confirmed incoming POs.
4. Handle full coverage, partial coverage, residual quantities, pagination,
   a 50-candidate limit, and at most three concurrent product workflows.
5. Audit skipped and duplicate-blocked cases.

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

1. Enforce approved/unblocked vendor tags, offer validity, required price and
   currency, lead time, and delivery by need-by date.
2. Calculate quantity separately per offer using arrival projection, reorder
   maximum, MOQ, and packaging/UoM rounding.
3. Return normalized current order cost, projected inventory, and excess
   inventory without claiming landed cost.
4. Compute 365-day on-time rate, average positive lateness, return proxy,
   evidence counts, and insufficient-history status below three orders.
5. Display rejected offers with safe deterministic reasons.

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

1. Map product category to the approved analytic account and monthly period.
2. Calculate budget, current confirmed commitments, remaining before/after,
   and exact overage in authoritative code.
3. Keep an over-budget offer eligible but mark it as requiring explicit
   manager exception and justification.
4. Reject malformed, mismatched-period, or mismatched-currency budget data.

**Verification:** Test month boundaries, no budget record, exact budget,
overage, currency errors, UI warning prominence, sanitized logs, and the real
Odoo budget scenario.

**Dependencies:** T26.

**Requirements:** CR-02, CR-06, CR-12, CR-13, CR-15; spec sections 8.6 and 14.

**Complete when:** Every proposed amount has an authoritative budget result and
an overage cannot be visually or structurally hidden.

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

1. Give Bedrock only eligible, bounded, sanitized alternatives and
   authoritative calculations.
2. Allow `recommend` or `manual_review`; validate the selected offer and every
   copied number against evidence.
3. Surface contextual cost/delivery/reliability/quality/order/payment/evidence
   trade-offs without fixed-score overclaiming.
4. On repeated Bedrock failure or invalid output, show deterministic comparison
   and create no draft.
5. Emit token, latency, retry, invalid-output, and fallback metrics.

**Verification:** Test valid recommendation, manual review, ineligible
identifier, altered arithmetic, omitted warning, malicious business text,
timeout/retries, schema repair, fallback, and live selected-model invocation.

**Dependencies:** T27.

**Requirements:** CR-02, CR-03, CR-05, CR-12, CR-13, CR-15; spec sections 4.3,
9, and 19.

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

1. Create one draft PO per product using only a validated eligible offer and
   deterministic quantity.
2. Store case ID in Odoo origin/reference and use DynamoDB conditional
   idempotency records.
3. On a write timeout, reconcile DynamoDB and Odoo before any retry.
4. Bind the evidence hash and PO revision, checkpoint, and interrupt without
   holding an HTTP request open.
5. Enter `RECONCILIATION_REQUIRED` on ambiguous results.

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

1. Allow only authenticated managers to approve the exact current case,
   vendor, quantity, amount, budget state, evidence hash, and PO revision.
2. Require an explicit exception flag and non-empty bounded justification for
   every over-budget approval.
3. Have MCP perform a strongly consistent approval read and independent exact
   match before Odoo confirmation.
4. Reject wrong role, stale/expired/replayed approval, changed draft, missing
   exception, or environment mismatch.
5. Confirm only a fictional Odoo PO; do not contact a supplier or move money.

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

**Behavior**

1. Reject with a bounded reason, preserve the decision, and idempotently cancel
   the draft.
2. Accept only supported structured change fields plus an untrusted bounded
   note.
3. Invalidate prior recommendation/approval, recompute all policy, safely
   update the same draft, increment revision, and require reapproval.
4. Route unsupported or ambiguous requests to manual review.
5. Reconcile create/update/cancel/confirm results before any write retry.
6. Expose a chronological immutable audit timeline.

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

1. Verify default-deny network flows and only documented allow paths.
2. Verify containers run non-root, drop capabilities, use seccomp, and use
   read-only roots with explicit writable volumes where supported.
3. Rotate Odoo key, MCP/Cron tokens, session secret, database credentials, and
   Grafana credentials without logging old/new values.
4. Test browser security headers, CSRF, session fixation, role escalation,
   input limits, untrusted MCP output, prompt injection-like data, and error
   leakage.
5. Inspect image/dependency/secret/configuration scans and record accepted
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

1. Verify exact read/write/LLM timeouts and retries, permanent-error
   no-retry behavior, 120-second case limit, and reconciliation before write
   retry.
2. Send SIGTERM during reads, reasoning, draft creation, human wait, and
   confirmation; verify immediate readiness failure, checkpoint/reconciliation,
   and completion within the 45-second grace period.
3. Load the seeded scenario, confirm p95 approval-ready time, and verify a
   second API replica can schedule through HPA.
4. Measure every pod’s CPU/memory/disk use on one `t3.medium` worker; adjust
   hypotheses without removing required services.
5. Exercise EC2 stop/start warming behavior, prod snapshot recovery, retained
   local PVs, and reproducible seed fallback.
6. Record actual active-hour cost and confirm the $50 target/$75 review ceiling
   alerts.

**Stop condition**

If a complete environment cannot fit safely below 85% memory or the required
second API replica cannot schedule, stop stretch work and revise resource
values or the approved architecture before production promotion.

**Verification:** Run resilience/load suites, inspect Kubernetes events and
dashboards, perform a controlled recovery drill, and update evidence.

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

1. Verify metrics for requests, errors, latency, LLM/MCP failures/timeouts,
   retries, tokens, scan/case outcomes, approval latency, duplicates, safety
   attempts, pod restarts, CPU/memory, disk, and dependencies.
2. Verify log fields and redaction from every service, Loki queryability, S3
   objects, and dev 14-day/prod 90-day lifecycle configuration.
3. Fire every actionable alert safely and confirm its runbook action.
4. Run the complete unit, integration, UI, Compose, infrastructure, security,
   resilience, dev smoke, prod smoke, and immutable-release verification.
5. Walk CR-01 through CR-16 and attach actual evidence; do not mark a
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

1. Time the documented manual replenishment workflow at least three times and
   record the baseline method and result.
2. Rehearse the happy path and over-budget exception path from clean,
   environment-specific fictional seed data.
3. Show Odoo draft/confirmation, UI evidence, immutable audit, Grafana
   metrics/logs/alerts, GitHub Actions, Argo CD, and immutable digest promotion.
4. Fit introduction, value, architecture, agent/MCP, live demo,
   infrastructure/observability/testing, pipeline, and AI-agent reflection into
   15 minutes.
5. Prepare a truthful failure fallback using pre-recorded screenshots or
   exported evidence; do not use generated video and do not present fallback
   evidence as live.
6. Perform one cold-start rehearsal after the cluster was intentionally
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
| G1 — Local skeleton | Unit/integration reports and manual browser check | Local API → LangGraph → real MCP transport → result works |
| G2 — Odoo boundary | Executable Odoo contract, repeatable seed, live MCP read | No unresolved Odoo contract assumption |
| G3 — Container | Image builds, Compose E2E, image contract checks | Local system runs from pinned containers |
| G4 — Platform | Terraform/cluster/Kustomize/CI validation | Reproducible AWS and full dev/prod desired state |
| G5 — Dev skeleton | Real Bedrock/Odoo/MCP/DynamoDB/Cognito smoke and observability evidence | Full walking skeleton healthy in dev |
| G6 — Prod skeleton | Same-digest proof, Argo health, prod smoke | Promotion workflow proven |
| G7 — Functional MVP | All seven procurement vertical slices and safety tests | End-to-end approval-gated fictional PO workflow works |
| G8 — Release candidate | Security, resilience, resource, cost, observability, and CR-01–CR-16 evidence | MVP is submission-ready |
| G9 — Presentation | Two timed rehearsals and fallback evidence | Fifteen-minute demo is ready |

No stretch work may begin before G9.

## 10. Requirements-to-task traceability

| Requirement | Primary implementation tasks | Acceptance evidence |
|---|---|---|
| CR-01 Planning gates | Current plan, T34 | Approved spec/plan PRs and explicit implementation instruction |
| CR-02 Business problem/value | T11, T25–T31, T35 | Timed baseline, approval-ready latency, live workflow |
| CR-03 Coded LLM framework | T05, T12, T28–T31 | LangGraph tests, deployed graph, real model evidence |
| CR-04 HTTP API/UI | T03, T05, T06, T14, T25–T31 | API/UI tests and live dashboard |
| CR-05 Reliability contracts | T02–T05, T12, T28–T33 | Errors, retries, fallback, reconciliation, shutdown tests |
| CR-06 Real MCP interaction | T04, T07, T11, T25–T31 | Streamable HTTP tests and demo traces |
| CR-07 Self-managed EC2 Kubernetes | T16, T18A–T18B | Terraform state, node inventory, no EKS |
| CR-08 Complete dev/prod | T17, T19A–T24 | Separate full-stack overlays, namespaces, Argo apps, smoke |
| CR-09 Workload quality | T18A–T20B, T32, T33 | Probes, resources, HPA, secrets, graceful shutdown evidence |
| CR-10 Terraform | T15–T17 | Validated/applied state and reproducible runbooks |
| CR-11 CI/CD/GitOps | T21–T24 | PR checks, dev/main flows, Argo reconciliation, digest identity |
| CR-12 Observability | T03–T05, T20A–T20B, T25–T34 | Metrics, logs, S3 objects, dashboards, fired alerts |
| CR-13 Automated testing | Every behavior task; T34 audit | Unit/integration/UI/smoke/JUnit/coverage evidence |
| CR-14 Presentation | T06, T23, T30, T34, T35 | Timed live demo, dashboard, pipeline, reflection |
| CR-15 Security | T02–T04, T11–T24, T25–T33 | IAM/RBAC/CSRF/idempotency/redaction/network/approval tests |
| CR-16 Decision/AWS justification | T15–T17, T23, T33–T35 | Plans, cost evidence, implementation status, explanation |

## 11. Test coverage map

| Behavior | Unit | Integration | Deployed smoke or acceptance |
|---|---|---|---|
| Forecast/trigger/need-by | T25 | T25 real MCP transport | T25 real Odoo |
| Duplicate/full/partial coverage | T25 | T25 concurrency | T25 seeded open PO |
| Offer eligibility/quantity | T26 | T26 MCP + Odoo adapter | T26 vendor comparison |
| Performance evidence | T26 | T26 MCP + Odoo adapter | T26 seeded receipts/returns |
| Budget and overage | T27 | T27 MCP + Odoo adapter | T27/T30 exception path |
| LLM recommendation/fallback | T12/T28 mocked | T28 graph + MCP | T28 real Bedrock |
| Draft/idempotency | T29 | T29 concurrency/ambiguous result | T29 real Odoo |
| Approval/confirmation | T30 | T30 stale/replay/role/exception | T30 real Odoo |
| Reject/change/reconcile | T31 | T31 failures/restarts | T31 real Odoo |
| API/auth/CSRF | T03/T05/T14/T25–T31 | T14/T25–T31 | T23/T24/T30 |
| React states/actions | T06/T14/T25–T31 | Local browser E2E | Dev/prod browser smoke |
| MCP tools | T04/T11/T25–T31 | All ten over Streamable HTTP | Real Odoo demo traces |
| AWS repositories | T12–T14 mocked | DynamoDB Local | Dev/prod AWS smoke |
| Kubernetes/config | T18A–T20B static | Render/policy/resource tests | Argo health/recovery |
| Security/shutdown/load | T32/T33 | Fault injection | Dev drills and prod-safe checks |

## 12. Risk-driven stop conditions

Implementation stops for review when any of these occurs:

- Odoo 19 Community or JSON-2 cannot provide an approved required operation.
- The selected Bedrock model is unavailable in the approved region/account or
  violates the expected IAM invocation contract.
- The complete stack cannot fit safely on each `t3.medium`/30 GB worker after
  reducing nonessential retention/cardinality/caches.
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

Supplier communication, payment, autonomous vendor approval, unbounded budget
exceptions, and real legal ordering require a new safety design and are not
implicitly authorized by finishing the MVP.

## 14. Plan review checklist

The user and course staff should confirm:

- The task order matches the course’s walking-skeleton-first strategy.
- Each task is small enough to review and has concrete files and checks.
- All ten MCP tools and their real transport are covered.
- Odoo feasibility is tested before broad dependence on its data model.
- Every PO remains manager-approved and revision-bound.
- Dev and prod are complete, isolated, and constrained to the approved nodes.
- All AWS services are justified and provisioned through Terraform.
- GitHub Actions never deploys workloads directly.
- The exact dev-tested image digests are promoted to prod.
- Tests cover happy paths, failures, malformed outputs, timeouts, retries,
  fallbacks, concurrency, and ambiguous writes.
- Logs reach Loki and encrypted S3 without leaking sensitive procurement data.
- Dashboards and alerts cover application, LLM, MCP, Kubernetes, dependencies,
  and procurement safety.
- Cost, disk, memory, non-24/7 operation, and recovery limitations are tested
  and presented honestly.
- Stretch integrations remain blocked until the submission-ready MVP is
  complete.

## 15. Next approval gate

The next action is user review of this plan. Requested changes must be made
before it is considered user-approved.

After user approval, course staff must approve `docs/plan.md` through a pull
request. Even after that approval, implementation must wait for a separate,
explicit user instruction to begin.
