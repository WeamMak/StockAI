# T23 Argo CD/HPA Replica Ownership Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Argo CD and the dev HPAs from repeatedly overwriting the three autoscaled Deployments' replica counts so T23 can validate a stable `Synced` and `Healthy` release.

**Architecture:** Keep HPA ownership of `Deployment.spec.replicas` and keep Argo ownership of every other desired-state field. Configure three name-scoped Argo diff rules and make automated sync respect those rules.

**Tech Stack:** Argo CD Application YAML, Kubernetes HPA/Deployment resources, Pytest, PyYAML.

## Global Constraints

- Change only the dev Argo CD Application and its focused contract test.
- Ignore only `/spec/replicas` for `stockai-frontend`, `stockai-agent-api`, and `stockai-procurement-mcp`.
- Do not change HPA thresholds, replica bounds, workloads, images, release metadata, application behavior, or production configuration.
- Use the existing `dev` GitOps branch; do not deploy imperatively and do not rebuild images.
- Resume T23 smoke only after live Argo state is stably `Synced` and `Healthy` with the exact existing image digests.

---

### Task 1: Assign replica ownership to the three dev HPAs

**Files:**
- Modify: `tests/kubernetes/test_argocd_applications.py`
- Modify: `deploy/kubernetes/cluster/argocd/dev-application.yaml`

**Interfaces:**
- Consumes: Argo CD `Application.spec.ignoreDifferences` and `Application.spec.syncPolicy.syncOptions`.
- Produces: a dev Application contract in which Argo ignores only `/spec/replicas` on the three named HPA targets and respects that rule during sync.

- [ ] **Step 1: Write the failing application-contract assertions**

Add these assertions to `test_dev_application_tracks_dev_overlay_and_uses_automated_gitops`:

```python
    assert application["spec"]["ignoreDifferences"] == [
        {
            "group": "apps",
            "kind": "Deployment",
            "name": name,
            "jsonPointers": ["/spec/replicas"],
        }
        for name in (
            "stockai-frontend",
            "stockai-agent-api",
            "stockai-procurement-mcp",
        )
    ]
    assert application["spec"]["syncPolicy"]["syncOptions"] == [
        "CreateNamespace=false",
        "RespectIgnoreDifferences=true",
    ]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/kubernetes/test_argocd_applications.py
```

Expected: failure because `spec.ignoreDifferences` is absent and the new sync option is absent.

- [ ] **Step 3: Add the minimal declarative Argo configuration**

Add this immediately after `spec.destination` in `dev-application.yaml`:

```yaml
  ignoreDifferences:
    - group: apps
      kind: Deployment
      name: stockai-frontend
      jsonPointers:
        - /spec/replicas
    - group: apps
      kind: Deployment
      name: stockai-agent-api
      jsonPointers:
        - /spec/replicas
    - group: apps
      kind: Deployment
      name: stockai-procurement-mcp
      jsonPointers:
        - /spec/replicas
```

Add the one sync option without changing the existing option:

```yaml
    syncOptions:
      - CreateNamespace=false
      - RespectIgnoreDifferences=true
```

- [ ] **Step 4: Run focused and representative Kubernetes tests**

Run:

```bash
UV_CACHE_DIR=/tmp/stockai-uv-cache uv run pytest -q tests/kubernetes/test_argocd_applications.py tests/kubernetes/test_application_overlays.py tests/kubernetes/test_cluster_resources.py
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 5: Commit the correction on `feature/t23`**

```bash
git add deploy/kubernetes/cluster/argocd/dev-application.yaml tests/kubernetes/test_argocd_applications.py
git commit -m "fix(gitops): respect dev HPA replica ownership"
```

- [ ] **Step 6: Merge and reconcile through the existing dev GitOps path**

Merge `feature/t23` into `dev` with a configuration-only commit and push it. The changed paths are outside `.github/workflows/dev-images.yml` path filters, so no image or release workflow should run. Verify the remote release manifest and four image digests remain unchanged.

- [ ] **Step 7: Verify stable live convergence and resume T23**

Use the existing read-only SSM smoke helper to assert Argo is `Synced` and `Healthy` and that all four live images equal `deploy/releases/dev.json`. Repeat the assertion after one refresh interval to prove the replica loop stopped. Then run `make smoke-dev` with fresh Cognito cookies; do not start the worker-replacement drill until smoke passes.

