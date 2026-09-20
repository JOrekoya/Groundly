# Running Groundly locally

Two processes: the FastAPI engine and the Vite dashboard. The dashboard proxies
`/api` to the backend, so nothing needs configuring.

## One-time setup

```
python -m pip install -e ".[dev]"      # or: pip install fastapi uvicorn pytest
cd frontend && npm install
```

## Every time

**Terminal 1 — the engine:**

```
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://localhost:8000/docs>

**Terminal 2 — the dashboard:**

```
cd frontend
npm run dev
```

Open <http://localhost:5173>.

> On Windows, Vite binds to `localhost` over IPv6. Use `http://localhost:5173`
> rather than `http://127.0.0.1:5173`, which will refuse the connection.

## Without the browser

The dashboard is a convenience, not the product surface. The same engine is
reachable from the command line:

```
python backend/analyze_deal.py --price 300000 --rent 2800 --rate 0.07
```

## Checks

```
python -m pytest                 # backend, no network
python -m pyright                # type check
cd frontend && npm run typecheck # frontend types
cd frontend && npm run build     # production build
```
