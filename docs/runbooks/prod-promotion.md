# Exact dev-to-prod release promotion

## Purpose

This T24 procedure promotes the complete immutable release already validated in
dev. It never builds or retags an image and never deploys a workload directly.
Git is the desired-state boundary, and Argo CD is the only production
reconciler.

Stop immediately on a dirty or protected branch, failed or rewritten dev
evidence, malformed release metadata, application-content mismatch, unexpected
file change, failed check, Argo revision mismatch, or deployed-image digest
mismatch.

## Prepare the feature branch

1. Start from the T24 feature branch created from the latest protected `main`.
2. Confirm `origin/dev` contains the passed T23 release and the worktree is
   clean.
3. Run `make promote-dev`. The command fetches `origin/dev`, verifies the
   current release and its append-only validation history, compares the
   application-content identity, renders both overlays in a temporary
   workspace, and leaves only the prod overlay and release record unstaged.
4. Run `make verify-release` and `make kubernetes-validate`.
5. Compare the parsed `images` and `provenance` maps in
   `deploy/releases/dev.json` and `deploy/releases/prod.json`. They must be
   byte-for-byte identical.
6. Review the complete diff. No source, dev desired state, infrastructure,
   secret value, or unrelated file may change as a side effect of promotion.

Commit the reviewed prod desired state on the feature branch and open its pull
request to `main`. Required tests, release checks, Kustomize validation, secret
scanning, and report-only Docker Scout evidence must finish before merge.
Merging the reviewed pull request is the explicit production decision.

## Reconciliation and smoke

After the protected merge, verify that the main workflow accepts the exact
passed dev release without changing it. Observe `stockai-prod` until Argo CD
reports the merged main revision as `Synced` and `Healthy`. Confirm every
deployed project image ends in the corresponding promoted digest.

Authenticate through the fictional prod Cognito account, keep the fresh
session and CSRF values out of shell history and chat, and run
`make smoke-prod`. The repeatable production smoke is non-mutating: it accepts
any valid terminal Procurement Case outcome and proves the public API,
LangGraph routing, MCP/Odoo reads, DynamoDB persistence, metrics, and logs
without requiring data-dependent Bedrock reasoning or creating a draft purchase
order. Bedrock, preference, and write-path proof remain part of dev validation
and deterministic integration coverage. Preserve only the sanitized evidence
file and its SHA-256 digest. A failed smoke is a release failure; diagnose it
without changing live desired state manually.

## Rollback

Rollback uses a Git revert of the faulty production desired-state commit to a
previously verified prod release. Before merging the revert, verify the old
release record, provenance, and exact immutable digest map. Let Argo CD
reconcile that reviewed Git state, then repeat production health and smoke
checks. Never reconstruct an old release from a mutable reference.
