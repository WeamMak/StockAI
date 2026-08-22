import { useState } from "react";

import {
  ApiError,
  submitCaseForDraft,
  type AcceptedDraftSubmission,
  type CaseDetail,
} from "../api/client";

interface DraftSubmissionPanelProps {
  caseDetail: CaseDetail;
  onSubmitted: (accepted: AcceptedDraftSubmission) => void;
}

function storageKey(caseDetail: CaseDetail): string {
  return `stockai:draft:${caseDetail.case_id}:${caseDetail.revision}`;
}

function idempotencyKey(caseDetail: CaseDetail): string {
  const key = storageKey(caseDetail);
  const existing = sessionStorage.getItem(key);
  if (existing !== null) {
    return existing;
  }
  const created = `draft-${crypto.randomUUID()}`;
  sessionStorage.setItem(key, created);
  return created;
}

export function DraftSubmissionPanel({
  caseDetail,
  onSubmitted,
}: DraftSubmissionPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await submitCaseForDraft(
        caseDetail,
        idempotencyKey(caseDetail),
      );
      onSubmitted(accepted);
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "The draft request could not be completed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="draft-submission-panel" aria-labelledby="draft-action-title">
      <div>
        <h3 id="draft-action-title">Ready to hand off?</h3>
        <p>
          This locks this recommendation for refinement, creates one fictional
          Odoo draft, and sends it to a manager for a decision.
        </p>
      </div>
      <button
        aria-busy={submitting}
        className="primary-button draft-submission-button"
        disabled={submitting}
        type="button"
        onClick={() => void submit()}
      >
        {submitting ? "Creating draft…" : "Create draft and send to manager"}
      </button>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </section>
  );
}
