import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  approveCase,
  rejectCase,
  type AcceptedDecision,
  type CaseDetail,
  type Session,
} from "../api/client";
const MAX_MANAGER_TEXT = 280;
type DecisionForm = "idle" | "reject" | "budget_exception";

interface ManagerDecisionPanelProps {
  session: Session;
  caseDetail: CaseDetail;
  onAccepted?: (decision: AcceptedDecision) => void;
}

export function ManagerDecisionPanel({
  session,
  caseDetail,
  onAccepted,
}: ManagerDecisionPanelProps) {
  const [exceptionApproved, setExceptionApproved] = useState(false);
  const [justification, setJustification] = useState("");
  const [reason, setReason] = useState("");
  const [form, setForm] = useState<DecisionForm>("idle");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rejectionReasonRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (form === "reject") {
      rejectionReasonRef.current?.focus();
    }
  }, [form]);

  if (
    session.role !== "manager" ||
    caseDetail.status !== "pending_approval" ||
    caseDetail.draft === null ||
    caseDetail.result?.outcome !== "approval_ready" ||
    caseDetail.result.validation_level !== "t27"
  ) {
    return null;
  }

  const result = caseDetail.result;
  const evidence = caseDetail.evidence.find(
    (item) => item.product_id === result.product_id,
  );
  const offer = evidence?.offers.find((item) => item.offer_id === result.offer_id);
  if (evidence === undefined || offer === undefined) {
    return null;
  }
  const exceptionRequired = evidence.budget?.exception_required === true;
  const approvalEnabled =
    !submitting &&
    (!exceptionRequired || (exceptionApproved && justification.trim().length > 0));

  function openApproval() {
    setError(null);
    if (exceptionRequired) {
      setForm("budget_exception");
      return;
    }
    void submitApproval();
  }

  function cancelApproval() {
    setExceptionApproved(false);
    setJustification("");
    setForm("idle");
  }

  function openRejection() {
    setError(null);
    setForm("reject");
  }

  function cancelRejection() {
    setReason("");
    setForm("idle");
  }

  async function submitApproval() {
    if (!approvalEnabled) return;
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await approveCase(
        caseDetail,
        exceptionRequired,
        exceptionRequired ? justification.trim() : null,
      );
      setNotice("Approval accepted. The fictional Odoo purchase order is being confirmed.");
      onAccepted?.(accepted);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The decision could not be submitted.",
      );
      setSubmitting(false);
    }
  }

  async function submitRejection() {
    const trimmed = reason.trim();
    if (submitting || trimmed.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await rejectCase(caseDetail, trimmed);
      setNotice("Rejection accepted. The fictional Odoo draft is being cancelled.");
      onAccepted?.(accepted);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The decision could not be submitted.",
      );
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="manager-decision-title" className="panel manager-actions-card">
      <div className="manager-actions">
        <div className="manager-actions__copy">
          <p className="eyebrow">Human approval required</p>
          <h2 id="manager-decision-title">Manager actions</h2>
          <p>
            Approve or reject fictional Odoo draft PO #{caseDetail.draft.po_id} at
            revision {caseDetail.draft.write_date}. No supplier is contacted.
          </p>
        </div>
        {form === "idle" ? (
          <div className="manager-actions__buttons">
            <button
              className="primary-button"
              type="button"
              disabled={submitting}
              onClick={openApproval}
            >
              Approve
            </button>
            <button
              className="danger-button"
              type="button"
              disabled={submitting}
              onClick={openRejection}
            >
              Reject
            </button>
          </div>
        ) : null}
      </div>

      {form === "budget_exception" ? (
        <div className="manager-actions__form">
          <h3>Approve budget exception</h3>
          <label className="decision-checkbox">
            <input
              type="checkbox"
              checked={exceptionApproved}
              disabled={submitting}
              onChange={(event) => setExceptionApproved(event.target.checked)}
            />
            Approve budget exception
          </label>
          <label>
            Justification
            <textarea
              maxLength={MAX_MANAGER_TEXT}
              value={justification}
              disabled={submitting}
              onChange={(event) => setJustification(event.target.value)}
            />
          </label>
          <div className="manager-actions__form-actions">
            <button
              className="primary-button"
              type="button"
              disabled={!approvalEnabled}
              onClick={() => void submitApproval()}
            >
              Confirm approval
            </button>
            <button
              className="text-button"
              type="button"
              disabled={submitting}
              onClick={cancelApproval}
            >
              Cancel approval
            </button>
          </div>
        </div>
      ) : null}

      {form === "reject" ? (
        <div className="manager-actions__form">
          <h3>Reject draft</h3>
          <label>
            Rejection reason
            <textarea
              ref={rejectionReasonRef}
              maxLength={MAX_MANAGER_TEXT}
              value={reason}
              disabled={submitting}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="manager-actions__form-actions">
            <button
              className="danger-button"
              type="button"
              disabled={submitting || reason.trim().length === 0}
              onClick={() => void submitRejection()}
            >
              Confirm rejection
            </button>
            <button
              className="text-button"
              type="button"
              disabled={submitting}
              onClick={cancelRejection}
            >
              Cancel rejection
            </button>
          </div>
        </div>
      ) : null}
      <p aria-live="polite" role="status">{notice}</p>
      {error ? <p className="notice notice--error" role="alert">{error}</p> : null}
    </section>
  );
}
