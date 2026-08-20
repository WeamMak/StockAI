import type { OfferEvidence } from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatQuantity,
  formatRatioPercent,
} from "../presentation";

const VISIBLE_OFFER_COUNT = 3;

function sortOffers(
  offers: OfferEvidence[],
  selectedOfferId: string | null,
): OfferEvidence[] {
  if (selectedOfferId === null) {
    return offers;
  }
  const selected = offers.filter((offer) => offer.offer_id === selectedOfferId);
  const rest = offers.filter((offer) => offer.offer_id !== selectedOfferId);
  return [...selected, ...rest];
}

const REJECTION_LABEL: Record<string, string> = {
  VENDOR_NOT_APPROVED: "Vendor not approved",
  VENDOR_BLOCKED: "Vendor blocked",
  OFFER_NOT_YET_VALID: "Not yet valid",
  OFFER_EXPIRED: "Offer expired",
  DELIVERY_AFTER_NEED_BY: "Delivery too late",
};
const REJECTION_PRIORITY = Object.keys(REJECTION_LABEL);

function statusLabel(offer: OfferEvidence): string {
  const matched = REJECTION_PRIORITY.find((code) =>
    offer.reason_codes.includes(code),
  );
  if (matched) {
    return REJECTION_LABEL[matched];
  }
  return offer.status === "eligible" ? "Eligible" : "Not eligible";
}

function OfferCard({
  offer,
  isSelected,
}: {
  offer: OfferEvidence;
  isSelected: boolean;
}) {
  return (
    <article
      className={`offer-card${isSelected ? " offer-card--selected" : ""}`}
    >
      {isSelected ? (
        <span className="offer-card__selected-badge">AI selected</span>
      ) : null}
      <div className="offer-card__heading">
        <strong>{offer.vendor_name}</strong>
        <span
          className={`status status--${offer.status === "eligible" ? "succeeded" : "failed"}`}
        >
          {statusLabel(offer)}
        </span>
      </div>
      <p className="offer-card__price" title={offer.normalized_cost}>
        {formatCurrency(offer.normalized_cost, offer.company_currency)}
      </p>
      <dl className="offer-metrics">
        <div>
          <dt>Quantity</dt>
          <dd>{formatQuantity(offer.quantity)}</dd>
        </div>
        <div>
          <dt>Delivery</dt>
          <dd>{formatDate(offer.delivery_date)}</dd>
        </div>
      </dl>
      <p className="offer-history">
        On-time rate: {formatRatioPercent(offer.performance.on_time_rate)}
        <br />
        Completed orders: {offer.performance.completed_order_count}
      </p>
    </article>
  );
}

export function OfferComparison({
  offers,
  selectedOfferId,
}: {
  offers: OfferEvidence[];
  selectedOfferId: string | null;
}) {
  if (offers.length === 0) {
    return <p>No vendor offer evidence was available.</p>;
  }
  const sorted = sortOffers(offers, selectedOfferId);
  const visible = sorted.slice(0, VISIBLE_OFFER_COUNT);
  const overflow = sorted.slice(VISIBLE_OFFER_COUNT);

  return (
    <div className="offer-comparison">
      <div
        className="offer-comparison__row"
        role="list"
        aria-label="Vendor offers"
      >
        {visible.map((offer) => (
          <div role="listitem" key={offer.offer_id}>
            <OfferCard
              offer={offer}
              isSelected={offer.offer_id === selectedOfferId}
            />
          </div>
        ))}
      </div>
      {overflow.length > 0 ? (
        <details className="compact-disclosure offer-comparison__overflow">
          <summary>
            View {overflow.length} more{" "}
            {overflow.length === 1 ? "offer" : "offers"}
          </summary>
          <div
            className="offer-comparison__row"
            role="list"
            aria-label="Additional vendor offers"
          >
            {overflow.map((offer) => (
              <div role="listitem" key={offer.offer_id}>
                <OfferCard offer={offer} isSelected={false} />
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
