# T22 Bedrock Leading-Quote Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely accept the one observed GPT-OSS stray-leading-quote envelope, publish a new immutable dev release through the normal T22 workflow, and resume T23 against that exact release.

**Architecture:** Keep `BedrockStructuredLlm.recommend()` as the public test seam. Add one private strict decoder branch that removes exactly one leading quote only for a response beginning `"{` and ending at `}`, then delegates to the existing `json.loads` and unchanged semantic validator. Land the already-completed release-ID/T23 prerequisites on `dev` with the image workflow explicitly skipped, then land the correction separately so the normal workflow can verify the prior release, rebuild the changed API/MCP inputs, assemble all four exact digests, and let Argo reconcile them.

**Tech Stack:** Python 3.12, pytest/AnyIO, boto3 Bedrock Converse, Docker BuildKit/Docker Hub, GitHub Actions, Kustomize, Argo CD, AWS SSM, Kubernetes.

## Global Constraints

- Normalize only one leading `"` immediately before `{` when the structured response, excluding JSON whitespace, ends at `}`.
- Use the existing `json.loads` after normalization and the unchanged strict semantic validator afterward.
- Do not add a JSON-repair dependency, embedded-object search, code-fence removal, delimiter balancing, coercion, or schema relaxation.
- Preserve one provider repair attempt, bounded retry/timeout behavior, hidden-reasoning exclusion, deterministic candidate eligibility, and `LlmOutputInvalidError` fallback.
- Use only the normal GitHub/Argo T22 release path; do not patch running pods or deploy with local `kubectl`.
- Do not run the dev worker-replacement drill until the exact-release smoke passes, and obtain explicit approval immediately before termination.

---

### Task 1: Land the stable-release and T23 prerequisites without triggering an image build

**Files:**
- Modify: `.github/workflows/dev-images.yml`
- Modify: `Makefile`
- Modify: `deploy/releases/dev.json`
- Modify: `deploy/releases/schema.json`
- Modify: `infra/terraform/modules/app-environment/main.tf`
- Modify: `scripts/release/create_manifest.py`
- Modify: `scripts/release/verify_manifest.py`
- Create: `scripts/release/record_validation.py`
- Create: `scripts/smoke/dev.sh`
- Create: `tests/smoke/test_dev_skeleton.py`
- Create: `tests/unit/release/test_record_validation.py`
- Modify/Create: the currently prepared T23 workflow, dashboard, runbook, configuration, and release tests shown by `git status --short`

**Interfaces:**
- Consumes: the accepted T22 dev manifest whose immutable core is unchanged.
- Produces: a verified stable `releaseId`, append-only pending validation shape, T23 smoke/recording surface, and a `dev` parent revision the next workflow can verify.

- [ ] **Step 1: Review the exact prepared diff and ensure no Bedrock implementation is present yet**

Run:

```bash
git status --short
git diff -- src/procurement/adapters/aws/bedrock.py tests/unit/adapters/aws/test_bedrock.py
git diff --check
```

Expected: the Bedrock source/test diff is empty; only the already-reviewed release-ID, Cognito scope, T23 harness, dashboards, workflow guard, runbook, and their tests are pending.

- [ ] **Step 2: Re-run the prepared prerequisite verification**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/unit/release tests/infra/test_environment_plans.py::test_each_environment_has_isolated_tables_secrets_and_cognito tests/config/test_ci_workflows.py tests/config/test_makefile_contract.py
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -m scripts.release.verify_manifest deploy/releases/dev.json
bash -n scripts/smoke/dev.sh
```

Expected: all focused tests pass, the accepted release verifies with its stable ID, and the smoke wrapper has valid Bash syntax.

- [ ] **Step 3: Commit only the prepared prerequisite set on `feature/t23`**

Run:

```bash
git add .github/workflows/dev-images.yml Makefile deploy/releases/dev.json deploy/releases/schema.json infra/terraform/modules/app-environment/main.tf scripts/release scripts/smoke tests/config tests/infra/test_environment_plans.py tests/smoke tests/unit/release deploy/kubernetes/base/observability/dashboards docs/runbooks/dev-validation.md
git commit -m "feat(release): prepare exact dev validation"
git status --short
```

Expected: the commit contains no Bedrock decoder change and the worktree is clean.

- [ ] **Step 4: Merge the prerequisite commit into `dev` with the existing image-workflow skip marker**

Run:

```bash
git switch dev
git merge --no-ff feature/t23 -m "merge: prepare T23 exact validation [skip dev-images]"
git push origin dev
git switch feature/t23
```

Expected: `origin/dev` contains the corrected stable manifest before any new build consumes it; the workflow prepare job is skipped by the reviewed marker.

- [ ] **Step 5: Verify the remote prerequisite manifest**

Run:

```bash
git fetch origin dev
git show origin/dev:deploy/releases/dev.json > /tmp/stockai-t23-prior-release.json
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -m scripts.release.verify_manifest /tmp/stockai-t23-prior-release.json
```

Expected: `release manifest verified`.

---

### Task 2: Add the live-response regression and exact normalization test-first

**Files:**
- Modify: `tests/unit/adapters/aws/test_bedrock.py`
- Modify: `src/procurement/adapters/aws/bedrock.py`

**Interfaces:**
- Consumes: `BedrockStructuredLlm.recommend(request: RecommendationRequest) -> StructuredRecommendation` and one Converse text block.
- Produces: private `_decode_structured_object(text: str) -> Mapping[str, object]`; no public interface changes.

- [ ] **Step 1: Add the failing sanitized live-envelope regression at the public seam**

Add this test using the existing `_request`, `_adapter`, and fake client:

```python
@pytest.mark.anyio
async def test_one_gpt_oss_leading_quote_is_normalized_before_validation() -> None:
    live_text = (
        '"{ "budget_acknowledgement":"not_evaluated", '
        '"decision":"recommend", "product_id":"product-101", '
        '"rationale":"Eligible product selected; budget not evaluated." '
        '\t,"risk_flags":[] }\n'
    )
    response = _response(live_text)
    response["output"]["message"]["content"].insert(
        0,
        {"reasoningContent": {"reasoningText": {"text": "not retained"}}},
    )
    client = FakeBedrockRuntimeClient(response, response)

    recommendation = await _adapter(client).recommend(_request())

    assert recommendation.product_id == "product-101"
    assert len(client.requests) == 1
