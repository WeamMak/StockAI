import { useState } from "react";

import { ApiError, refineCase, type CaseDetail } from "../api/client";

const MAX_NOTE_LENGTH = 280;
const MAX_REFINEMENTS = 3;

interface RefinementPanelProps {
  scanId: string;
  caseId: string;
  refinementCount: number;
  onRefined: (scan: CaseDetail) => void;
}

export function RefinementPanel({
  scanId,
  caseId,
  refinementCount,
  onRefined,
}: RefinementPanelProps) {
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const atLimit = refinementCount >= MAX_REFINEMENTS;

  async function submit() {
    const trimmed = note.trim();
    if (trimmed.length === 0 || trimmed.length > MAX_NOTE_LENGTH) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const nextScan = await refineCase(scanId, caseId, trimmed);
      setNote("");
      onRefined(nextScan);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The request could not be completed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="refinement-title" className="panel refinement-panel">
      <h3 id="refinement-title">Refine this recommendation</h3>
      {atLimit ? (
        <p className="refinement-limit">
          Refinement limit reached (3/3). Run a new scan for a fresh recommendation.
        </p>
      ) : (
        <>
          <p className="refinement-hint">
            Add situational context, such as favoring delivery speed or avoiding a
            vendor for a temporary reason, and get this case re-evaluated.
          </p>
          <textarea
            aria-label="Refinement note"
            maxLength={MAX_NOTE_LENGTH}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            disabled={submitting}
          />
          <div className="refinement-controls">
            <span className="refinement-count">
              {refinementCount} of {MAX_REFINEMENTS} refinements used
            </span>
            <button
              className="primary-button"
              type="button"
              onClick={() => void submit()}
              disabled={submitting || note.trim().length === 0}
              aria-busy={submitting}
            >
              {submitting ? "Submitting…" : "Submit refinement"}
            </button>
          </div>
          {error ? (
            <p className="notice notice--error" role="alert">
              {error}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
