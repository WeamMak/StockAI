UV ?= uv
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
PYTHON_PATHS := src tests

export UV_CACHE_DIR

.PHONY: help sync lock-check format format-check lint test-unit check

help:
	@echo "Available targets:"
	@echo "  sync          Install the locked runtime and development dependencies"
	@echo "  lock-check    Verify uv.lock matches pyproject.toml"
	@echo "  format        Format Python source and tests"
	@echo "  format-check  Check Python formatting without changing files"
	@echo "  lint          Run Ruff, mypy, and architecture checks"
	@echo "  test-unit     Run unit tests with JUnit and coverage reports"
	@echo "  check         Run the complete Python verification suite"

sync:
	$(UV) sync --locked

lock-check:
	$(UV) lock --check

format:
	$(UV) run ruff format $(PYTHON_PATHS)

format-check:
	$(UV) run ruff format --check $(PYTHON_PATHS)

lint:
	$(UV) run ruff check $(PYTHON_PATHS)
	$(UV) run mypy
	$(UV) run pytest -q tests/unit/test_architecture.py

test-unit:
	mkdir -p reports/junit reports/coverage
	$(UV) run pytest tests/unit \
		--cov=src/procurement \
		--cov-report=term-missing \
		--cov-report=xml:reports/coverage/unit.xml \
		--junitxml=reports/junit/unit.xml

check: lock-check format-check lint test-unit
