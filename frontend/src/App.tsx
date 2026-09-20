/**
 * The dashboard shell: holds the scope, asks Python for the numbers.
 *
 * The one rule this file enforces is that the browser never computes a
 * financial figure. Moving a slider updates the scope and sends it; the
 * numbers on screen are always whatever the engine last returned. That is why
 * there is a `stale` state rather than an optimistic local recalculation.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_SCOPE,
  analyze,
  type DealMetrics,
  type DealScope,
} from "./api/client";
import { ChatPanel } from "./components/ChatPanel";
import { DealDashboard } from "./components/DealDashboard";
import { Guide } from "./components/Guide";
import { ScopeSliders } from "./components/ScopeSliders";
import { ValuationPanel } from "./components/ValuationPanel";

/**
 * How long to wait after the last change before asking the server.
 *
 * A localhost round trip is a millisecond or two, so this is not about load —
 * it is about not firing sixty requests while a slider is being dragged.
 */
const DEBOUNCE_MS = 60;

export default function App() {
  const [scope, setScope] = useState<DealScope>(DEFAULT_SCOPE);
  const [metrics, setMetrics] = useState<DealMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [computeMs, setComputeMs] = useState<number | null>(null);
  const [location, setLocation] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);

  const inFlight = useRef<AbortController | null>(null);

  const request = useCallback(async (next: DealScope) => {
    // Abandon any request still running: a slow reply from an earlier slider
    // position must never overwrite a newer one.
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    const started = performance.now();
    try {
      const response = await analyze(next, controller.signal);
      setMetrics(response.metrics);
      setScope(response.scope);
      setComputeMs(performance.now() - started);
      setError(null);
      setStale(false);
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Request failed");
      setStale(false);
    }
  }, []);

  useEffect(() => {
    setStale(true);
    const timer = window.setTimeout(() => void request(scope), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // Serialising the scope keeps this stable across the nested expenses object.
  }, [JSON.stringify(scope), request]);

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Groundly</h1>
          <p className="tagline">
            Every number below is computed in Python. Nothing here is estimated
            by a model.
          </p>
        </div>
        <div className="status">
          {error ? (
            <span className="status-error">{error}</span>
          ) : (
            <span className={stale ? "status-stale" : "status-live"}>
              {stale
                ? "computing…"
                : computeMs !== null
                  ? `updated in ${computeMs.toFixed(0)} ms`
                  : "ready"}
            </span>
          )}
        </div>
      </header>

      <Guide />

      <main className="layout">
        <div className="column">
          <ChatPanel
            scope={scope}
            onScope={setScope}
            location={location}
            onLocation={setLocation}
          />
          <ScopeSliders scope={scope} onChange={setScope} />
        </div>
        <div className="column">
          {metrics ? (
            <DealDashboard metrics={metrics} />
          ) : (
            <div className="panel placeholder">
              {error
                ? "Could not reach the API. Is uvicorn running on port 8000?"
                : "Loading…"}
            </div>
          )}
          <ValuationPanel
            onUsePrice={(price) =>
              setScope((current) => ({ ...current, purchase_price: price }))
            }
          />
        </div>
      </main>

      <footer className="footer">
        Estimates only. Not financial advice, and not a substitute for your own
        diligence.
      </footer>
    </div>
  );
}
