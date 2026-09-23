"""Tests for log_trade / report_by_item / report_by_run: expected-vs-actual arithmetic
on hand-worked examples (same style as test_fees.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from eve.ops.trades import log_trade, report_by_item, report_by_run
from eve.store.duckdb_store import DuckDBStore

RUN_ID = "run-1"
TYPE_WITH_PLAN = 34
TYPE_NO_PLAN = 99


def _seed_plan(store: DuckDBStore) -> None:
    store.write_plan_run(RUN_ID, "haul", datetime.now(UTC), 60003760, 60008494, 999.0, "per_run")
    store.write_plan_lines(
        RUN_ID,
        [
            {
                "type_id": TYPE_WITH_PLAN,
                "type_name": "Tritanium",
                "suggested_quantity": 1000.0,
                "suggested_buy_price": 5.0,
                "suggested_sell_price": 8.0,
            }
        ],
    )


def test_log_trade_writes_a_row(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_plan(store)
        trade_id = log_trade(
            store,
            type_id=TYPE_WITH_PLAN,
            quantity=100.0,
            buy_price=5.1,
            sell_price=7.9,
            run_id=RUN_ID,
        )
        rows = store._con.execute("SELECT trade_id, run_id, type_id FROM trades").fetchall()

    assert rows == [(trade_id, RUN_ID, TYPE_WITH_PLAN)]


def test_report_by_item_computes_expected_and_actual(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_plan(store)
        # expected = (8.0 - 5.0) * 100 = 300.0; actual = (7.9 - 5.1) * 100 = 280.0
        log_trade(
            store,
            type_id=TYPE_WITH_PLAN,
            quantity=100.0,
            buy_price=5.1,
            sell_price=7.9,
            run_id=RUN_ID,
        )
        # no plan reference -- expected stays None/0, actual = (3.0 - 2.0) * 50 = 50.0
        log_trade(store, type_id=TYPE_NO_PLAN, quantity=50.0, buy_price=2.0, sell_price=3.0)

        by_item = {row["type_id"]: row for row in report_by_item(store)}

    assert by_item[TYPE_WITH_PLAN]["expected_profit"] == pytest.approx(300.0)
    assert by_item[TYPE_WITH_PLAN]["actual_profit"] == pytest.approx(280.0)
    assert by_item[TYPE_WITH_PLAN]["trade_count"] == 1

    assert by_item[TYPE_NO_PLAN]["actual_profit"] == pytest.approx(50.0)
    assert by_item[TYPE_NO_PLAN]["trade_count"] == 1


def test_report_by_run_groups_unreferenced_trades_under_no_run(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_plan(store)
        log_trade(
            store,
            type_id=TYPE_WITH_PLAN,
            quantity=100.0,
            buy_price=5.1,
            sell_price=7.9,
            run_id=RUN_ID,
        )
        log_trade(store, type_id=TYPE_NO_PLAN, quantity=50.0, buy_price=2.0, sell_price=3.0)

        by_run = {row["run_id"]: row for row in report_by_run(store)}

    assert by_run[RUN_ID]["expected_profit"] == pytest.approx(300.0)
    assert by_run[RUN_ID]["actual_profit"] == pytest.approx(280.0)
    assert by_run[None]["actual_profit"] == pytest.approx(50.0)


def test_report_since_filters_out_older_trades(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_plan(store)
        log_trade(
            store,
            type_id=TYPE_WITH_PLAN,
            quantity=100.0,
            buy_price=5.1,
            sell_price=7.9,
            run_id=RUN_ID,
        )

        future_cutoff = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
        by_item = report_by_item(store, since=future_cutoff)

    assert by_item == []
