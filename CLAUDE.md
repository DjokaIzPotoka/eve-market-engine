# EVE Market Engine

Two tools on one shared core:
- **Module A — market making advisor**: per-station suggestions for which buy/sell orders to place, at what price, with expected fill rate and profit per day per ISK of capital.
- **Module B — hauling optimizer**: hub-to-hub arbitrage; picks items and quantities that maximize profit under budget, cargo, exit-liquidity and cargo-value constraints.

## Stack
- Python 3.12, managed with `uv`
- `httpx` (async) for ESI, `polars` for transforms, `duckdb` + Parquet for storage
- `pulp` (CBC) for optimization (keep solver behind an interface so OR-Tools can be swapped in)
- `pydantic-settings` for config, `typer` for CLI, `pytest` + `ruff`

## Layout
```
src/eve/
  ingest/        # ESI clients: orders, history, SDE loader
  store/         # duckdb schema, parquet writers, retention
  features/      # best bid/ask, depth levels, order counts, snapshot diffs
  fill/          # fill-rate & competition model (from snapshot diffs)
  fees/          # broker fee, sales tax, relist cost — driven by config
  optimize/      # shared LP/MILP core
  market_making/ # Module A
  hauling/       # Module B
  cli.py
tests/
  fixtures/      # recorded ESI responses — tests never hit the network
config/
  settings.toml  # skills, standings, budget, cargo, station pairs, filters
```

## Key IDs
- The Forge region `10000002`, Jita IV-4 station `60003760`
- Domain region `10000043`, Amarr VIII station `60008494`

## ESI rules
- Base URL `https://esi.evetech.net/latest`, public endpoints only in v1.
- Always send a descriptive `User-Agent` with contact info.
- Respect the `Expires` header — never refetch a resource before it expires.
- Watch `X-ESI-Error-Limit-Remain` / `X-ESI-Error-Limit-Reset`; back off before hitting zero.
- Orders are paginated (`X-Pages`); fetch pages concurrently with bounded concurrency (semaphore).
- Market history updates daily — fetch once per day per (region, type), not per snapshot.
- Item volumes (`packaged_volume`) come from the SDE, not per-type ESI calls.

## Storage rules
- Keep the latest full orders snapshot queryable in duckdb.
- Persist per-snapshot aggregates per (location, type): best bid/ask, depth at N price levels, order counts, top-of-book change counts.
- Raw snapshots go to Parquet partitioned by region/date with configurable retention.

## Domain rules
- Exclude buy orders with `min_volume > 1` from exit liquidity.
- Buy orders with `range != "station"` must be evaluated by their actual range, not treated as local.
- All fee rates live in config; never hardcode tax or broker percentages.
- Solver output must satisfy every constraint — assert after solving; a plan over budget or over cargo is a bug, not a warning.
- Module A ranks by profit per day per ISK of capital tied up, not raw margin.
- Module B ranks lanes by profit per hour (jumps × time per jump), and supports both exit modes: instant (fill buy orders) and listing (capped by a share of daily volume, with broker fee and a time discount).

## Commands
- `uv run pytest`
- `uv run ruff check . && uv run ruff format .`
- `uv run eve ingest --region 10000002`

## Working style
- Work phase by phase; propose a plan before large changes.
- Every new module gets tests using fixtures in `tests/fixtures/`.
- Ask before adding new dependencies.