```

- [ ] **Step 2: Run the regression and confirm red**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/unit/adapters/aws/test_bedrock.py::test_one_gpt_oss_leading_quote_is_normalized_before_validation
```

Expected: FAIL because the current strict `json.loads` rejects the leading quote and the adapter exhausts the repair path.

- [ ] **Step 3: Add broader-malformation negative regressions**

Add a parameterized public-seam test whose first responses are these literals and whose second response is `_response("still invalid")`:

```python
@pytest.mark.parametrize(
    "invalid_text",
    (
        '```json\n{"decision":"recommend"}\n```',
        '"{"decision":"recommend"} trailing',
        '"{\\"decision\\":\\"recommend\\"}"',
        '"{"decision" "recommend"}',
        '"{"decision":"recommend"}',
        (
            '"{"decision":"recommend","product_id":"not-eligible",'
            '"rationale":"Invalid selection.","risk_flags":[],'
            '"budget_acknowledgement":"not_evaluated"}'
        ),
    ),
)
@pytest.mark.anyio
async def test_leading_quote_normalization_rejects_every_broader_case(
    invalid_text: str,
) -> None:
    client = FakeBedrockRuntimeClient(
        _response(invalid_text),
        _response("still invalid"),
    )

    with pytest.raises(LlmOutputInvalidError):
        await _adapter(client).recommend(_request())

    assert len(client.requests) == 2
```

- [ ] **Step 4: Implement only the exact decoder branch**

Add this module-private function and call it from `_response` in place of direct `json.loads` plus the mapping check:

```python
def _decode_structured_object(text: str) -> Mapping[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        bounded = text.rstrip()
        if not bounded.startswith('"{') or not bounded.endswith("}"):
            raise
        payload = json.loads(bounded[1:])
    if not isinstance(payload, Mapping):
        raise ValueError("Bedrock output must be a JSON object")
    return payload
```

Keep content-block selection, usage extraction, repair behavior, and `self.validator(...)` unchanged.

- [ ] **Step 5: Run the focused green cycle and existing safety tests**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/unit/adapters/aws/test_bedrock.py
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run ruff check src/procurement/adapters/aws/bedrock.py tests/unit/adapters/aws/test_bedrock.py
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run mypy --strict src/procurement/adapters/aws/bedrock.py
```

Expected: the live regression and all negative/retry/reasoning/fallback tests pass; Ruff and strict mypy pass.

- [ ] **Step 6: Commit the bounded correction separately**

Run:

```bash
git add src/procurement/adapters/aws/bedrock.py tests/unit/adapters/aws/test_bedrock.py
git commit -m "fix(llm): handle GPT-OSS leading quote safely"
```

Expected: one reviewable correction commit with no schema, prompt, model, dependency, or Kubernetes manifest change.

---

### Task 3: Verify locally and publish a new immutable dev release

**Files:**
- Modify by workflow only: `deploy/releases/dev.json`
- Modify by workflow only: `deploy/kubernetes/overlays/dev/kustomization.yaml`

**Interfaces:**
- Consumes: the verified prior dev manifest and corrected `feature/t23` source commit.
- Produces: one pending immutable release with a new `releaseId`, application identity, source traceability, and exact four-image map.

- [ ] **Step 1: Run the smallest complete pre-push regression set**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache ACTIONLINT=/tmp/actionlint make check
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/unit/release tests/config/test_ci_workflows.py tests/kubernetes/test_application_overlays.py
git diff --check
```

Expected: all commands pass before any release push.

- [ ] **Step 2: Merge the correction into `dev` and trigger the normal workflow**

Run:

```bash
git switch dev
git merge --no-ff feature/t23 -m "merge: correct Bedrock structured output"
git push origin dev
```

Expected: `Dev image release` runs. Because `src/` is a declared input for both API and MCP, those two images rebuild; frontend and Odoo are carried only from the verified prior release with identical build-input identities. Desired state still contains exactly four immutable digests.

