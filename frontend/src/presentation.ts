const dateFormatter = new Intl.DateTimeFormat("en-US", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

function finiteNumber(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatDate(value: string | null): string {
  if (value === null) {
    return "Not reached";
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? value : dateFormatter.format(parsed);
}

export function formatNumber(value: string): string {
  const parsed = finiteNumber(value);
  return parsed === null ? value : numberFormatter.format(parsed);
}

export function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : dateTimeFormatter.format(parsed);
}

export function formatQuantity(value: string): string {
  const parsed = finiteNumber(value);
  if (parsed === null) {
    return value;
  }
  return `${numberFormatter.format(parsed)} ${parsed === 1 ? "unit" : "units"}`;
}

export function formatCurrency(value: string, currency: string): string {
  const parsed = finiteNumber(value);
  if (parsed === null) {
    return `${value} ${currency}`;
  }
  try {
    return new Intl.NumberFormat("en-US", {
      currency,
      currencyDisplay: "narrowSymbol",
      maximumFractionDigits: 2,
      minimumFractionDigits: 2,
      style: "currency",
    }).format(parsed);
  } catch {
    return `${numberFormatter.format(parsed)} ${currency}`;
  }
}

export function formatPercent(value: string): string {
  return `${formatNumber(value)}%`;
}

export function formatRatioPercent(value: string | null): string {
  if (value === null) {
    return "No history";
  }
  const parsed = finiteNumber(value);
  return parsed === null
    ? value
    : new Intl.NumberFormat("en-US", {
        maximumFractionDigits: 1,
        style: "percent",
      }).format(parsed);
}

export const OUTCOME_LABEL: Record<string, string> = {
  approval_ready: "Approval ready",
  manual_review: "Manual review",
  no_valid_offer: "No valid offer",
  confirmed: "Confirmed",
  error: "Error",
  pending_approval: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  confirming: "Confirming",
  cancelled: "Cancelled",
  reconciliation_required: "Reconciliation required",
};

export const OUTCOME_COLOR: Record<string, string> = {
  approval_ready: "#2f9e58",
  manual_review: "#3157c8",
  no_valid_offer: "#c0392b",
  confirmed: "#2f9e58",
  error: "#c0392b",
  pending_approval: "#b4780a",
  approved: "#3157c8",
  rejected: "#c0392b",
  confirming: "#3157c8",
  cancelled: "#60708a",
  reconciliation_required: "#c0392b",
};
