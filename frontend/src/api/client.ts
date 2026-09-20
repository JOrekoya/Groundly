/**
 * Typed client for the Groundly API.
 *
 * These types mirror backend/app/schemas.py. They are hand-written rather than
 * generated so the duplication is visible: if a field is added server-side and
 * not here, TypeScript says so at the call site.
 *
 * Nothing in this file computes a financial figure. Every number rendered by
 * the dashboard comes from the Python engine, which is the point.
 */

export type Strategy = "rental" | "flip" | "brrrr";

export interface OperatingExpenses {
  property_taxes_annual: number;
  insurance_annual: number;
  hoa_annual: number;
  other_annual: number;
  maintenance_rate: number;
  management_rate: number;
  capex_reserve_rate: number;
}

export interface DealScope {
  purchase_price: number;
  address: string | null;
  strategy: Strategy;

  down_payment_rate: number;
  interest_rate: number;
  term_years: number;
  closing_cost_rate: number;
  finance_rehab: boolean;

  rehab_budget: number;
  after_repair_value: number | null;

  monthly_rent: number;
  other_monthly_income: number;
  vacancy_rate: number;

  expenses: OperatingExpenses;
}

export interface DealMetrics {
  gross_scheduled_income: number;
  vacancy_loss: number;
  effective_gross_income: number;
  operating_expenses: number;
  net_operating_income: number;

  loan_amount: number;
  monthly_payment: number;
  annual_debt_service: number;
  total_cash_invested: number;
  down_payment: number;
  closing_costs: number;

  capex_reserve: number;
  annual_cash_flow: number;
  monthly_cash_flow: number;

  cap_rate: number;
  /** Null when no cash was invested — undefined, not zero. */
  cash_on_cash_return: number | null;
  /** Null on an all-cash deal: no debt to cover, so not applicable. */
  debt_service_coverage_ratio: number | null;
  break_even_monthly_rent: number | null;

  max_allowable_offer: number | null;
  passes_seventy_percent_rule: boolean | null;
}

export interface AnalyzeResponse {
  scope: DealScope;
  metrics: DealMetrics;
}

export class ApiError extends Error {}

/** Sensible starting deal, matching the CLI's defaults. */
export const DEFAULT_SCOPE: DealScope = {
  purchase_price: 300000,
  address: null,
  strategy: "rental",
  down_payment_rate: 0.2,
  interest_rate: 0.07,
  term_years: 30,
  closing_cost_rate: 0.02,
  finance_rehab: false,
  rehab_budget: 0,
  after_repair_value: null,
  monthly_rent: 2800,
  other_monthly_income: 0,
  vacancy_rate: 0.05,
  expenses: {
    property_taxes_annual: 3600,
    insurance_annual: 1200,
    hoa_annual: 0,
    other_annual: 0,
    maintenance_rate: 0.05,
    management_rate: 0.08,
    capex_reserve_rate: 0.05,
  },
};

/**
 * Send a scope to the engine and get every derived metric back.
 *
 * `signal` lets a superseded request be abandoned mid-drag, so a slow response
 * from an earlier slider position cannot overwrite a newer one.
 */
export async function analyze(
  scope: DealScope,
  signal?: AbortSignal,
): Promise<AnalyzeResponse> {
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
    signal,
  });

  if (!response.ok) {
    let detail = `Request failed with ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first = body.detail[0];
        const field = (first.loc ?? []).slice(1).join(".");
        detail = field ? `${field}: ${first.msg}` : first.msg;
      }
    } catch {
      // Keep the status-code message if the body is not JSON.
    }
    throw new ApiError(detail);
  }

  return response.json();
}
