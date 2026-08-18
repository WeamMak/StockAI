import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecommendationHeader } from "../src/components/RecommendationHeader";

const EVIDENCE = {
  environment: "dev" as const,
  evidence_id: "dev:evidence-product-101",
  product_id: "product-101",
  product_name: "Fictional Safety Gloves",
  category_id: "category-safety",
  captured_at: "2026-08-05T10:00:01Z",
  shortage: {
    horizon_start: "2026-08-05",
    horizon_end: "2026-08-19",
    reorder_trigger_date: "2026-08-08",
    need_by_date: "2026-08-12",
    reorder_minimum: "10.000000",
    reorder_maximum: "40.000000",
    minimum_projected_quantity: "0.000000",
    timeline: [],
  },
  coverage: {
    status: "partial" as const,
    covered_quantity: "5.000000",
    residual_quantity: "35.000000",
    source_count: 1,
  },
  offers: [
    {
      offer_id: "offer-101",
      vendor_id: "vendor-101",
      vendor_name: "Fictional Approved Supplies",
      status: "eligible" as const,
      reason_codes: [],
      currency: "USD",
      unit_price: "12.500000",
      company_currency: "USD",
      normalized_unit_price: "12.500000",
      delivery_date: "2026-08-10",
      quantity: "35.000000",
      normalized_cost: "437.500000",
      projected_inventory_after_receipt: "40.000000",
      excess_inventory: "0.000000",
      performance: {
        completed_order_count: 2,
        on_time_rate: "0.500000",
        history_status: "limited" as const,
      },
    },
  ],
  budget: {
    period_start: "2026-08-01",
    currency: "USD",
    budget_amount: "5000.000000",
    confirmed_commitment: "160.000000",
    proposed_amount: "437.500000",
    remaining_before: "4840.000000",
    remaining_after: "4402.500000",
    overage: "0.000000",
    exception_required: false,
  },
  skip_reason_code: null,
  preferences: null,
};

const RESULT = {
  outcome: "approval_ready" as const,
  validation_level: "t27" as const,
  product_id: "product-101",
  product_name: "Fictional Safety Gloves",
  offer_id: "offer-101",
  rationale: "Projected stock is below the reorder minimum.",
  trade_offs: [],
  risk_flags: [],
  uncertainty: "",
  evidence_limitations: [],
  evidence_digest: `sha256:${"a".repeat(64)}`,
  quantity: "35.000000",
  unit_price: "12.500000",
  normalized_cost: "437.500000",
  budget_status: "within_budget" as const,
  preference_profile_id: "preference-3",
  preference_scope: "product" as const,
  preference_revision: 6,
  priority_order: ["price", "reliability", "delivery"] as (
    | "price"
    | "delivery"
    | "reliability"
  )[],
  premium_outcome: "within_cap" as const,
  read_only: true as const,
};

describe("RecommendationHeader", () => {
  it("shows the mockup's 4-card stat row for an approval-ready result", () => {
    render(<RecommendationHeader result={RESULT} evidence={EVIDENCE} />);

    const highlights = screen.getByRole("region", { name: "Decision highlights" });
    expect(highlights).toHaveTextContent("Offers considered1 eligible1 total reviewed");
    expect(highlights).toHaveTextContent("Uncovered target gap35 unitsAt Aug 12, 2026 stockout");
    expect(highlights).toHaveTextContent("Recommended vendorFictional Approved Supplies$437.50");
    expect(highlights).toHaveTextContent("Budget statusWithin budget$4,402.50 remaining");
  });

  it("omits the stat row when no evidence is available", () => {
    render(<RecommendationHeader result={RESULT} evidence={null} />);

    expect(
      screen.queryByRole("region", { name: "Decision highlights" }),
    ).not.toBeInTheDocument();
  });

  it("shows the manual-review badge and fallback title", () => {
    render(
      <RecommendationHeader
        result={{
          outcome: "manual_review",
          rationale: "x",
          trade_offs: [],
          risk_flags: [],
          uncertainty: "x",
          evidence_limitations: [],
          read_only: true,
        }}
        evidence={null}
      />,
    );

    expect(screen.getByText("Manual review")).toBeInTheDocument();
    expect(screen.getByText("No draft created")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Compare eligible offers" }),
    ).toBeInTheDocument();
  });
});
