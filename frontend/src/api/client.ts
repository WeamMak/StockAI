const SCANS_PATH = "/api/v1/scans";
const SESSION_PATH = "/api/v1/session";
const CSRF_COOKIE_NAME = "stockai_csrf";
const MAX_SCAN_LIST_LENGTH = 100;

export type ScanStatus = "queued" | "running" | "succeeded" | "failed";
export type ScanTrigger = "manual" | "cron";

export interface VendorPerformanceEvidence {
  completed_order_count: number;
  on_time_rate: string | null;
  history_status: "limited" | "sufficient";
}

export interface OfferEvidence {
  offer_id: string;
  vendor_id: string;
  vendor_name: string;
  status: "eligible" | "rejected";
  reason_codes: string[];
  currency: string;
  unit_price: string;
  company_currency: string;
  normalized_unit_price: string;
  delivery_date: string;
  quantity: string;
  normalized_cost: string;
  projected_inventory_after_receipt: string;
  excess_inventory: string;
  performance: VendorPerformanceEvidence;
}

export interface ProcurementEvidence {
  environment: "dev" | "prod";
  evidence_id: string;
  product_id: string;
  product_name: string;
  category_id: string;
  captured_at: string;
  shortage: {
    horizon_start: string;
    horizon_end: string;
    reorder_trigger_date: string | null;
    need_by_date: string;
    reorder_minimum: string;
    reorder_maximum: string;
    minimum_projected_quantity: string;
    timeline: { projection_date: string; quantity: string }[];
  };
  coverage: {
    status: "none" | "partial" | "full";
    covered_quantity: string;
    residual_quantity: string;
    source_count: number;
  };
  offers: OfferEvidence[];
  budget: {
    period_start: string;
    currency: string;
    budget_amount: string;
    confirmed_commitment: string;
    proposed_amount: string;
    remaining_before: string;
    remaining_after: string;
    overage: string;
    exception_required: boolean;
  } | null;
  skip_reason_code: string | null;
}

export interface ApprovalReadyResult {
  outcome: "approval_ready";
  product_id: string;
  product_name: string;
  rationale: string;
  risk_flags: string[];
  read_only: true;
}

export interface ScanFailure {
  error_code: string;
  message: string;
  retryable: boolean;
  retry_count: number;
}

export interface Scan {
  scan_id: string;
  status: ScanStatus;
  trigger: ScanTrigger;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  evidence: ProcurementEvidence[];
  result: ApprovalReadyResult | null;
  error: ScanFailure | null;
}

export interface Session {
  user_id: string;
  email: string;
  role: "officer" | "manager";
}

interface RequestOptions {
  signal?: AbortSignal;
}

interface ApiResponse {
  body: unknown;
  status: number;
}

export class ApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, message: string, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function invalidResponse(): never {
  throw new ApiError(
    "INVALID_RESPONSE",
    "The server returned an invalid response.",
    true,
  );
}

function parseResult(value: unknown): ApprovalReadyResult | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    value.outcome !== "approval_ready" ||
    typeof value.product_id !== "string" ||
    typeof value.product_name !== "string" ||
    typeof value.rationale !== "string" ||
    !Array.isArray(value.risk_flags) ||
    !value.risk_flags.every((flag) => typeof flag === "string") ||
    value.read_only !== true
  ) {
    return invalidResponse();
  }
  return {
    outcome: "approval_ready",
    product_id: value.product_id,
    product_name: value.product_name,
    rationale: value.rationale,
    risk_flags: value.risk_flags,
    read_only: true,
  };
}

function parseFailure(value: unknown): ScanFailure | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.error_code !== "string" ||
    typeof value.message !== "string" ||
    typeof value.retryable !== "boolean" ||
    !Number.isInteger(value.retry_count) ||
    (value.retry_count as number) < 0
  ) {
    return invalidResponse();
  }
  return {
    error_code: value.error_code,
    message: value.message,
    retryable: value.retryable,
    retry_count: value.retry_count as number,
  };
}

function stringField(record: Record<string, unknown>, name: string): string {
  return typeof record[name] === "string" ? record[name] : invalidResponse();
}

