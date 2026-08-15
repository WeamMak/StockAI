import type { ProcurementEvidence as Evidence } from "../api/client";
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
      {evidence.map((item) => (
        <article key={item.evidence_id} className="evidence-record">
          <div className="result-heading">
            <div>
              <h3>{item.product_name}</h3>
              <p className="muted">{item.evidence_id}</p>
            </div>
            {item.skip_reason_code ? (
              <span className="status status--failed">
                Skipped: {label(item.skip_reason_code)}
              </span>
            ) : (
              <span className="status status--succeeded">Eligible</span>
            )}
          </div>
          <dl className="evidence-grid">
            <div>
              <dt>Reorder trigger</dt>
              <dd>{item.shortage.reorder_trigger_date ?? "None"}</dd>
            </div>
            <div>
              <dt>Need by</dt>
              <dd>{item.shortage.need_by_date}</dd>
            </div>
            <div>
              <dt>Coverage</dt>
              <dd>
                {item.coverage.status} ({item.coverage.covered_quantity} covered,
                {" "}{item.coverage.residual_quantity} residual)
              </dd>
            </div>
          </dl>
          <table className="evidence-timeline">
            <caption>14-day inventory projection</caption>
            <thead>
              <tr><th scope="col">Date</th><th scope="col">Projected</th></tr>
            </thead>
            <tbody>
              {item.shortage.timeline.map((day) => (
                <tr key={day.projection_date}>
                  <td>{day.projection_date}</td><td>{day.quantity}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {item.offers.length === 0 ? (
            <p>No valid vendor offer evidence was available.</p>
          ) : (
            <ul className="offer-list" aria-label={`${item.product_name} offers`}>
              {item.offers.map((offer) => (
                <li key={offer.offer_id}>
                  <strong>{offer.vendor_name}</strong>
                  <span>
                    {offer.quantity} for {offer.normalized_cost}{" "}
                    {offer.company_currency}; delivery {offer.delivery_date}
                  </span>
                  <span>
                    On-time: {offer.performance.on_time_rate ?? "no history"} ({offer.performance.completed_order_count}{" "}
                    completed orders, {offer.performance.history_status})
                  </span>
                  {offer.reason_codes.length > 0 ? (
                    <span>{offer.reason_codes.map(label).join(", ")}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          {item.budget ? (
            <p>
              Budget remaining after: {item.budget.remaining_after}{" "}
              {item.budget.currency}
              {item.budget.exception_required
                ? `; manager exception required for ${item.budget.overage} ${item.budget.currency} overage`
                : ""}
            </p>
          ) : null}
          {item.preferences ? (
            <AppliedPreferences preferences={item.preferences} />
          ) : null}
        </article>
      ))}
    </section>
  );
}
