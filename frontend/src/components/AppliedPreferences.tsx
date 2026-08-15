import type { AppliedPreferences as Preferences } from "../api/client";
import { formatNumber, formatPercent } from "../presentation";

function label(value: string) {
  return value.replaceAll("_", " ");
}

export function AppliedPreferences({ preferences }: { preferences: Preferences }) {
  return (
    <section aria-labelledby={`preferences-${preferences.profile_id}`}>
      <h4 id={`preferences-${preferences.profile_id}`}>Applied preferences</h4>
      <dl className="evidence-grid">
        <div><dt>Source</dt><dd>{preferences.scope} {preferences.scope_id}</dd></div>
        <div><dt>Revision</dt><dd>{preferences.revision}</dd></div>
        <div><dt>Priority</dt><dd>{preferences.ordered_criteria.join(" → ")}</dd></div>
        <div>
          <dt>Price premium</dt>
          <dd title={preferences.max_price_premium_percent}>
            {formatPercent(preferences.max_price_premium_percent)} ({preferences.enforcement_mode})
          </dd>
        </div>
        <div>
          <dt>Cheapest eligible cost</dt>
          <dd title={preferences.cheapest_eligible_cost}>
            {formatNumber(preferences.cheapest_eligible_cost)}
          </dd>
        </div>
      </dl>
      <ul className="tag-list" aria-label="Offer premium results">
        {preferences.offer_results.map((result) => (
          <li key={result.offer_id}>
            {result.offer_id}: {formatPercent(result.premium_percent)} — {label(result.outcome)}
          </li>
        ))}
      </ul>
    </section>
  );
}
