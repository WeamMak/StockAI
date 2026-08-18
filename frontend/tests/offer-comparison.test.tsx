import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OfferComparison } from "../src/components/OfferComparison";
import type { OfferEvidence } from "../src/api/client";

function makeOffer(overrides: Partial<OfferEvidence>): OfferEvidence {
  return {
    offer_id: "offer-x",
    vendor_id: "vendor-x",
    vendor_name: "Vendor X",
    status: "eligible",
    reason_codes: [],
    currency: "USD",
    unit_price: "10.000000",
    company_currency: "USD",
    normalized_unit_price: "10.000000",
    delivery_date: "2026-08-18",
    quantity: "12.000000",
    normalized_cost: "120.000000",
    projected_inventory_after_receipt: "12.000000",
    excess_inventory: "0.000000",
    performance: {
      completed_order_count: 2,
      on_time_rate: "1.000000",
      history_status: "limited",
    },
    ...overrides,
  };
}

describe("OfferComparison", () => {
  it("sorts the selected offer first and marks it as AI selected", () => {
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({ offer_id: "offer-b", vendor_name: "Vendor B" }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId="offer-b" />);

    const cards = screen.getAllByRole("listitem");
    expect(cards[0]).toHaveTextContent("Vendor B");
    expect(cards[0]).toHaveTextContent("AI selected");
    expect(cards[1]).toHaveTextContent("Vendor A");
    expect(cards[1]).not.toHaveTextContent("AI selected");
  });

  it("shows a not-eligible badge with a vendor-not-approved reason inline", () => {
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({
        offer_id: "offer-c",
        vendor_name: "Vendor C",
        status: "rejected",
        reason_codes: ["VENDOR_NOT_APPROVED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId="offer-a" />);

    expect(screen.getByText("Vendor not approved")).toBeInTheDocument();
  });

  it("labels a vendor-blocked rejection", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-c",
        status: "rejected",
        reason_codes: ["VENDOR_BLOCKED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Vendor blocked")).toBeInTheDocument();
  });

  it("labels an offer-not-yet-valid rejection", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-c",
        status: "rejected",
        reason_codes: ["OFFER_NOT_YET_VALID"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Not yet valid")).toBeInTheDocument();
  });

  it("labels an offer-expired rejection", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-c",
        status: "rejected",
        reason_codes: ["OFFER_EXPIRED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Offer expired")).toBeInTheDocument();
  });

  it("labels a delivery-after-need-by rejection", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-c",
        status: "rejected",
        reason_codes: ["DELIVERY_AFTER_NEED_BY"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Delivery too late")).toBeInTheDocument();
  });

  it("labels the first matching reason in priority order for multi-reason offers", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-multi",
        status: "rejected",
        reason_codes: ["OFFER_EXPIRED", "VENDOR_BLOCKED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Vendor blocked")).toBeInTheDocument();
    expect(screen.queryByText("Offer expired")).not.toBeInTheDocument();
  });

  it("caps visible cards at 3 and collapses the rest", async () => {
    const user = userEvent.setup();
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({ offer_id: "offer-b", vendor_name: "Vendor B" }),
      makeOffer({ offer_id: "offer-c", vendor_name: "Vendor C" }),
      makeOffer({ offer_id: "offer-d", vendor_name: "Vendor D" }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    const visibleRow = screen.getByRole("list", { name: "Vendor offers" });
    expect(within(visibleRow).getAllByRole("listitem")).toHaveLength(3);
    expect(within(visibleRow).queryByText("Vendor D")).not.toBeInTheDocument();

    await user.click(screen.getByText("View 1 more offer"));
    expect(screen.getByText("Vendor D")).toBeInTheDocument();
  });
});
