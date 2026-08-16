import type {
  OfferEvidence,
  ProcurementEvidence as Evidence,
} from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  formatNumber,
  formatQuantity,
  formatRatioPercent,
} from "../presentation";
import { AppliedPreferences } from "./AppliedPreferences";
import { InventoryChart } from "./InventoryChart";

function label(code: string) {
  return code.replaceAll("_", " ").toLowerCase();
}

function OfferList({
  accessibleName,
  offers,
  onlyEligible = false,
}: {
  accessibleName: string;
  offers: OfferEvidence[];
  onlyEligible?: boolean;
}) {
  return (
    <ul className="offer-list" aria-label={accessibleName}>
      {offers.map((offer) => (
        <li key={offer.offer_id}>
          <div className="offer-heading">
            <strong>{offer.vendor_name}</strong>
            <span
              className={`status status--${offer.status === "eligible" ? "succeeded" : "failed"}`}
            >
              {onlyEligible ? "Only eligible offer" : offer.status}
            </span>
          </div>
          <dl className="offer-metrics">
            <div><dt>Price</dt><dd title={offer.normalized_cost}>{formatCurrency(offer.normalized_cost, offer.company_currency)}</dd></div>
            <div><dt>Quantity</dt><dd>{formatQuantity(offer.quantity)}</dd></div>
            <div><dt>Delivery</dt><dd>{formatDate(offer.delivery_date)}</dd></div>
          </dl>
          <p className="offer-history">
            On-time: {formatRatioPercent(offer.performance.on_time_rate)} ·{" "}
            {offer.performance.completed_order_count} completed orders ·{" "}
            {offer.performance.history_status} history
          </p>
          {offer.reason_codes.length > 0 ? (
            <p className="rejection-reason">
              {offer.reason_codes.map(label).join(", ")}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
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

          <div className="evidence-detail-grid" role="region" aria-label="Evidence details">
          <div className="evidence-main">
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

          <div className="evidence-primary-grid">
            <section className="evidence-card" aria-labelledby={`inventory-${item.evidence_id}`}>
              <div className="evidence-card__heading">
                <div>
                  <h4 id={`inventory-${item.evidence_id}`}>Inventory projection</h4>
                  <p>{formatDate(item.shortage.horizon_start)} – {formatDate(item.shortage.horizon_end)}</p>
                </div>
                <span className="evidence-count">{item.shortage.timeline.length} days</span>
              </div>
              <InventoryChart
                reorderMinimum={item.shortage.reorder_minimum}
                timeline={item.shortage.timeline}
              />
              <details className="compact-disclosure">
                <summary>View daily values</summary>
                <div className="table-scroll">
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
            </section>

            <section className="evidence-card" aria-labelledby={`offers-${item.evidence_id}`}>
              <div className="evidence-card__heading">
                <div>
                  <h4 id={`offers-${item.evidence_id}`}>Vendor offers</h4>
                  <p>Eligible offers are shown first.</p>
                </div>
                <span className="evidence-count">{item.offers.length} considered</span>
              </div>
              {item.offers.filter((offer) => offer.status === "eligible").length === 0 ? (
                  <p>No valid vendor offer evidence was available.</p>
                ) : (
                  <OfferList
                    accessibleName={`${item.product_name} eligible offers`}
                    offers={item.offers.filter((offer) => offer.status === "eligible")}
                    onlyEligible={item.offers.filter((offer) => offer.status === "eligible").length === 1}
                  />
                )}
              {item.offers.some((offer) => offer.status === "rejected") ? (
                <details className="compact-disclosure rejected-offers">
                  <summary>
                    View rejected offers ({item.offers.filter((offer) => offer.status === "rejected").length})
                  </summary>
                  <OfferList
                    accessibleName={`${item.product_name} rejected offers`}
                    offers={item.offers.filter((offer) => offer.status === "rejected")}
                  />
                </details>
              ) : null}
            </section>
          </div>
          </div>

          <aside className="evidence-disclosures" aria-label="Evidence policy details">

            {item.budget ? (
              <details className="disclosure" open>
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
              <details className="disclosure" open>
                <summary>
                  <span>Applied preferences</span>
                  <small>Revision {item.preferences.revision}</small>
                </summary>
                <div className="disclosure__content">
                  <AppliedPreferences preferences={item.preferences} />
                </div>
              </details>
            ) : null}
          </aside>
          </div>
          <footer className="evidence-footer">
            <span className="identifier">Evidence ID: {item.evidence_id}</span>
            <span>Captured {formatDateTime(item.captured_at)}</span>
          </footer>
        </article>
      ))}
    </section>
  );
}
