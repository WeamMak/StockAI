# StockAI Procurement Agent

StockAI is an approval-gated AI procurement agent for a fictional, self-hosted
Odoo business. The approved design and implementation sequence live in
[`docs/spec.md`](docs/spec.md) and [`docs/plan.md`](docs/plan.md).

Tasks T01 and T02 established the Python foundation and framework-independent
procurement domain contracts. The repository does not contain runnable
application behavior yet; that begins with later approved plan tasks.

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

Do not put credentials in the repository. Copy `.env.example` to `.env` only
when a later task introduces documented local configuration.

## Quality commands

```bash
make lock-check
make format-check
make lint
make test-unit
```

Run the complete quality and unit-test suite with:

```bash
make check
```

Generated environments, caches, coverage output, and test reports are ignored
by Git.
