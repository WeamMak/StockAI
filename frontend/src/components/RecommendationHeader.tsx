import type {
  ProcurementEvidence as Evidence,
  ScanResult,
} from "../api/client";
import { formatCurrency, formatDate, formatQuantity } from "../presentation";
import { Icon } from "./Icon";

function badgeCopy(result: ScanResult): { label: string; readOnly: string } {
  if (result.outcome === "manual_review") {
    return { label: "Manual review", readOnly: "No draft created" };
  }
  if (result.outcome === "no_valid_offer") {
    return { label: "No valid offer", readOnly: "No draft created" };
  }
  if (result.validation_level === "legacy") {
    return {
      label: "Historical recommendation",
      readOnly: "Read-only recommendation",
    };
  }
  return { label: "Approval ready", readOnly: "Read-only recommendation" };
}

function titleCopy(result: ScanResult): {
  title: string;
  subtitle: string | null;
} {
  if (result.outcome === "manual_review") {
    return { title: "Compare eligible offers", subtitle: null };
  }
  return { title: result.product_name, subtitle: result.product_id };
}

export function RecommendationHeader({
  result,
  evidence,
}: {
  result: ScanResult;
  evidence: Evidence | null;
}) {
  const { label, readOnly } = badgeCopy(result);
  const { title, subtitle } = titleCopy(result);
  const selectedOffer =
    evidence && result.outcome === "approval_ready" && result.offer_id !== null
      ? evidence.offers.find((offer) => offer.offer_id === result.offer_id) ??
        null
      : null;
  const eligibleCount =
    evidence?.offers.filter((offer) => offer.status === "eligible").length ?? 0;
  const totalCount = evidence?.offers.length ?? 0;

  return (
    <div className="recommendation-overview">
      <div className="result-heading">
        <div>
          <p className="approval-label">
            <span className="summary-icon summary-icon--green">
              <Icon name="coverage" />
            </span>
            {label}
          </p>
          <h2>{title}</h2>
          {subtitle ? <p className="muted identifier">{subtitle}</p> : null}
        </div>
        <span className="read-only-badge">{readOnly}</span>
      </div>

      {evidence ? (
        <section aria-label="Decision highlights">
          <dl className="decision-grid">
            <div className="decision-card decision-card--offers">
              <dt>
                <span className="summary-icon summary-icon--blue">
                  <Icon name="offer" />
                </span>
                Offers considered
              </dt>
              <dd>
                {eligibleCount} eligible
                <small>{totalCount} total reviewed</small>
              </dd>
            </div>
            <div className="decision-card decision-card--shortage">
              <dt>
                <span className="summary-icon summary-icon--amber">
                  <Icon name="shortage" />
                </span>
                Uncovered target gap
              </dt>
              <dd title={evidence.coverage.residual_quantity}>
                {formatQuantity(evidence.coverage.residual_quantity)}
                <small>
                  At {formatDate(evidence.shortage.need_by_date)} stockout
                </small>
              </dd>
            </div>
            <div className="decision-card decision-card--vendor">
              <dt>
                <span className="summary-icon summary-icon--green">
                  <Icon name="recommendation" />
                </span>
                Recommended vendor
              </dt>
              <dd>
                {selectedOffer ? selectedOffer.vendor_name : "Not available"}
                <small>
                  {selectedOffer
                    ? formatCurrency(
                        selectedOffer.normalized_cost,
                        selectedOffer.company_currency,
                      )
                    : "No offer selected"}
                </small>
              </dd>
            </div>
            <div className="decision-card decision-card--budget">
              <dt>
                <span className="summary-icon summary-icon--blue">
                  <Icon name="document" />
                </span>
                Budget status
              </dt>
              <dd>
                {evidence.budget
                  ? evidence.budget.exception_required
                    ? "Exception required"
                    : "Within budget"
                  : "Not available"}
                <small>
                  {evidence.budget
                    ? `${formatCurrency(evidence.budget.remaining_after, evidence.budget.currency)} remaining`
                    : "Budget not available"}
                </small>
              </dd>
            </div>
          </dl>
        </section>
      ) : null}
    </div>
  );
}
