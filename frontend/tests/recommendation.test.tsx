import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecommendationPage } from "../src/pages/RecommendationPage";

const BASE_SCAN = {
  scan_id: "scan-101",
  case_id: "scan-101:product-101",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: "2026-08-05T10:00:01Z",
  completed_at: "2026-08-05T10:00:02Z",
  refinement_count: 0,
  evidence: [
    {
      environment: "dev",
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
        timeline: Array.from({ length: 15 }, (_, offset) => ({
          projection_date: `2026-08-${String(5 + offset).padStart(2, "0")}`,
          quantity: offset < 7 ? "8.000000" : "0.000000",
        })),
      },
      coverage: {
        status: "partial",
        covered_quantity: "5.000000",
        residual_quantity: "35.000000",
        source_count: 1,
      },
      offers: [
        {
          offer_id: "offer-101",
          vendor_id: "vendor-101",
          vendor_name: "Fictional Approved Supplies",
          status: "eligible",
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
            history_status: "limited",
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
      preferences: {
        profile_id: "preference-3",
        company_id: "1",
        category_id: "category-safety",
        product_id: "product-101",
        scope: "product",
        scope_id: "product-101",
        revision: 6,
        ordered_criteria: ["price", "reliability", "delivery"],
        max_price_premium_percent: "10.000000",
        enforcement_mode: "advisory",
        precedence_source: "product",
        cheapest_eligible_cost: "437.500000",
        offer_results: [
          {
            offer_id: "offer-101",
            premium_percent: "0.000000",
            exceeds_cap: false,
            outcome: "within_cap",
          },
        ],
      },
    },
  ],
  result: {
    outcome: "approval_ready",
    validation_level: "t27",
    product_id: "product-101",
    product_name: "Fictional Safety Gloves",
    offer_id: "offer-101",
    rationale: "Projected stock is below the reorder minimum.",
    trade_offs: ["Reliable delivery is favored."],
    risk_flags: ["LIMITED_EVIDENCE"],
    uncertainty: "Vendor history is limited.",
    evidence_limitations: ["No quality evidence is available."],
    evidence_digest: `sha256:${"a".repeat(64)}`,
    quantity: "35.000000",
    unit_price: "12.500000",
    normalized_cost: "437.500000",
    budget_status: "within_budget",
    preference_profile_id: "preference-3",
    preference_scope: "product",
    preference_revision: 6,
    priority_order: ["price", "reliability", "delivery"],
    premium_outcome: "within_cap",
    read_only: true,
  },
  error: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RecommendationPage", () => {
  it("shows evidence only for the recommended product, not every evaluated candidate", async () => {
    const otherCandidateEvidence = {
      ...BASE_SCAN.evidence[0],
      evidence_id: "dev:evidence-product-999",
      product_id: "product-999",
      product_name: "Fictional Other Candidate",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          evidence: [otherCandidateEvidence, BASE_SCAN.evidence[0]],
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    expect(
      await screen.findByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();
    expect(screen.getByText("dev:evidence-product-101")).toBeInTheDocument();
    expect(screen.queryByText("Fictional Other Candidate")).not.toBeInTheDocument();
    expect(screen.queryByText("dev:evidence-product-999")).not.toBeInTheDocument();
  });

  it("shows loading before rendering an approval-ready result", async () => {
    const user = userEvent.setup();
    let resolveRequest: ((response: Response) => void) | undefined;
    const request = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request));

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading scan");
    resolveRequest?.(jsonResponse(BASE_SCAN));

    expect(
      await screen.findByRole("heading", {
        name: "Fictional Safety Gloves",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Read-only recommendation")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();

    expect(screen.getByText(/50%/)).toBeInTheDocument();

    await user.click(
      screen.getByText("View daily values"),
    );
    expect(screen.getByText("14-day inventory projection")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();

    expect(
      screen.getByRole("heading", { name: "Applied preferences" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Preference priority" })).toHaveTextContent(
      "1Price2Reliability3Delivery",
    );
    expect(screen.getByText("10%", { selector: ".preference-policy dd" })).toBeInTheDocument();
  });

  it("shows a decision summary before expandable supporting evidence", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(BASE_SCAN)));

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    const summary = await screen.findByRole("region", {
      name: "Recommendation summary",
    });
    expect(summary).toHaveTextContent("Approval ready");
    expect(summary).toHaveTextContent("Aug 12, 2026");
    expect(summary).toHaveTextContent("35 units");
    expect(summary).toHaveTextContent("1 eligible");
    expect(summary).toHaveTextContent("$437.50");
    expect(summary).toHaveTextContent("Within budget");
    const reasoning = screen.getByRole("region", { name: "AI reasoning" });
    expect(reasoning).toHaveTextContent("Validated against evidence");
    expect(reasoning).toHaveTextContent("Key trade-offs");
    expect(reasoning).toHaveTextContent("Risks and limitations");

    expect(
      screen.getByRole("region", { name: "Evidence details" }),
    ).toBeInTheDocument();

    const inventory = screen
      .getByText("View daily values")
      .closest("details");
    expect(inventory).not.toBeNull();
    expect(inventory).not.toHaveAttribute("open");

    await user.click(screen.getByText("View daily values"));
    expect(inventory).toHaveAttribute("open");
    expect(screen.getByRole("table")).toHaveAccessibleName(
      "14-day inventory projection",
    );
  });

  it("shows truthful icon-card highlights and risk status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(BASE_SCAN)));

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    const highlights = await screen.findByRole("region", {
      name: "Decision highlights",
    });
    expect(highlights).toHaveTextContent("Offers considered1 eligible1 total reviewed");
    expect(highlights).toHaveTextContent(
      "Uncovered target gap35 unitsAt Aug 12, 2026 stockout",
    );
    expect(highlights).toHaveTextContent(
      "Recommended vendorFictional Approved Supplies$437.50",
    );
    expect(highlights).toHaveTextContent("Budget statusWithin budget$4,402.50 remaining");

    const risks = screen.getByRole("region", {
      name: "Risks and limitations",
    });
    expect(risks).toHaveTextContent("LIMITED EVIDENCE");
    expect(risks).not.toHaveTextContent("No risk flags identified");
  });

  it("shows a green clear state only when no risk flags exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          result: { ...BASE_SCAN.result, risk_flags: [] },
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    const risks = await screen.findByRole("region", {
      name: "Risks and limitations",
    });
    expect(risks).toHaveTextContent("No risk flags identified");
  });

  it("shows a projection graph and separates rejected offers", async () => {
    const eligibleOffer = BASE_SCAN.evidence[0].offers[0];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          evidence: [
            {
              ...BASE_SCAN.evidence[0],
              offers: [
                eligibleOffer,
                {
                  ...eligibleOffer,
                  offer_id: "offer-rejected",
                  vendor_id: "vendor-rejected",
                  vendor_name: "Fictional Late Supplies",
                  status: "rejected",
                  reason_codes: ["DELIVERY_AFTER_NEED_BY"],
                  delivery_date: "2026-08-20",
                },
              ],
            },
          ],
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    expect(
      await screen.findByRole("img", {
        name: "Inventory projection from Aug 5, 2026 to Aug 19, 2026",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Projected inventory after existing coverage"),
    ).toBeInTheDocument();
    const offersSection = screen.getByRole("region", { name: "Vendor offers" });
    expect(within(offersSection).getAllByRole("listitem")).toHaveLength(2);
    expect(
      within(offersSection).getByText("Fictional Approved Supplies"),
    ).toBeInTheDocument();
    expect(
      within(offersSection).getByText("Fictional Late Supplies"),
    ).toBeInTheDocument();
    expect(within(offersSection).getByText("Delivery too late")).toBeInTheDocument();
  });

  it("presents applied preferences as ordered policy information", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(BASE_SCAN)));

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    await screen.findByRole("heading", { name: "Applied preferences" });
    expect(
      document.querySelector(".evidence-disclosures .disclosure"),
    ).toBeNull();

    expect(screen.getByText("Product scope")).toBeInTheDocument();
    expect(
      screen.getByText("Revision 6", { selector: ".policy-badges > span" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Advisory enforcement")).toBeInTheDocument();
    const priorities = screen.getByRole("list", {
      name: "Preference priority",
    });
    expect(priorities).toHaveTextContent("1Price");
    expect(priorities).toHaveTextContent("2Reliability");
    expect(priorities).toHaveTextContent("3Delivery");
    expect(screen.getByText("Maximum premium")).toBeInTheDocument();
    expect(screen.getByText("Within cap")).toBeInTheDocument();
    expect(screen.getByText("Captured Aug 5, 2026")).toBeInTheDocument();
  });

  it("shows a non-retryable unresolved result as manual review", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          status: "failed",
          result: null,
          error: {
            error_code: "NO_VALID_OFFER",
            message: "No approval-ready replenishment candidate was found.",
            retryable: false,
            retry_count: 0,
          },
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    expect(
      await screen.findByRole("heading", { name: "Manual review required" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/no approval-ready/i)).toBeInTheDocument();
  });

  it("shows retryable failures as safe errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          status: "failed",
          result: null,
          error: {
            error_code: "MCP_TIMEOUT",
            message: "The procurement source timed out.",
            retryable: true,
            retry_count: 1,
          },
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The procurement source timed out.",
    );
    expect(screen.getByText("MCP_TIMEOUT")).toBeInTheDocument();
  });

  it("polls running scans until they finish", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          ...BASE_SCAN,
          status: "running",
          completed_at: null,
          result: null,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(BASE_SCAN));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RecommendationPage
        scanId="scan-101"
        caseId="scan-101:product-101"
        onBack={vi.fn()}
        pollIntervalMs={20}
        maxPollAttempts={2}
      />,
    );

    expect(await screen.findByText("Scan in progress")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", {
        name: "Fictional Safety Gloves",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after the configured polling limit", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        jsonResponse({
          ...BASE_SCAN,
          status: "running",
          completed_at: null,
          result: null,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RecommendationPage
        scanId="scan-101"
        caseId="scan-101:product-101"
        onBack={vi.fn()}
        pollIntervalMs={1}
        maxPollAttempts={2}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The scan is still running.",
    );
    expect(screen.getByText("POLL_LIMIT_REACHED")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a safe manual-review fallback without a recommended-product evidence section", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          result: {
            outcome: "manual_review",
            rationale: "Contextual model judgment could not be safely accepted.",
            trade_offs: ["Compare the eligible offers manually."],
            risk_flags: ["LLM_OUTPUT_INVALID"],
            uncertainty: "No validated model recommendation is available.",
            evidence_limitations: ["The model response was invalid."],
            read_only: true,
          },
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    const summary = await screen.findByRole("region", {
      name: "Recommendation summary",
    });
    expect(summary).toHaveTextContent("Manual review");
    expect(summary).toHaveTextContent("No draft created");
    expect(
      screen.getByRole("heading", { name: "Compare eligible offers" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Contextual model judgment could not be safely accepted."),
    ).toBeInTheDocument();
    expect(screen.getByText("Compare the eligible offers manually.")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Deterministic procurement evidence" }),
    ).not.toBeInTheDocument();
  });

  it("shows evidence for a no_valid_offer result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          result: {
            outcome: "no_valid_offer",
            product_id: "product-101",
            product_name: "Fictional Safety Gloves",
            rationale: "No eligible offer: 1 offer rejected (vendor not approved).",
            evidence_limitations: [],
            read_only: true,
          },
        }),
      ),
    );

    render(
      <RecommendationPage
        scanId="scan-101"
        caseId="scan-101:product-101"
        onBack={vi.fn()}
      />,
    );

    expect(
      await screen.findByText(
        "No eligible offer: 1 offer rejected (vendor not approved).",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Fictional Approved Supplies")).toBeInTheDocument();
  });

  it("keeps historical successful recommendations approval-ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          result: {
            outcome: "approval_ready",
            validation_level: "legacy",
            product_id: "product-101",
            product_name: "Fictional Safety Gloves",
            offer_id: null,
            rationale: "One eligible candidate was available.",
            trade_offs: ["Authoritative evidence remains available."],
            risk_flags: ["LEGACY_RECOMMENDATION"],
            uncertainty: "This recommendation predates T27 validation.",
            evidence_limitations: [
              "Offer-level validation metadata is unavailable.",
            ],
            read_only: true,
          },
        }),
      ),
    );

    render(<RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />);

    const summary = await screen.findByRole("region", {
      name: "Recommendation summary",
    });
    expect(summary).toHaveTextContent("Historical recommendation");
    expect(summary).toHaveTextContent("Predates T27 validation");
    expect(summary).not.toHaveTextContent("Selected offer-101");
    expect(
      screen.queryByRole("region", { name: "Manual review summary" }),
    ).not.toBeInTheDocument();
  });

  it("stops scheduled polling when the page unmounts", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        ...BASE_SCAN,
        status: "running",
        completed_at: null,
        result: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const view = render(
      <RecommendationPage
        scanId="scan-101"
        caseId="scan-101:product-101"
        onBack={vi.fn()}
        pollIntervalMs={20}
        maxPollAttempts={5}
      />,
    );
    await screen.findByText("Scan in progress");
    view.unmount();

    await new Promise((resolve) => window.setTimeout(resolve, 30));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it("shows a refinement panel for an approval-ready result and restarts polling after a submission", async () => {
    const user = userEvent.setup();
    const runningScan = {
      ...BASE_SCAN,
      status: "running",
      result: null,
      completed_at: null,
      refinement_count: 0,
    };
    const refinedScan = {
      ...BASE_SCAN,
      refinement_count: 1,
      result: {
        ...BASE_SCAN.result,
        rationale: "Refined: Prioritize delivery speed.",
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(BASE_SCAN))
      .mockResolvedValueOnce(jsonResponse(runningScan, 202))
      .mockResolvedValueOnce(jsonResponse(refinedScan));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />,
    );

    await screen.findByLabelText("Refinement note");
    await user.type(
      screen.getByLabelText("Refinement note"),
      "Prioritize delivery speed.",
    );
    await user.click(screen.getByRole("button", { name: "Submit refinement" }));

    expect(
      await screen.findByText("Refined: Prioritize delivery speed."),
    ).toBeInTheDocument();
    expect(screen.getByText("1 of 3 refinements used")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/scans/scan-101/cases/scan-101%3Aproduct-101/refine",
    );
  });
});
