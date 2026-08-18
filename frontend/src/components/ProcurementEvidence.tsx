import type { ProcurementEvidence as Evidence } from "../api/client";
import {
  formatDate,
  formatDateTime,
  formatNumber,
  formatQuantity,
} from "../presentation";
import { AppliedPreferences } from "./AppliedPreferences";
import { BudgetPanel } from "./BudgetPanel";
import { InventoryChart } from "./InventoryChart";
import { OfferComparison } from "./OfferComparison";

function label(code: string) {
  return code.replaceAll("_", " ").toLowerCase();
}

export function ProcurementEvidence({
  evidence,
  selectedOfferId,
}: {
  evidence: Evidence;
  selectedOfferId: string | null;
}) {
  return (
    <section aria-labelledby="evidence-title" className="panel evidence-panel">
      <h2 id="evidence-title">Deterministic procurement evidence</h2>
      <p className="section-intro">
        Grounded facts, calculations, and policy configuration used to arrive
        at this recommendation.
      </p>
      <article className="evidence-record">
        <div className="result-heading">
          <div>
            <h3>{evidence.product_name}</h3>
            <p className="muted identifier">{evidence.evidence_id}</p>
          </div>
          {evidence.skip_reason_code ? (
            <span className="status status--failed">
              Skipped: {label(evidence.skip_reason_code)}
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
            <dd title={evidence.shortage.reorder_trigger_date ?? undefined}>
              {formatDate(evidence.shortage.reorder_trigger_date)}
            </dd>
          </div>
          <div>
            <dt>Need by</dt>
            <dd title={evidence.shortage.need_by_date}>
              {formatDate(evidence.shortage.need_by_date)}
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              {label(evidence.coverage.status)} ·{" "}
              {formatQuantity(evidence.coverage.covered_quantity)} covered ·{" "}
              {formatQuantity(evidence.coverage.residual_quantity)} residual
            </dd>
          </div>
        </dl>

        <div className="evidence-primary-grid">
          <section className="evidence-card" aria-labelledby={`inventory-${evidence.evidence_id}`}>
            <div className="evidence-card__heading">
              <div>
                <h4 id={`inventory-${evidence.evidence_id}`}>Inventory projection</h4>
                <p>{formatDate(evidence.shortage.horizon_start)} – {formatDate(evidence.shortage.horizon_end)}</p>
              </div>
              <span className="evidence-count">{evidence.shortage.timeline.length} days</span>
            </div>
            <InventoryChart
              reorderMinimum={evidence.shortage.reorder_minimum}
              timeline={evidence.shortage.timeline}
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
                  {evidence.shortage.timeline.map((day) => (
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

          <section className="evidence-card" aria-labelledby={`offers-${evidence.evidence_id}`}>
            <div className="evidence-card__heading">
              <div>
                <h4 id={`offers-${evidence.evidence_id}`}>Vendor offers</h4>
                <p>The AI-selected offer is shown first.</p>
              </div>
              <span className="evidence-count">{evidence.offers.length} considered</span>
            </div>
            <OfferComparison
              offers={evidence.offers}
              selectedOfferId={selectedOfferId}
            />
          </section>
        </div>
        </div>

        <aside className="evidence-panels" aria-label="Evidence policy details">

          <BudgetPanel budget={evidence.budget} />

          {evidence.preferences ? (
            <AppliedPreferences preferences={evidence.preferences} />
          ) : null}
        </aside>
        </div>
        <footer className="evidence-footer">
          <span className="identifier">Evidence ID: {evidence.evidence_id}</span>
          <span>Captured {formatDateTime(evidence.captured_at)}</span>
        </footer>
      </article>
    </section>
  );
}
