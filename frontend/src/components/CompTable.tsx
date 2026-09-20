/**
 * The comparable sales behind a valuation, and how much each one counted.
 *
 * The weight column is the point of this table. An estimate a user cannot
 * interrogate is one they have to take on faith, and taking a property
 * valuation on faith is exactly what this project exists not to ask of anyone.
 * Every row shows the distance, the age and the resulting weight, so a comp
 * that looks wrong can be seen to be discounted — or caught not being.
 */

import type { WeightedComp } from "../api/client";

const money = (value: number) =>
  value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

function relativeWidth(weight: number, heaviest: number): string {
  if (heaviest <= 0) return "0%";
  return `${Math.max(2, (weight / heaviest) * 100)}%`;
}

export function CompTable({ comps }: { comps: WeightedComp[] }) {
  if (comps.length === 0) return null;

  const heaviest = Math.max(...comps.map((c) => c.weight));

  return (
    <div className="panel">
      <h2>Comparable sales used</h2>
      <div className="table-scroll">
        <table className="comps">
          <thead>
            <tr>
              <th>Property</th>
              <th className="num">Sold</th>
              <th className="num">Price</th>
              <th className="num">Sq ft</th>
              <th className="num">$/sqft</th>
              <th className="num">Miles</th>
              <th className="num">Age</th>
              <th>Weight</th>
            </tr>
          </thead>
          <tbody>
            {comps.map((comp) => (
              <tr key={`${comp.county}-${comp.parcel_id}`}>
                <td className="comp-name">
                  {comp.address ?? comp.parcel_id}
                  <em>
                    {comp.beds ?? "?"} bd
                    {comp.full_baths !== null && ` · ${comp.full_baths} ba`}
                    {comp.year_built !== null && ` · built ${comp.year_built}`}
                  </em>
                </td>
                <td className="num">{comp.sale_date}</td>
                <td className="num">{money(comp.sale_price)}</td>
                <td className="num">
                  {comp.building_sqft?.toLocaleString() ?? "—"}
                </td>
                <td className="num">
                  {comp.price_per_sqft ? money(comp.price_per_sqft) : "—"}
                </td>
                <td className="num">{comp.distance_miles.toFixed(2)}</td>
                <td className="num">{comp.age_months.toFixed(0)} mo</td>
                <td>
                  <span
                    className="weight-bar"
                    style={{ width: relativeWidth(comp.weight, heaviest) }}
                    title={
                      `distance ${comp.distance_weight.toFixed(2)} × ` +
                      `recency ${comp.recency_weight.toFixed(2)} × ` +
                      `similarity ${comp.similarity_weight.toFixed(2)}`
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="table-note">
        Weight is distance × recency × similarity. Hover a bar for the
        breakdown.
      </p>
    </div>
  );
}
