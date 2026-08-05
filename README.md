# StockAI Procurement Agent

StockAI is an approval-gated AI procurement agent for a fictional, self-hosted
Odoo business. The approved design and implementation sequence live in
[`docs/spec.md`](docs/spec.md) and [`docs/plan.md`](docs/plan.md).

Tasks T01 through T04 established the Python foundation, framework-independent
procurement domain contracts, the runnable FastAPI observability baseline, and
the first authenticated Procurement MCP tool. The MCP service now discovers and
calls `list_replenishment_candidates` over real Streamable HTTP with a
deterministic fictional ERP adapter in tests. The LangGraph workflow and real
Odoo adapter begin in later approved plan tasks.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.11 or later
- GNU Make

`uv` reads `.python-version` and can install the requested Python version when
it is not already available.

## Set up a clean clone

```bash
uv sync --locked
```

Do not put credentials in the repository. `.env.example` documents the safe
runtime configuration names and contains no credentials.

## Run the API locally

```bash
uv run uvicorn procurement.api.app:app --host 127.0.0.1 --port 8000
```

The defaults are the `dev` environment and `INFO` JSON logging. Override them
through `PROCUREMENT_ENVIRONMENT` and `PROCUREMENT_LOG_LEVEL` when needed.

Useful local checks:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/health/dependencies
curl http://127.0.0.1:8000/metrics
```

## Quality commands

```bash
make lock-check
make format-check
make lint
make test-unit
make test-integration
```

Run the complete quality and unit-test suite with `make check`. The integration
target starts a localhost MCP server and requires permission to bind a local
TCP socket.

```bash
make check
```

Generated environments, caches, coverage output, and test reports are ignored
by Git.
