/**
 * The headline metrics, plus the flip check when an ARV is set.
 *
 * Ratios that do not apply arrive as null and are shown as "n/a" with a
 * reason, never as zero. An all-cash deal has no DSCR because there is no debt
 * to cover, which is a different thing from a deal that cannot cover its debt,
 * and a dashboard that renders both as 0.00 would be lying about one of them.
 */

import type { DealMetrics } from "../api/client";
import { CashFlowBreakdown } from "./CashFlowBreakdown";

const money = (value: number) =>
  value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

interface TileProps {
  label: string;
  value: string;
  tone?: "good" | "bad" | "muted";
  note?: string;
}

function Tile({ label, value, tone, note }: TileProps) {
  return (
    <div className={`tile ${tone ? `tile-${tone}` : ""}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}

function ratioTone(
  value: number | null,
  threshold: number,
): "good" | "bad" | "muted" {
  if (value === null) return "muted";
  return value >= threshold ? "good" : "bad";
}

export function DealDashboard({ metrics }: { metrics: DealMetrics }) {
  const dscr = metrics.debt_service_coverage_ratio;
  const coc = metrics.cash_on_cash_return;

  return (
    <div className="dashboard">
      <div className="tiles">
        <Tile
          label="Monthly cash flow"
          value={money(metrics.monthly_cash_flow)}
          tone={metrics.monthly_cash_flow >= 0 ? "good" : "bad"}
          note="what's left each month after every bill"
        />
        <Tile
          label="Cap rate"
          value={`${(metrics.cap_rate * 100).toFixed(2)}%`}
          tone={metrics.cap_rate >= 0.06 ? "good" : metrics.cap_rate >= 0.04 ? undefined : "bad"}
          note="how well the building earns, ignoring the loan"
        />
        <Tile
          label="Cash-on-cash"
          value={coc === null ? "n/a" : `${(coc * 100).toFixed(2)}%`}
          tone={coc === null ? "muted" : coc >= 0.08 ? "good" : coc >= 0 ? undefined : "bad"}
          note={coc === null ? "no cash invested" : "yearly return on the cash you put in"}
        />
        <Tile
          label="DSCR"
          value={dscr === null ? "n/a" : dscr.toFixed(2)}
          tone={ratioTone(dscr, 1.2)}
          note={dscr === null ? "no loan, so not applicable" : "times the profit covers the mortgage"}
        />
        <Tile
          label="Break-even rent"
          value={
            metrics.break_even_monthly_rent === null
              ? "n/a"
              : money(metrics.break_even_monthly_rent)
          }
          note="the rent at which you'd make nothing"
        />
        <Tile
          label="Mortgage payment"
          value={money(metrics.monthly_payment)}
          note="principal and interest, each month"
        />
      </div>

      {metrics.max_allowable_offer !== null && (
        <div
          className={`verdict ${
            metrics.passes_seventy_percent_rule ? "is-pass" : "is-fail"
          }`}
        >
          <span className="verdict-rule">70% rule</span>
          <span className="verdict-result">
            {metrics.passes_seventy_percent_rule ? "PASS" : "FAIL"}
          </span>
          <span className="verdict-detail">
            ceiling {money(metrics.max_allowable_offer)} — 70% of ARV, less
            rehab
          </span>
        </div>
      )}

      <CashFlowBreakdown metrics={metrics} />
    </div>
  );
}
