import type { AppliedPreferences as Preferences } from "../api/client";
import { formatNumber, formatPercent } from "../presentation";
import { Icon } from "./Icon";

function label(value: string) {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function AppliedPreferences({ preferences }: { preferences: Preferences }) {
  return (
    <section
      aria-labelledby={`preferences-${preferences.profile_id}`}
      className="preferences-panel"
    >
      <div className="preferences-heading">
        <div>
          <span className="summary-icon summary-icon--blue">
            <Icon name="preferences" />
          </span>
          <h4 id={`preferences-${preferences.profile_id}`}>
            Applied preferences
          </h4>
        </div>
        <div className="policy-badges">
          <span>{label(preferences.scope)} scope</span>
          <span>Revision {preferences.revision}</span>
          <span>{label(preferences.enforcement_mode)} enforcement</span>
        </div>
      </div>

      <div className="preference-layout">
        <section aria-labelledby={`priority-${preferences.profile_id}`}>
          <p className="field-label" id={`priority-${preferences.profile_id}`}>
            Decision priority
          </p>
          <ol className="preference-priority" aria-label="Preference priority">
            {preferences.ordered_criteria.map((criterion, index) => (
              <li key={criterion}>
                <span>{index + 1}</span>
                <strong>{label(criterion)}</strong>
              </li>
            ))}
          </ol>
        </section>

        <dl className="preference-policy">
          <div>
            <dt>Maximum premium</dt>
            <dd title={preferences.max_price_premium_percent}>
              {formatPercent(preferences.max_price_premium_percent)}
            </dd>
          </div>
          <div>
            <dt>Baseline eligible cost</dt>
            <dd title={preferences.cheapest_eligible_cost}>
              {formatNumber(preferences.cheapest_eligible_cost)}
            </dd>
          </div>
          <div>
            <dt>Applied source</dt>
            <dd>{preferences.scope_id}</dd>
          </div>
        </dl>
      </div>

      <div className="preference-results">
        <p className="field-label">Offer policy outcomes</p>
        <ul aria-label="Offer premium results">
          {preferences.offer_results.map((result) => (
            <li key={result.offer_id}>
              <span className="identifier">{result.offer_id}</span>
              <strong>{formatPercent(result.premium_percent)}</strong>
              <span className={`policy-outcome policy-outcome--${result.outcome}`}>
                {label(result.outcome)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
