/**
 * The income statement, line by line, as the engine computed it.
 *
 * Laid out so it can be checked against a calculator: every line is either a
 * figure the server sent or a subtotal the server also sent. Nothing here is
 * derived in the browser, which is why a reader can trust that what they see
 * is what Python computed.
 */

import type { DealMetrics } from "../api/client";

const money = (value: number) =>
  value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

interface RowProps {
  label: string;
  value: number;
  /** Render as a deduction: parenthesised and muted. */
  deduction?: boolean;
  subtotal?: boolean;
  note?: string;
}

function Row({ label, value, deduction, subtotal, note }: RowProps) {
  const classes = ["line"];
  if (subtotal) classes.push("line-subtotal");
  if (deduction) classes.push("line-deduction");

  return (
    <div className={classes.join(" ")}>
      <span className="line-label">
        {label}
        {note && <em className="line-note">{note}</em>}
      </span>
      <span className="line-value">
        {deduction ? `(${money(Math.abs(value))})` : money(value)}
      </span>
    </div>
  );
}

export function CashFlowBreakdown({ metrics }: { metrics: DealMetrics }) {
  const negative = metrics.annual_cash_flow < 0;

  return (
    <div className="panel">
      <h2>Annual cash flow</h2>

      <Row label="Gross scheduled rent" value={metrics.gross_scheduled_income} />
      <Row label="Vacancy loss" value={metrics.vacancy_loss} deduction />
      <Row
        label="Effective gross income"
        value={metrics.effective_gross_income}
        subtotal
      />

      <Row
        label="Operating expenses"
        value={metrics.operating_expenses}
        deduction
        note="taxes, insurance, maintenance, management"
      />
      <Row
        label="Net operating income"
        value={metrics.net_operating_income}
        subtotal
        note="excludes debt service and capex"
      />

      <Row label="Capex reserve" value={metrics.capex_reserve} deduction />
      <Row label="Debt service" value={metrics.annual_debt_service} deduction />

      <div className={`line line-total ${negative ? "is-negative" : ""}`}>
        <span className="line-label">Annual cash flow</span>
        <span className="line-value">{money(metrics.annual_cash_flow)}</span>
      </div>
      <div className={`line line-sub ${negative ? "is-negative" : ""}`}>
        <span className="line-label">Monthly cash flow</span>
        <span className="line-value">{money(metrics.monthly_cash_flow)}</span>
      </div>

      <h2>Cash to close</h2>
      <Row label="Down payment" value={metrics.down_payment} />
      <Row label="Closing costs" value={metrics.closing_costs} />
      {metrics.total_cash_invested -
        metrics.down_payment -
        metrics.closing_costs >
        0 && (
        <Row
          label="Rehab paid in cash"
          value={
            metrics.total_cash_invested -
            metrics.down_payment -
            metrics.closing_costs
          }
        />
      )}
      <Row label="Total cash invested" value={metrics.total_cash_invested} subtotal />

      <h2>Loan</h2>
      <Row label="Loan amount" value={metrics.loan_amount} />
      <Row label="Monthly principal and interest" value={metrics.monthly_payment} />
    </div>
  );
}
