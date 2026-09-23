# EVE Market Engine

Market making advisor and hauling optimizer for EVE Online. See `CLAUDE.md` for the
architecture/stack overview and `PLAN.md` for the phase-by-phase build plan.

## Web dashboard

`uv run eve serve` starts a local dashboard at `http://127.0.0.1:8000`: health status,
forms to trigger `mm`/`haul` from the browser (each saved run gets a link to log a trade
against it), the trade log, the expected-vs-actual report, and a per-item order-book
lookup. It's safe to leave running alongside the cron ingest job below -- each HTTP
request opens its own short-lived duckdb connection, same as every CLI command.

## Operations

Periodic ingest runs via cron, not a Python daemon (see `scripts/cron_ingest.sh`):

1. `crontab -e` and paste the lines from `scripts/crontab.example` (with an absolute path
   to this repo) -- orders every 10 minutes, daily history at 03:00 UTC.
2. Watch it run: `tail -f data/logs/ingest.log` (and `data/logs/ingest-history.log`).
3. Check status any time with `uv run eve health` -- reports each region's last snapshot
   age and order count, and any ingest errors from the last `health.error_lookback_hours`
   (default 24h). Exits non-zero if anything is stale or errored, so it's safe to wire
   into your own alerting.

## Tracking real trades

`eve mm`/`eve haul` save every suggested plan and print its `run_id`. Log what you
actually did against it:

```bash
uv run eve log-trade --run-id <run_id> --type-id 34 --quantity 500000 \
    --buy-price 5.02 --sell-price 6.48
```

Then `uv run eve report` shows expected vs. actual profit, per item and per plan run.
`--run-id` is optional -- trades logged without one still show up under actual profit,
just without an expected-profit comparison.
