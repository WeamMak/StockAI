UV ?= uv
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
ACTIONLINT ?= actionlint
PYTHON_PATHS := src tests scripts odoo
TERRAFORM_ROOTS := bootstrap platform edge environments/dev environments/prod

export UV_CACHE_DIR

.PHONY: help sync lock-check format format-check lint test-unit test-integration \
	test-e2e build odoo-image odoo-contract odoo-seed odoo-verify-seed \
	compose-validate terraform-validate kubernetes-validate compose-up \
	compose-down infra-provision check

help:
	@echo "Available targets:"
	@echo "  sync          Install the locked runtime and development dependencies"
	@echo "  lock-check    Verify uv.lock matches pyproject.toml"
	@echo "  format        Format Python source and tests"
	@echo "  format-check  Check Python formatting without changing files"
	@echo "  lint          Run Ruff, mypy, ESLint, architecture, and actionlint checks"
	@echo "  test-unit     Run unit tests with JUnit and coverage reports"
	@echo "  test-integration Run real-transport integration tests"
	@echo "  test-e2e      Run the four deterministic local Compose scenarios"
	@echo "  build         Build the three app images and the StockAI Odoo image"
	@echo "  odoo-image    Build only the derived StockAI Odoo image"
	@echo "  odoo-contract Run the clean Odoo add-on/bootstrap/seed contract suite"
	@echo "  odoo-seed     Seed fictional Odoo data in the running contract stack"
	@echo "  odoo-verify-seed Verify the running Odoo fictional seed"
	@echo "  compose-validate Validate base, test, and Odoo Compose configurations"
	@echo "  terraform-validate Validate every Terraform root and its plan contracts"
	@echo "  kubernetes-validate Render and schema-check both Kubernetes overlays"
	@echo "  compose-up    Build and start the local test-authenticated stack"
	@echo "  compose-down  Stop and remove the local Compose stack"
	@echo "  infra-provision Run guided, approval-gated AWS provisioning"
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
	npm --prefix frontend run lint
	$(UV) run pytest -q tests/unit/test_architecture.py
	$(ACTIONLINT) .github/workflows/*.yml

test-unit:
	mkdir -p reports/junit reports/coverage
	$(UV) run pytest tests/unit \
		--cov=src/procurement \
		--cov-report=term-missing \
		--cov-report=xml:reports/coverage/unit.xml \
		--junitxml=reports/junit/unit.xml
	npm --prefix frontend run test -- \
		--coverage.enabled \
		--coverage.reporter=text \
		--coverage.reporter=json-summary \
		--coverage.reportsDirectory=../reports/coverage/frontend \
		--reporter=default \
		--reporter=junit \
		--outputFile.junit=../reports/junit/frontend.xml

test-integration:
	mkdir -p reports/junit
	$(UV) run pytest tests/integration \
		--junitxml=reports/junit/integration.xml

test-e2e:
	mkdir -p reports/junit
	$(UV) run pytest tests/e2e \
		--junitxml=reports/junit/e2e.xml

build:
	docker compose -f compose.yaml build
	docker compose -f compose.odoo.yaml build odoo

odoo-image:
	docker compose -f compose.odoo.yaml build odoo

odoo-contract:
	mkdir -p reports/junit
	$(UV) run pytest tests/config/test_odoo_image_contract.py tests/contract \
		tests/integration/test_odoo_bootstrap.py \
		tests/integration/test_mcp_real_odoo.py \
		--junitxml=reports/junit/contract.xml

odoo-seed:
	docker compose -f compose.odoo.yaml exec -T odoo bash -lc \
		'odoo shell --no-http --database="$$ODOO_CONTRACT_DATABASE" --db_host="$$HOST" --db_port="$$PORT" --db_user="$$USER" --db_password="$$PASSWORD" --log-level=error < /opt/stockai/seed.py'

odoo-verify-seed:
	docker compose -f compose.odoo.yaml exec -T odoo bash -lc \
		'odoo shell --no-http --database="$$ODOO_CONTRACT_DATABASE" --db_host="$$HOST" --db_port="$$PORT" --db_user="$$USER" --db_password="$$PASSWORD" --log-level=error < /opt/stockai/verify_seed.py'

compose-validate:
	docker compose -f compose.yaml config --quiet
	docker compose -f compose.yaml -f compose.test.yaml config --quiet
	docker compose -f compose.odoo.yaml config --quiet

terraform-validate:
	terraform fmt -check -recursive infra/terraform
	@for root in $(TERRAFORM_ROOTS); do \
		terraform -chdir="infra/terraform/$$root" init -backend=false -input=false; \
		terraform -chdir="infra/terraform/$$root" validate; \
	done
	$(UV) run pytest tests/infra

kubernetes-validate:
	$(UV) run pytest tests/kubernetes
	kubectl kustomize deploy/kubernetes/overlays/dev | docker run --rm -i \
		ghcr.io/yannh/kubeconform:v0.7.0 \
		-strict -summary -kubernetes-version 1.35.0 \
		-skip ExternalSecret,SecretStore
	kubectl kustomize deploy/kubernetes/overlays/prod | docker run --rm -i \
		ghcr.io/yannh/kubeconform:v0.7.0 \
		-strict -summary -kubernetes-version 1.35.0 \
		-skip ExternalSecret,SecretStore

compose-up:
	docker compose -f compose.yaml -f compose.test.yaml up --build --detach --wait --wait-timeout 180

compose-down:
	docker compose -f compose.yaml -f compose.test.yaml down --volumes --remove-orphans

infra-provision:
	$(UV) run python -m scripts.infra.provision provision

check: lock-check format-check lint test-unit
