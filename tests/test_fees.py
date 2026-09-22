"""Tests for broker fee, sales tax, and relist cost -- hand-calculated skill combinations."""

from __future__ import annotations

from eve.config import Fees, Skills, Standings
from eve.fees.fees import (
    broker_fee_isk,
    broker_fee_pct,
    is_structure_location,
    relist_fee_isk,
    sales_tax_isk,
    sales_tax_pct,
)

FEES = Fees()


def _skills(**overrides: int) -> Skills:
    return Skills(**overrides)


def _standings(**overrides: float) -> Standings:
    return Standings(**overrides)


def test_broker_fee_pct_with_no_skills_or_standings_is_base_rate() -> None:
    pct = broker_fee_pct(_skills(), _standings(), FEES)

    assert pct == 3.0


def test_broker_fee_pct_at_broker_relations_v_matches_independent_source() -> None:
    # No access to the live EVE client (same caveat as Faza 3) -- cross-checked instead
    # against an independently written reference: "With broker relations V, the broker's
    # fee is reduced to 1.5%" (no standings involved).
    pct = broker_fee_pct(_skills(broker_relations=5), _standings(), FEES)

    assert pct == 1.5


def test_broker_fee_isk_matches_independent_worked_example() -> None:
    # Independent worked example: a 2,000,000 ISK sell order at the 3% base rate is
    # charged exactly 60,000 ISK.
    fee = broker_fee_isk(2_000_000, pct=3.0, fees=FEES)

    assert fee == 60_000.0


def test_broker_fee_pct_combines_skill_and_standings_reductions() -> None:
    # 3 - 0.3*4 - 0.03*5.0 - 0.02*3.0 = 3 - 1.2 - 0.15 - 0.06 = 1.59
    pct = broker_fee_pct(
        _skills(broker_relations=4),
        _standings(faction=5.0, corporation=3.0),
        FEES,
    )

    assert round(pct, 6) == 1.59


def test_broker_fee_pct_floors_at_minimum_even_with_max_skills_and_standings() -> None:
    # raw = 3 - 0.3*5 - 0.03*10 - 0.02*10 = 3 - 1.5 - 0.3 - 0.2 = 1.0 -- exactly the floor
    pct = broker_fee_pct(
        _skills(broker_relations=5),
        _standings(faction=10.0, corporation=10.0),
        FEES,
    )

    assert pct == 1.0


def test_broker_fee_pct_rises_above_base_with_bad_standings() -> None:
    # raw = 3 - 0 - 0.03*(-10) - 0.02*(-10) = 3 + 0.3 + 0.2 = 3.5 -- no ceiling, floor doesn't bite
    pct = broker_fee_pct(_skills(), _standings(faction=-10.0, corporation=-10.0), FEES)

    assert round(pct, 6) == 3.5


def test_broker_fee_isk_applies_the_isk_floor_on_small_orders() -> None:
    # 1000 ISK order at 3% would be 30 ISK, well under the 100 ISK minimum
    fee = broker_fee_isk(1000, pct=3.0, fees=FEES)

    assert fee == 100.0


def test_broker_fee_isk_uses_the_computed_percentage_on_large_orders() -> None:
    fee = broker_fee_isk(100_000_000, pct=1.59, fees=FEES)

    assert round(fee, 2) == 1_590_000.0


def test_broker_fee_pct_on_a_structure_ignores_skills_and_standings() -> None:
    pct = broker_fee_pct(
        _skills(broker_relations=5),
        _standings(faction=10.0, corporation=10.0),
        FEES,
        is_structure=True,
    )

    # 0.5% base + 0% assumed owner cut (default config)
    assert pct == 0.5


def test_broker_fee_pct_on_a_structure_uses_explicit_owner_pct_override() -> None:
    pct = broker_fee_pct(
        _skills(), _standings(), FEES, is_structure=True, structure_owner_fee_pct=1.5
    )

    assert pct == 2.0


def test_is_structure_location_distinguishes_stations_from_structures() -> None:
    assert is_structure_location(60003760) is False  # Jita IV-4, NPC station
    assert is_structure_location(1_035_466_617_946) is True  # citizen structure range


def test_sales_tax_pct_with_no_accounting_is_base_rate() -> None:
    assert sales_tax_pct(_skills(), FEES) == 7.5


def test_sales_tax_pct_at_accounting_v() -> None:
    # 7.5 * (1 - 0.11*5) = 7.5 * 0.45 = 3.375
    pct = sales_tax_pct(_skills(accounting=5), FEES)

    assert round(pct, 6) == 3.375


def test_sales_tax_isk() -> None:
    tax = sales_tax_isk(100_000_000, _skills(accounting=5), FEES)

    assert round(tax, 2) == 3_375_000.0


def test_relist_fee_isk_with_no_advanced_broker_relations() -> None:
    # broker_fee_pct = 3.0 (no skills), discount = 50%
    # (1-0.5) * 0.03 * 1_100_000 + 0.03 * max(1_100_000-1_000_000, 0)
    # = 0.5*0.03*1_100_000 + 0.03*100_000 = 16_500 + 3_000 = 19_500
    fee = relist_fee_isk(1_000_000, 1_100_000, _skills(), _standings(), FEES)

    assert round(fee, 2) == 19_500.0


def test_relist_fee_isk_with_advanced_broker_relations_v() -> None:
    # discount = 50 + 6*5 = 80% -- broker_fee_pct still 3.0 (advanced_br doesn't affect it)
    # (1-0.8) * 0.03 * 1_100_000 + 0.03 * 100_000 = 6_600 + 3_000 = 9_600
    fee = relist_fee_isk(
        1_000_000, 1_100_000, _skills(advanced_broker_relations=5), _standings(), FEES
    )

    assert round(fee, 2) == 9_600.0


def test_relist_fee_isk_applies_the_isk_floor() -> None:
    fee = relist_fee_isk(1000, 1000, _skills(), _standings(), FEES)

    assert fee == 100.0


def test_relist_fee_isk_only_charges_broker_fee_on_price_decrease() -> None:
    # new_value < old_value -- max(new-old, 0) drops to 0, only the base discount term applies
    # (1-0.5) * 0.03 * 500_000 = 7_500 -- above the 100 ISK floor
    fee = relist_fee_isk(1_000_000, 500_000, _skills(), _standings(), FEES)

    assert round(fee, 2) == 7_500.0
