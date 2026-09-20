/**
 * Look up what nearby sales say a property is worth.
 *
 * Renders a range, never a bare number, and shows the model's own grade for
 * the estimate alongside the reasons behind it. When the comps are too thin,
 * this shows the refusal as plainly as it would show a valuation — a "we do
 * not know" is a real answer here, not a failure state to be hidden.
 */

import { useState } from "react";
import { type Valuation, valueProperty } from "../api/client";
import { CompTable } from "./CompTable";

const money = (value: number) =>
  value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

/** Somewhere with real comps in each validated county, for a quick look. */
const EXAMPLES = [
  { label: "Chicago, IL", lat: 41.9484, lon: -87.6553, sqft: 1800 },
  { label: "Philadelphia, PA", lat: 40.0095, lon: -75.1421, sqft: 1200 },
];

interface Props {
  onUsePrice: (price: number) => void;
}

export function ValuationPanel({ onUsePrice }: Props) {
  const [lat, setLat] = useState("41.9484");
  const [lon, setLon] = useState("-87.6553");
  const [sqft, setSqft] = useState("1800");
  const [beds, setBeds] = useState("3");
  const [baths, setBaths] = useState("2");
  const [result, setResult] = useState<Valuation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await valueProperty({
          latitude: Number(lat),
          longitude: Number(lon),
          building_sqft: sqft ? Number(sqft) : null,
          beds: beds ? Number(beds) : null,
          full_baths: baths ? Number(baths) : null,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lookup failed");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="panel">
        <h2>What do nearby sales say?</h2>

        <div className="grid-2">
          <label className="control">
            <span className="control-label">Latitude</span>
            <input value={lat} onChange={(e) => setLat(e.target.value)} />
          </label>
          <label className="control">
            <span className="control-label">Longitude</span>
            <input value={lon} onChange={(e) => setLon(e.target.value)} />
          </label>
          <label className="control">
            <span className="control-label">Square feet</span>
            <input value={sqft} onChange={(e) => setSqft(e.target.value)} />
          </label>
          <label className="control">
            <span className="control-label">Bedrooms</span>
            <input value={beds} onChange={(e) => setBeds(e.target.value)} />
          </label>
          <label className="control">
            <span className="control-label">Bathrooms</span>
            <input value={baths} onChange={(e) => setBaths(e.target.value)} />
          </label>
        </div>

        <div className="examples">
          {EXAMPLES.map((example) => (
            <button
              key={example.label}
              type="button"
              className="chip"
              onClick={() => {
                setLat(String(example.lat));
                setLon(String(example.lon));
                setSqft(String(example.sqft));
              }}
            >
              {example.label}
            </button>
          ))}
        </div>

        <button type="button" className="primary" onClick={run} disabled={busy}>
          {busy ? "Looking up comps…" : "Estimate from comps"}
        </button>

        {error && <p className="status-error">{error}</p>}

        {result && !result.estimated && (
          <div className="refusal">
            <strong>Not enough comparable sales</strong>
            <p>{result.reason}</p>
            <p className="control-hint">
              This is an honest answer, not an error. Load more county data, or
              try a location inside Cook County or Philadelphia.
            </p>
          </div>
        )}

        {result?.estimated && result.estimate !== null && (
          <div className={`valuation conf-${result.confidence}`}>
            <span className="valuation-range">
              {money(result.low!)} – {money(result.high!)}
            </span>
            <span className="valuation-point">
              midpoint {money(result.estimate)}
              {result.price_per_sqft &&
                ` · ${money(result.price_per_sqft)}/sq ft`}
            </span>
            <span className="valuation-conf">
              {result.confidence} confidence · {result.comps.length} comps
            </span>
            {result.notes.length > 0 && (
              <ul className="valuation-notes">
                {result.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => onUsePrice(Math.round(result.estimate!))}
            >
              Use midpoint as purchase price
            </button>
          </div>
        )}
      </div>

      {result?.comps && result.comps.length > 0 && (
        <CompTable comps={result.comps} />
      )}
    </>
  );
}
