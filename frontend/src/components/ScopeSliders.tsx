/**
 * The what-if controls.
 *
 * Every control edits one field of the deal scope and hands the whole updated
 * scope upward. No component here derives a financial figure — dragging a
 * slider changes an input and nothing else, and the numbers that appear are
 * whatever Python sends back.
 */

import type { DealScope, OperatingExpenses } from "../api/client";

interface Props {
  scope: DealScope;
  onChange: (scope: DealScope) => void;
}

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
  hint?: string;
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
  hint,
}: SliderProps) {
  return (
    <label className="control">
      <div className="control-head">
        <span className="control-label">{label}</span>
        <span className="control-value">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint && <span className="control-hint">{hint}</span>}
    </label>
  );
}

interface NumberFieldProps {
  label: string;
  value: number;
  step?: number;
  onChange: (value: number) => void;
}

function NumberField({ label, value, step = 1000, onChange }: NumberFieldProps) {
  return (
    <label className="control">
      <div className="control-head">
        <span className="control-label">{label}</span>
      </div>
      <input
        type="number"
        min={0}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value) || 0)}
      />
    </label>
  );
}

const money = (value: number) =>
  value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

const percent = (digits = 0) => (value: number) =>
  `${(value * 100).toFixed(digits)}%`;

export function ScopeSliders({ scope, onChange }: Props) {
  const set = <K extends keyof DealScope>(key: K, value: DealScope[K]) =>
    onChange({ ...scope, [key]: value });

  const setExpense = <K extends keyof OperatingExpenses>(
    key: K,
    value: OperatingExpenses[K],
  ) => onChange({ ...scope, expenses: { ...scope.expenses, [key]: value } });

  return (
    <div className="panel sliders">
      <h2>The deal</h2>

      <Slider
        label="Purchase price"
        value={scope.purchase_price}
        min={25000}
        max={2000000}
        step={5000}
        format={money}
        onChange={(value) => set("purchase_price", value)}
      />
      <Slider
        label="Monthly rent"
        value={scope.monthly_rent}
        min={0}
        max={15000}
        step={25}
        format={money}
        onChange={(value) => set("monthly_rent", value)}
      />

      <h2>Financing</h2>

      <Slider
        label="Down payment"
        value={scope.down_payment_rate}
        min={0}
        max={1}
        step={0.01}
        format={percent(0)}
        onChange={(value) => set("down_payment_rate", value)}
        hint="100% is an all-cash purchase; DSCR stops applying."
      />
      <Slider
        label="Interest rate"
        value={scope.interest_rate}
        min={0}
        max={0.2}
        step={0.00125}
        format={percent(3)}
        onChange={(value) => set("interest_rate", value)}
      />
      <Slider
        label="Term"
        value={scope.term_years}
        min={5}
        max={40}
        step={1}
        format={(value) => `${value} years`}
        onChange={(value) => set("term_years", value)}
      />
      <Slider
        label="Closing costs"
        value={scope.closing_cost_rate}
        min={0}
        max={0.08}
        step={0.0025}
        format={percent(2)}
        onChange={(value) => set("closing_cost_rate", value)}
      />

      <h2>Operating</h2>

      <Slider
        label="Vacancy"
        value={scope.vacancy_rate}
        min={0}
        max={0.3}
        step={0.005}
        format={percent(1)}
        onChange={(value) => set("vacancy_rate", value)}
      />
      <Slider
        label="Maintenance"
        value={scope.expenses.maintenance_rate}
        min={0}
        max={0.25}
        step={0.005}
        format={percent(1)}
        onChange={(value) => setExpense("maintenance_rate", value)}
        hint="Share of effective gross income."
      />
      <Slider
        label="Management"
        value={scope.expenses.management_rate}
        min={0}
        max={0.25}
        step={0.005}
        format={percent(1)}
        onChange={(value) => setExpense("management_rate", value)}
      />
      <Slider
        label="Capex reserve"
        value={scope.expenses.capex_reserve_rate}
        min={0}
        max={0.25}
        step={0.005}
        format={percent(1)}
        onChange={(value) => setExpense("capex_reserve_rate", value)}
        hint="Sits below NOI, but is a real cash outflow."
      />

      <h2>Annual costs</h2>

      <div className="grid-2">
        <NumberField
          label="Property taxes"
          value={scope.expenses.property_taxes_annual}
          step={100}
          onChange={(value) => setExpense("property_taxes_annual", value)}
        />
        <NumberField
          label="Insurance"
          value={scope.expenses.insurance_annual}
          step={100}
          onChange={(value) => setExpense("insurance_annual", value)}
        />
        <NumberField
          label="HOA"
          value={scope.expenses.hoa_annual}
          step={100}
          onChange={(value) => setExpense("hoa_annual", value)}
        />
        <NumberField
          label="Rehab budget"
          value={scope.rehab_budget}
          step={1000}
          onChange={(value) => set("rehab_budget", value)}
        />
      </div>

      <h2>Flip check</h2>

      <label className="control">
        <div className="control-head">
          <span className="control-label">After-repair value</span>
          <span className="control-value">
            {scope.after_repair_value === null
              ? "not set"
              : money(scope.after_repair_value)}
          </span>
        </div>
        <input
          type="number"
          min={0}
          step={5000}
          placeholder="Leave blank to skip the 70% rule"
          value={scope.after_repair_value ?? ""}
          onChange={(event) =>
            set(
              "after_repair_value",
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
        />
        <span className="control-hint">
          Set this to evaluate the 70% rule.
        </span>
      </label>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={scope.finance_rehab}
          onChange={(event) => set("finance_rehab", event.target.checked)}
        />
        <span>Roll rehab into the loan</span>
      </label>
    </div>
  );
}