- [ ] **Step 3: Monitor the exact workflow run and inspect failures before continuing**

Run:

```bash
run_id="$(gh run list --workflow dev-images.yml --branch dev --limit 1 --json databaseId --jq '.[0].databaseId')"
test -n "$run_id"
gh run watch "$run_id" --exit-status
```

Expected: prepare, API/MCP build and Scout, immutable assembly, release verification, overlay render, and bot desired-state commit succeed. Stop on any failed job; do not build or push locally as a workaround.

- [ ] **Step 4: Fast-forward to the bot-owned desired state and verify the new release**

Run:

```bash
git fetch origin dev
git pull --ff-only origin dev
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -m scripts.release.verify_manifest deploy/releases/dev.json
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -m scripts.release.build_inputs --output /tmp/stockai-t22-new-identities.json
```

Expected: validation is pending, the release/source/application identities are new, API and MCP digests match the new build results, and frontend/Odoo remain exact verified members of the new four-image manifest.

- [ ] **Step 5: Wait for Argo and verify exact digest convergence**

Run:

```bash
PYTHONPATH=/home/weam/StockAI UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -c 'from tests.smoke.test_dev_skeleton import _deployed_release; print(_deployed_release())'
```

Repeat only within the documented bounded reconciliation window.

Expected: Argo reports `Synced` and `Healthy`; all four deployed `@sha256` references equal `deploy/releases/dev.json`.

---

### Task 4: Resume T23 smoke against the exact new release

**Files:**
- Modify on success: `deploy/releases/dev.json`
- Create locally: `reports/smoke/${STOCKAI_SMOKE_RUN_ID}.json` (ignored sanitized evidence)
- Modify after live evidence: `docs/implementation-status.md`

**Interfaces:**
- Consumes: the Argo-reconciled pending release, existing fictional Cognito manager, seeded dev Odoo data, and fresh browser cookies.
- Produces: append-only passed validation bound to the new release ID, four digests, Argo revision, smoke ID, timestamp, and evidence digest.

- [ ] **Step 1: Obtain a fresh Cognito session and run the exact smoke**

Sign in at `https://app.dev.stockai.fursa.click/auth/login`, retrieve fresh matching cookie values in browser developer tools, and run:

```bash
make smoke-dev
```

Expected: HTTPS → Cognito → FastAPI/LangGraph → Bedrock → MCP → Odoo → DynamoDB succeeds; exact Argo digests, metrics, sanitized Loki logs, and S3 evidence pass; the recorder appends one passed attempt.

- [ ] **Step 2: Verify and publish only bounded validation evidence**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run python -m scripts.release.verify_manifest deploy/releases/dev.json
git diff --check
git add deploy/releases/dev.json docs/implementation-status.md
git commit -m "docs: record exact dev smoke [record dev-validation]"
git push origin dev
```

Expected: the validation-only marker prevents image rebuilding; immutable release fields and deployed digests do not change.

---

### Task 5: Gate and complete the representative T23 worker drill

**Files:**
- Modify after success: `docs/implementation-status.md`
- Create locally: one ignored sanitized report under `reports/smoke/`

**Interfaces:**
- Consumes: the exact release only after Task 4 passed and `docs/runbooks/dev-validation.md`.
- Produces: one reusable worker/storage recovery evidence digest and truthful T23 completion status.

- [ ] **Step 1: Capture the pre-drill sanitized checklist**

Record the exact dev instance/private DNS, ASG capacity, Node label/taint/Ready state, three PV/PVC/VolumeAttachment and EC2 attachment identities, fictional Odoo verifier counts, Prometheus sample, four Grafana dashboard UIDs, ALB health, workload health, and Argo revision.

Expected: all preconditions are healthy and tied to the passed release.

- [ ] **Step 2: Stop and request explicit approval for the resolved instance ID**

Do not execute a termination command in this step. Present the exact current dev worker instance ID and wait for an explicit user response approving that target.

- [ ] **Step 3: After approval only, terminate through the existing ASG mechanism**

Run exactly:

```bash
: "${STOCKAI_APPROVED_DEV_WORKER_INSTANCE_ID:?set only after explicit approval}"
[[ "$STOCKAI_APPROVED_DEV_WORKER_INSTANCE_ID" =~ ^i-[0-9a-f]{17}$ ]]
aws autoscaling terminate-instance-in-auto-scaling-group --region us-east-1 --instance-id "$STOCKAI_APPROVED_DEV_WORKER_INSTANCE_ID" --no-should-decrement-desired-capacity
```

Expected: lifecycle cleanup records `outcome=clean`, the old Node disappears, one correctly identified dev replacement joins Ready, all three retained EBS volumes reattach, data/history remain, and the complete stack/ALB/Argo recover.

- [ ] **Step 4: Record evidence and close T23 truthfully**

Hash the sanitized drill report, update `docs/implementation-status.md` with the date, release ID, old/new identities, result, and evidence digest, rerun release verification, and commit only the status evidence with the validation marker.

Expected: T23 is marked complete only when both exact-release smoke and the one representative drill have passed; T24 remains untouched.
