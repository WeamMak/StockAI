import { useState } from "react";

import {
  ApiError,
  approveCase,
  rejectCase,
  type AcceptedDecision,
  type CaseDetail,
  type Session,
} from "../api/client";
import { formatCurrency, formatQuantity } from "../presentation";

const MAX_MANAGER_TEXT = 280;

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
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    <section aria-labelledby="manager-decision-title" className="panel decision-panel">
      <h2 id="manager-decision-title">Manager decision</h2>
      <p>
        This decision applies only to fictional Odoo draft PO #{caseDetail.draft.po_id}
        {" "}at revision {caseDetail.draft.write_date}. It does not contact a supplier.
      </p>
      <dl className="decision-binding">
        <div><dt>Vendor</dt><dd>{offer.vendor_name} ({offer.vendor_id})</dd></div>
        <div><dt>Quantity</dt><dd>{formatQuantity(result.quantity)}</dd></div>
        <div><dt>Amount</dt><dd>{formatCurrency(result.normalized_cost, offer.currency)}</dd></div>
        <div><dt>Evidence digest</dt><dd className="identifier">{result.evidence_digest}</dd></div>
        <div><dt>Remaining budget</dt><dd>{evidence.budget ? formatCurrency(evidence.budget.remaining_after, evidence.budget.currency) : "Unavailable"}</dd></div>
        <div><dt>Overage</dt><dd>{evidence.budget ? formatCurrency(evidence.budget.overage, evidence.budget.currency) : "Unavailable"}</dd></div>
      </dl>

      <div className="decision-forms">
        <section aria-labelledby="approval-title">
          <h3 id="approval-title">Approve</h3>
          {exceptionRequired ? (
            <>
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
            </>
          ) : null}
          <button
            className="primary-button"
            type="button"
            disabled={!approvalEnabled}
            onClick={() => void submitApproval()}
          >
            Approve and confirm
          </button>
        </section>

        <section aria-labelledby="rejection-title">
          <h3 id="rejection-title">Reject</h3>
          <label>
            Rejection reason
            <textarea
              maxLength={MAX_MANAGER_TEXT}
              value={reason}
              disabled={submitting}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button
            className="secondary-button"
            type="button"
            disabled={submitting || reason.trim().length === 0}
            onClick={() => void submitRejection()}
          >
            Reject and cancel draft
          </button>
        </section>
      </div>
      <p aria-live="polite" role="status">{notice}</p>
      {error ? <p className="notice notice--error" role="alert">{error}</p> : null}
    </section>
  );
}