function parseEvidence(value: unknown): ProcurementEvidence {
  if (!isRecord(value) || !isRecord(value.shortage) || !isRecord(value.coverage)) {
    return invalidResponse();
  }
  const offers = value.offers;
  const timeline = value.shortage.timeline;
  if (!Array.isArray(offers) || offers.length > 50) {
    return invalidResponse();
  }
  if (
    !Array.isArray(timeline) ||
    timeline.length !== 15 ||
    !timeline.every(
      (item) =>
        isRecord(item) &&
        typeof item.projection_date === "string" &&
        typeof item.quantity === "string",
    )
  ) {
    return invalidResponse();
  }
  const parsedOffers = offers.map((offer): OfferEvidence => {
    if (!isRecord(offer) || !isRecord(offer.performance)) {
      return invalidResponse();
    }
    const reasonCodes = offer.reason_codes;
    if (!Array.isArray(reasonCodes) || !reasonCodes.every((code) => typeof code === "string")) {
      return invalidResponse();
    }
    const historyStatus = offer.performance.history_status;
    const offerStatus = offer.status;
    if (
      !["eligible", "rejected"].includes(String(offerStatus)) ||
      !["limited", "sufficient"].includes(String(historyStatus)) ||
      !Number.isInteger(offer.performance.completed_order_count) ||
      !isNullableString(offer.performance.on_time_rate)
    ) {
      return invalidResponse();
    }
    return {
      offer_id: stringField(offer, "offer_id"),
      vendor_id: stringField(offer, "vendor_id"),
      vendor_name: stringField(offer, "vendor_name"),
      status: offerStatus as OfferEvidence["status"],
      reason_codes: reasonCodes,
      currency: stringField(offer, "currency"),
      unit_price: stringField(offer, "unit_price"),
      company_currency: stringField(offer, "company_currency"),
      normalized_unit_price: stringField(offer, "normalized_unit_price"),
      delivery_date: stringField(offer, "delivery_date"),
      quantity: stringField(offer, "quantity"),
      normalized_cost: stringField(offer, "normalized_cost"),
      projected_inventory_after_receipt: stringField(
        offer,
        "projected_inventory_after_receipt",
      ),
      excess_inventory: stringField(offer, "excess_inventory"),
      performance: {
        completed_order_count: offer.performance.completed_order_count as number,
        on_time_rate: offer.performance.on_time_rate,
        history_status: historyStatus as VendorPerformanceEvidence["history_status"],
      },
    };
  });
  const coverageStatus = value.coverage.status;
  if (
    !["dev", "prod"].includes(String(value.environment)) ||
    !["none", "partial", "full"].includes(String(coverageStatus)) ||
    !Number.isInteger(value.coverage.source_count) ||
    !isNullableString(value.shortage.reorder_trigger_date) ||
    !isNullableString(value.skip_reason_code)
  ) {
    return invalidResponse();
  }
  let budget: ProcurementEvidence["budget"] = null;
  if (value.budget !== null) {
    if (!isRecord(value.budget) || typeof value.budget.exception_required !== "boolean") {
      return invalidResponse();
    }
    budget = {
      period_start: stringField(value.budget, "period_start"),
      currency: stringField(value.budget, "currency"),
      budget_amount: stringField(value.budget, "budget_amount"),
      confirmed_commitment: stringField(value.budget, "confirmed_commitment"),
      proposed_amount: stringField(value.budget, "proposed_amount"),
      remaining_before: stringField(value.budget, "remaining_before"),
      remaining_after: stringField(value.budget, "remaining_after"),
      overage: stringField(value.budget, "overage"),
      exception_required: value.budget.exception_required,
    };
  }
  return {
    environment: value.environment as ProcurementEvidence["environment"],
    evidence_id: stringField(value, "evidence_id"),
    product_id: stringField(value, "product_id"),
    product_name: stringField(value, "product_name"),
    category_id: stringField(value, "category_id"),
    captured_at: stringField(value, "captured_at"),
    shortage: {
      horizon_start: stringField(value.shortage, "horizon_start"),
      horizon_end: stringField(value.shortage, "horizon_end"),
      reorder_trigger_date: value.shortage.reorder_trigger_date,
      need_by_date: stringField(value.shortage, "need_by_date"),
      reorder_minimum: stringField(value.shortage, "reorder_minimum"),
      reorder_maximum: stringField(value.shortage, "reorder_maximum"),
      minimum_projected_quantity: stringField(
        value.shortage,
        "minimum_projected_quantity",
      ),
      timeline: timeline.map((item) => ({
        projection_date: stringField(item as Record<string, unknown>, "projection_date"),
        quantity: stringField(item as Record<string, unknown>, "quantity"),
      })),
    },
    coverage: {
      status: coverageStatus as ProcurementEvidence["coverage"]["status"],
      covered_quantity: stringField(value.coverage, "covered_quantity"),
      residual_quantity: stringField(value.coverage, "residual_quantity"),
      source_count: value.coverage.source_count as number,
    },
    offers: parsedOffers,
    budget,
    skip_reason_code: value.skip_reason_code,
  };
}

