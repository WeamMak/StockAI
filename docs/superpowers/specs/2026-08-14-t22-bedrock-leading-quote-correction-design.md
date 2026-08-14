# T22 Bedrock Leading-Quote Correction Design

## Status and scope

This is a bounded correction to the accepted T22 API artifact, approved by the
user on 2026-08-14 after the first T23 live smoke. It changes only the Amazon
Bedrock structured-response decoding path for one observed GPT-OSS defect. It
does not change the model, prompt, JSON schema, semantic validator, retry
limits, deterministic product-eligibility checks, or safe
`LLM_OUTPUT_INVALID` fallback.

The live response contained one `reasoningContent` block, which remains
ignored, and one final text block. The text block was an otherwise valid JSON
object with exactly one stray leading double quote immediately before its
opening brace and no trailing quote. No raw reasoning content is retained.

## Selected correction

The Bedrock response decoder first performs the existing strict
`json.loads(text)`. If and only if that fails with `JSONDecodeError`, it may
remove exactly one leading `"` when all of these conditions hold:

1. the first two characters are exactly `"{`;
2. after removing trailing JSON whitespace, the structured response ends at
   `}`; and
3. the response does not contain a matching trailing quote or any text after
   the closing brace.

The decoder then calls the same `json.loads` once on the one-character-normalized
text. The result must still be a mapping and must pass the existing unchanged
semantic validator. Any other malformed output follows the existing single
provider repair attempt and then raises the same sanitized
`LlmOutputInvalidError`.

This is not general JSON repair. The decoder will not search for an embedded
object, remove code fences, remove prefixes or suffixes, balance delimiters,
fix quoting inside the object, accept trailing text, coerce types, fill missing
fields, or alter identifiers.

## Test seam

The public seam is `BedrockStructuredLlm.recommend()` with the existing fake
Converse client. A red regression reproduces the sanitized live envelope with
an ignored `reasoningContent` block and the exact leading-quote defect. It must
return the schema- and evidence-validated recommendation without a provider
repair call.

Negative cases keep the existing safe behavior for:

- a fenced JSON object;
- trailing text after the closing brace;
- a matching trailing quote;
- malformed JSON inside the object;
- missing or extra schema fields; and
- an ineligible product identifier.

These cases must not become recommendations. They retain one bounded provider
repair attempt and then the existing `LlmOutputInvalidError` when the repair is
also invalid.

## Release and live verification

The normal T22 dev workflow remains authoritative. A relevant dev source push
builds all four project images, produces a new content identity and immutable
release manifest, updates the exact four dev overlay digests, and lets Argo CD
reconcile the desired state. There is no manual image deployment, partial
release, mutable tag, prior-manifest rewrite, or direct `kubectl` deployment.

After Argo reports the new release Synced and Healthy with all four exact
digests, T23 restarts against that release. The authenticated smoke must pass
HTTPS, Cognito, FastAPI/LangGraph, Bedrock, MCP, Odoo, DynamoDB, metrics, and
sanitized Loki/S3 checks before validation evidence is appended. The worker
replacement drill remains blocked until this exact-release smoke passes and
still requires explicit approval immediately before dev worker termination.

## Failure and rollback behavior

If unit negatives broaden, the build fails, the release cannot be verified,
Argo does not converge, or the live response still fails strict decoding or
semantic validation, stop T23 and preserve the prior release evidence. Do not
weaken validation, add a permissive repair dependency, patch the running pod,
or run the worker drill.
