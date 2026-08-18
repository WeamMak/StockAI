import type { ProcurementEvidence as Evidence } from "../api/client";
import { formatCurrency } from "../presentation";

export function BudgetPanel({
  budget,
}: {
  budget: Evidence["budget"];
}) {
  if (budget === null) {
    return null;
  }
  return (
    <section className="panel budget-panel" aria-labelledby="budget-panel-title">
      <div className="budget-panel__heading">
        <h4 id="budget-panel-title">Budget calculation</h4>
        <span
          className={
            budget.exception_required
              ? "budget-panel__status budget-panel__status--exception"
              : "budget-panel__status"
          }
        >
          {budget.exception_required ? "Exception required" : "Within budget"}
        </span>
      </div>
      <dl className="evidence-grid">
        <div>
          <dt>Budget</dt>
          <dd>{formatCurrency(budget.budget_amount, budget.currency)}</dd>
        </div>
        <div>
          <dt>Committed</dt>
          <dd>{formatCurrency(budget.confirmed_commitment, budget.currency)}</dd>
        </div>
        <div>
          <dt>Proposed</dt>
          <dd>{formatCurrency(budget.proposed_amount, budget.currency)}</dd>
        </div>
        <div>
          <dt>Remaining after</dt>
          <dd>{formatCurrency(budget.remaining_after, budget.currency)}</dd>
        </div>
      </dl>
      {budget.exception_required ? (
        <p className="budget-warning">
          Manager exception required for{" "}
          {formatCurrency(budget.overage, budget.currency)} overage.
        </p>
      ) : null}
    </section>
  );
}