function parseScan(value: unknown): Scan {
  if (
    !isRecord(value) ||
    typeof value.scan_id !== "string" ||
    typeof value.status !== "string" ||
    !["queued", "running", "succeeded", "failed"].includes(
      value.status,
    ) ||
    typeof value.trigger !== "string" ||
    !["manual", "cron"].includes(value.trigger) ||
    typeof value.created_at !== "string" ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.completed_at)
  ) {
    return invalidResponse();
  }

  const result = parseResult(value.result);
  const error = parseFailure(value.error);
  if (!Array.isArray(value.evidence) || value.evidence.length > 50) {
    return invalidResponse();
  }
  const evidence = value.evidence.map(parseEvidence);
  if (
    (value.status === "succeeded" && result === null) ||
    (value.status === "failed" && error === null) ||
    (["queued", "running"].includes(value.status) &&
      (result !== null || error !== null))
  ) {
    return invalidResponse();
  }

  return {
    scan_id: value.scan_id,
    status: value.status as ScanStatus,
    trigger: value.trigger as ScanTrigger,
    created_at: value.created_at,
    started_at: value.started_at,
    completed_at: value.completed_at,
    evidence,
    result,
    error,
  };
}

function parsePublicError(value: unknown): ApiError {
  if (
    isRecord(value) &&
    typeof value.error_code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean"
  ) {
    return new ApiError(value.error_code, value.message, value.retryable);
  }
  return new ApiError(
    "REQUEST_FAILED",
    "The request could not be completed.",
    true,
  );
}

async function request(
  path: string,
  init: RequestInit,
): Promise<ApiResponse> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.headers as Record<string, string> | undefined),
      },
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError(
      "NETWORK_ERROR",
      "The service could not be reached.",
      true,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return invalidResponse();
  }
  if (!response.ok) {
    throw parsePublicError(body);
  }
  return { body, status: response.status };
}

function cookieValue(name: string): string | null {
  const prefix = `${name}=`;
  for (const entry of document.cookie.split(";")) {
    const candidate = entry.trim();
    if (candidate.startsWith(prefix)) {
      return decodeURIComponent(candidate.slice(prefix.length));
    }
  }
  return null;
}

export async function getSession(
  options: RequestOptions = {},
): Promise<Session> {
  const response = await request(SESSION_PATH, {
    method: "GET",
    signal: options.signal,
  });
  if (
    !isRecord(response.body) ||
    typeof response.body.user_id !== "string" ||
    typeof response.body.email !== "string" ||
    !["officer", "manager"].includes(String(response.body.role))
  ) {
    return invalidResponse();
  }
  return {
    user_id: response.body.user_id,
    email: response.body.email,
    role: response.body.role as Session["role"],
  };
}

export async function createManualScan(
  options: RequestOptions = {},
): Promise<Scan> {
  const csrfToken = cookieValue(CSRF_COOKIE_NAME);
  const response = await request(SCANS_PATH, {
    method: "POST",
    headers: csrfToken === null ? {} : { "X-CSRF-Token": csrfToken },
    signal: options.signal,
  });
  if (response.status !== 202) {
    return invalidResponse();
  }
  return parseScan(response.body);
}

export async function listScans(
  options: RequestOptions = {},
): Promise<Scan[]> {
  const response = await request(SCANS_PATH, {
    method: "GET",
    signal: options.signal,
  });
  if (
    !isRecord(response.body) ||
    !Array.isArray(response.body.scans) ||
    response.body.scans.length > MAX_SCAN_LIST_LENGTH
  ) {
    return invalidResponse();
  }
  return response.body.scans.map(parseScan);
}

export async function getScan(
  scanId: string,
  options: RequestOptions = {},
): Promise<Scan> {
  const response = await request(`${SCANS_PATH}/${encodeURIComponent(scanId)}`, {
    method: "GET",
    signal: options.signal,
  });
  return parseScan(response.body);
}
