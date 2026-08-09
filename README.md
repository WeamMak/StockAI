# StockAI Procurement Agent

StockAI is an approval-gated AI procurement agent for a fictional, self-hosted
Odoo business. The approved design and implementation sequence live in
[`docs/spec.md`](docs/spec.md) and [`docs/plan.md`](docs/plan.md).

Tasks T01 through T14 establish the local walking skeleton and its real Odoo,
Bedrock, DynamoDB, and Cognito adapter boundaries: the Python domain, FastAPI
asynchronous scan API, coded LangGraph, authenticated Procurement MCP tool,
React polling UI, service-owned observability, and separate runnable API and
MCP composition roots. The local test path remains deterministic and needs
neither AWS nor real Odoo. Explicit runtime modes can select the validated Odoo
JSON-2, Bedrock, DynamoDB, and Cognito adapters; write operations remain later
approved plan tasks.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.11 or later
- Node.js 20.19 or later and npm
- Docker Engine with Docker Compose
- GNU Make
- `curl`

`uv` reads `.python-version` and can install the requested Python version when
it is not already available.

## Set up a clean clone

```bash
uv sync --locked
cd frontend && npm ci && cd ..
```

Do not put real credentials in the repository. `.env.example` documents the
safe runtime configuration names and contains only explicit fictional values.

## Run the reproducible Compose stack

Create the ignored local environment file once. Its provided tokens are
explicitly fictional and must never be replaced with production credentials:

```bash
cp .env.example .env
```

Then build and start the complete local stack with one command:

```bash
make compose-up
```

Open <http://127.0.0.1:8080>, select **Sign in with Cognito**, then select
**Run manual scan** and inspect the fictional read-only recommendation. This
local command layers `compose.test.yaml` over the deployable topology, so the
sign-in redirect uses the test-only fictional identity adapter. That adapter is
not selectable through application environment configuration and is not part
of dev or prod deployment configuration. Compose waits for fake Odoo, MCP,
API, and frontend health before returning. Stop the stack and remove its
disposable runtime mounts with:

```bash
make compose-down
```

The fake Odoo endpoint is an internal deterministic test contract. It does not
claim to implement the Odoo 19 JSON-2 contract; that contract is verified in
Task T10 before the real adapter is implemented.

## Run the process-only local walking skeleton

```bash
./scripts/run-local-skeleton.sh
```

The command generates one ephemeral local MCP bearer token, starts the MCP and
API as separate processes, and starts the React development server. It uses the
same test-only fictional identity adapter as the local Compose command. Open
<http://127.0.0.1:5173>, sign in, select **Run manual scan**, and inspect the
fictional read-only recommendation. Press Ctrl+C once to stop all three
processes.
If port 5173 is already in use, choose another frontend port with
`PROCUREMENT_FRONTEND_PORT=5174 ./scripts/run-local-skeleton.sh`.

The script is intentionally local-only. It uses deterministic fictional
identity, ERP, and structured-LLM adapters assigned to the test walking
skeleton; it does not call Cognito, Odoo, Bedrock, or AWS and it does not
contain a committed credential.

The API setting `PROCUREMENT_LLM_MODE` accepts only `local` or `bedrock` and
defaults to `local`. Bedrock mode constructs the approved GPT-OSS adapter using
the ambient IAM credential chain; no AWS credential is stored in application
configuration. Its first live deployed invocation remains the T23 dev smoke
test.

`PROCUREMENT_PERSISTENCE_MODE` accepts only `memory` or `dynamodb` and defaults
to `memory`. DynamoDB mode uses separate application and LangGraph checkpoint
tables, the immutable scan/case identifier as `thread_id`, and the ambient AWS
credential chain. `compose.test.yaml` provides a pinned, local-only DynamoDB
Local profile used by `tests/integration/test_dynamodb_local.py`; that test
creates disposable tables, replaces the API process, and verifies that scan
polling and sanitized graph checkpoints survive the replacement.

`PROCUREMENT_AUTHENTICATION_MODE` accepts only `disabled` or `cognito` and
defaults to `disabled`. Disabled mode grants no identity and every protected
route fails closed. Cognito mode requires DynamoDB persistence plus an HTTPS
domain, user-pool ID, client ID, and callback URI; an app-client secret is
optional. It uses authorization code with PKCE, verifies the ID-token signature,
issuer, audience, token use, nonce, and exact officer/manager group, then stores
only a hashed opaque session record in DynamoDB. Cognito tokens never enter the
browser session or application table. The `stockai-cognito-bootstrap` command
idempotently creates the two fictional groups and users after verifying that
self-signup is disabled; temporary passwords must be injected into that finite
process and are never printed.

Useful local checks:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
# Requires an authenticated manager session:
curl http://127.0.0.1:8000/health/dependencies
curl http://127.0.0.1:8000/metrics
```

The T05 scan contract is:

```text
GET  /api/v1/session        -> current bounded user and role
POST /api/v1/scans          -> 202 Accepted; officer/manager session plus CSRF
GET  /api/v1/scans          -> bounded newest-first list; officer/manager
GET  /api/v1/scans/{id}     -> progress/result/failure; officer/manager
POST /internal/v1/scans     -> 202 with the separate Cron bearer credential
```

The reusable `procurement.api.app` factory still defaults to a safe unconfigured
workflow. The local command starts `procurement.bootstrap.api` instead, whose
composition root connects the compiled LangGraph to the independent MCP process
over authenticated Streamable HTTP.

## Quality commands

```bash
make lock-check
make format-check
make lint
make test-unit
make test-integration
make compose-validate
make test-e2e
```

Run the complete quality and unit-test suite with `make check`. The integration
target starts a localhost MCP server and requires permission to bind a local
TCP socket.

```bash
make check
```

Generated environments, caches, coverage output, and test reports are ignored
by Git.

The single verification command for the local backend walking skeleton is:

```bash
make test-integration
```

It starts actual API and MCP processes for the T07 happy and timeout paths,
including retry, log, metric, and safe-error assertions.

The Task T09 end-to-end target builds isolated Compose projects and exercises
the success, no-valid-response, malformed-upstream-response, and timeout
scenarios through the public NGINX proxy. Every scenario uses the real
FastAPI-to-LangGraph-to-authenticated-MCP transport and the separate fake Odoo
HTTP service; cleanup runs even after a failed assertion.
