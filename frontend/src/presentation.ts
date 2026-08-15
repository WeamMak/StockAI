const dateFormatter = new Intl.DateTimeFormat("en-US", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
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
