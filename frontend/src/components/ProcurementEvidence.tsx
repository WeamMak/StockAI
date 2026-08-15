import type { ProcurementEvidence as Evidence } from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatNumber,
  formatQuantity,
  formatRatioPercent,
} from "../presentation";
import { AppliedPreferences } from "./AppliedPreferences";

function label(code: string) {
  return code.replaceAll("_", " ").toLowerCase();
}

export function ProcurementEvidence({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby="evidence-title" className="panel evidence-panel">
      <h2 id="evidence-title">Deterministic procurement evidence</h2>
      <p className="section-intro">
        Expand a section to inspect the exact facts behind the recommendation.
      </p>
      {evidence.map((item) => (
        <article key={item.evidence_id} className="evidence-record">
          <div className="result-heading">
            <div>
              <h3>{item.product_name}</h3>
              <p className="muted identifier">{item.evidence_id}</p>
            </div>
            {item.skip_reason_code ? (
              <span className="status status--failed">
                Skipped: {label(item.skip_reason_code)}
              </span>
            ) : (
              <span className="status status--succeeded">Eligible</span>
            )}
          </div>

          <dl className="evidence-grid evidence-overview">
            <div>
              <dt>Reorder trigger</dt>
              <dd title={item.shortage.reorder_trigger_date ?? undefined}>
                {formatDate(item.shortage.reorder_trigger_date)}
              </dd>
            </div>
            <div>
              <dt>Need by</dt>
              <dd title={item.shortage.need_by_date}>
                {formatDate(item.shortage.need_by_date)}
              </dd>
            </div>
            <div>
              <dt>Coverage</dt>
              <dd>
                {label(item.coverage.status)} ·{" "}
                {formatQuantity(item.coverage.covered_quantity)} covered ·{" "}
                {formatQuantity(item.coverage.residual_quantity)} residual
              </dd>
            </div>
          </dl>

          <div className="evidence-disclosures">
            <details className="disclosure">
              <summary>
                <span>Inventory projection</span>
                <small>{item.shortage.timeline.length} days</small>
              </summary>
              <div className="disclosure__content table-scroll">
                <table className="evidence-timeline">
                  <caption>14-day inventory projection</caption>
                  <thead>
                    <tr>
                      <th scope="col">Date</th>
                      <th scope="col">Projected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.shortage.timeline.map((day) => (
                      <tr key={day.projection_date}>
                        <td title={day.projection_date}>
                          {formatDate(day.projection_date)}
                        </td>
                        <td title={day.quantity}>{formatNumber(day.quantity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>

            <details className="disclosure">
              <summary>
                <span>Vendor offers</span>
                <small>{item.offers.length} considered</small>
              </summary>
              <div className="disclosure__content">
                {item.offers.length === 0 ? (
                  <p>No valid vendor offer evidence was available.</p>
                ) : (
                  <ul
                    className="offer-list"
                    aria-label={`${item.product_name} offers`}
                  >
                    {item.offers.map((offer) => (
                      <li key={offer.offer_id}>
                        <div className="offer-heading">
                          <strong>{offer.vendor_name}</strong>
                          <span
                            className={`status status--${offer.status === "eligible" ? "succeeded" : "failed"}`}
                          >
                            {offer.status}
                          </span>
                        </div>
                        <span>
                          {formatQuantity(offer.quantity)} for{" "}
                          <span title={offer.normalized_cost}>
                            {formatCurrency(
                              offer.normalized_cost,
                              offer.company_currency,
                            )}
                          </span>
                        </span>
                        <span>Delivery {formatDate(offer.delivery_date)}</span>
                        <span>
                          On-time:{" "}
                          {formatRatioPercent(offer.performance.on_time_rate)} ·{" "}
                          {offer.performance.completed_order_count} completed
                          orders · {offer.performance.history_status} history
                        </span>
                        {offer.reason_codes.length > 0 ? (
                          <span>{offer.reason_codes.map(label).join(", ")}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </details>

            {item.budget ? (
              <details className="disclosure">
                <summary>
                  <span>Budget calculation</span>
                  <small>
                    {item.budget.exception_required
                      ? "Exception required"
                      : "Within budget"}
                  </small>
                </summary>
                <div className="disclosure__content">
                  <dl className="evidence-grid">
                    <div>
                      <dt>Budget</dt>
                      <dd>{formatCurrency(item.budget.budget_amount, item.budget.currency)}</dd>
                    </div>
                    <div>
                      <dt>Committed</dt>
                      <dd>{formatCurrency(item.budget.confirmed_commitment, item.budget.currency)}</dd>
                    </div>
                    <div>
                      <dt>Proposed</dt>
                      <dd>{formatCurrency(item.budget.proposed_amount, item.budget.currency)}</dd>
                    </div>
                    <div>
                      <dt>Remaining after</dt>
                      <dd>{formatCurrency(item.budget.remaining_after, item.budget.currency)}</dd>
                    </div>
                  </dl>
                  {item.budget.exception_required ? (
                    <p className="budget-warning">
                      Manager exception required for{" "}
                      {formatCurrency(item.budget.overage, item.budget.currency)}{" "}
                      overage.
                    </p>
                  ) : null}
                </div>
              </details>
            ) : null}

            {item.preferences ? (
              <details className="disclosure">
                <summary>
                  <span>Applied preferences</span>
                  <small>Revision {item.preferences.revision}</small>
                </summary>
                <div className="disclosure__content">
                  <AppliedPreferences preferences={item.preferences} />
                </div>
              </details>
            ) : null}
          </div>
        </article>
      ))}
    </section>
  );
}
