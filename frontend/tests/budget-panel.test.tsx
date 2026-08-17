import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetPanel } from "../src/components/BudgetPanel";

describe("BudgetPanel", () => {
  it("renders budget figures without a collapse control", () => {
    render(
      <BudgetPanel
        budget={{
          period_start: "2026-08-01",
          currency: "USD",
          budget_amount: "5000.000000",
          confirmed_commitment: "160.000000",
          proposed_amount: "1080.000000",
          remaining_before: "4840.000000",
          remaining_after: "3760.000000",
          overage: "0.000000",
          exception_required: false,
        }}
      />,
    );

    expect(screen.getByText("Budget calculation")).toBeInTheDocument();
    expect(screen.getByText("Within budget")).toBeInTheDocument();
    expect(screen.getByText("$3,760.00")).toBeInTheDocument();
    expect(document.querySelector("details.budget-panel")).toBeNull();
  });

  it("shows the exception warning when required", () => {
    render(
      <BudgetPanel
        budget={{
          period_start: "2026-08-01",
          currency: "USD",
          budget_amount: "500.000000",
          confirmed_commitment: "0.000000",
          proposed_amount: "600.000000",
          remaining_before: "500.000000",
          remaining_after: "-100.000000",
          overage: "100.000000",
          exception_required: true,
        }}
      />,
    );

    expect(screen.getByText("Exception required")).toBeInTheDocument();
    expect(
      screen.getByText(/Manager exception required for \$100\.00 overage\./),
    ).toBeInTheDocument();
  });

  it("renders nothing when budget is null", () => {
    const { container } = render(<BudgetPanel budget={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
