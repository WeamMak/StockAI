UV ?= uv
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
PYTHON_PATHS := src tests

export UV_CACHE_DIR

.PHONY: help sync lock-check format format-check lint test-unit test-integration \
	test-e2e compose-validate compose-up compose-down check

help:
	@echo "Available targets:"
	@echo "  sync          Install the locked runtime and development dependencies"
	@echo "  lock-check    Verify uv.lock matches pyproject.toml"
	@echo "  format        Format Python source and tests"
	@echo "  format-check  Check Python formatting without changing files"
	@echo "  lint          Run Ruff, mypy, and architecture checks"
	@echo "  test-unit     Run unit tests with JUnit and coverage reports"
	@echo "  test-integration Run real-transport integration tests"
	@echo "  test-e2e      Run the four deterministic local Compose scenarios"
	@echo "  compose-validate Validate the base and test Compose configurations"
	@echo "  compose-up    Build and start the healthy local four-service stack"
	@echo "  compose-down  Stop and remove the local Compose stack"
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

test-integration:
	mkdir -p reports/junit
	$(UV) run pytest tests/integration \
		--junitxml=reports/junit/integration.xml

test-e2e:
	mkdir -p reports/junit
	$(UV) run pytest tests/e2e \
		--junitxml=reports/junit/e2e.xml

compose-validate:
	docker compose -f compose.yaml config --quiet
	docker compose -f compose.yaml -f compose.test.yaml config --quiet

compose-up:
	docker compose -f compose.yaml up --build --detach --wait --wait-timeout 180

compose-down:
	docker compose -f compose.yaml down --volumes --remove-orphans

check: lock-check format-check lint test-unit
