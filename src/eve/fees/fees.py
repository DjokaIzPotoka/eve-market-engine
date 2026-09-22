"""Broker fee, sales tax, and relist cost -- all coefficients from config, never hardcoded.

Formulas match the current (post-2022 CCP rework) EVE Online mechanics, cross-checked
against the EVE University wiki (Trading, Tax pages):

  broker fee % (NPC station) = max(min_pct,
      base_pct
      - broker_relations_reduction * BR_level
      - faction_reduction * faction_standing
      - corp_reduction * corp_standing)

  broker fee % (Upwell structure) = structure_base_pct + owner_pct
      (skills/standings do not apply; owner_pct is unknowable without authenticated
      ESI -- out of v1 scope per CLAUDE.md -- so it's an assumed config default)

  sales tax % = sales_tax_base_pct * (1 - accounting_fraction_per_level * Accounting_level)

  relist fee = (1 - (relist_discount_base + relist_discount_per_level * AdvBR_level))
               * broker_fee_pct * new_value
               + broker_fee_pct * max(new_value - old_value, 0)

Both broker fee and relist fee are floored at their respective minimum ISK amounts.
"""

from __future__ import annotations

from eve.config import Fees, Skills, Standings
from eve.store.duckdb_store import MAX_NPC_STATION_ID


def is_structure_location(location_id: int) -> bool:
    """Player-owned Upwell structures use IDs far above any NPC station ID."""
    return location_id >= MAX_NPC_STATION_ID


def broker_fee_pct(
    skills: Skills,
    standings: Standings,
    fees: Fees,
    *,
    is_structure: bool = False,
    structure_owner_fee_pct: float | None = None,
) -> float:
    """Broker fee as a percentage (3.0 means 3%)."""
    if is_structure:
        owner_pct = (
            fees.structure_owner_fee_pct
            if structure_owner_fee_pct is None
            else structure_owner_fee_pct
        )
        return fees.structure_broker_fee_base_pct + owner_pct

    raw_pct = (
        fees.broker_fee_base_pct
        - fees.broker_relations_reduction_pct_per_level * skills.broker_relations
        - fees.faction_standing_reduction_pct_per_point * standings.faction
        - fees.corp_standing_reduction_pct_per_point * standings.corporation
    )
    return max(raw_pct, fees.broker_fee_min_pct)


def broker_fee_isk(order_value: float, pct: float, fees: Fees) -> float:
    """Broker fee in ISK for placing/increasing an order of `order_value`, with the ISK floor."""
    return max(order_value * pct / 100, fees.broker_fee_min_isk)


def sales_tax_pct(skills: Skills, fees: Fees) -> float:
    """Sales tax as a percentage (7.5 means 7.5%)."""
    return fees.sales_tax_base_pct * (
        1 - fees.accounting_reduction_fraction_per_level * skills.accounting
    )


def sales_tax_isk(sale_value: float, skills: Skills, fees: Fees) -> float:
    """Sales tax in ISK on a completed sale of `sale_value`."""
    return sale_value * sales_tax_pct(skills, fees) / 100


def relist_fee_isk(
    old_value: float,
    new_value: float,
    skills: Skills,
    standings: Standings,
    fees: Fees,
    *,
    is_structure: bool = False,
) -> float:
    """Cost of modifying an existing order's price/volume, with the ISK floor."""
    pct = broker_fee_pct(skills, standings, fees, is_structure=is_structure)
    discount_pct = min(
        fees.relist_discount_base_pct
        + fees.relist_discount_pct_per_adv_br_level * skills.advanced_broker_relations,
        100.0,
    )
    raw = (100 - discount_pct) / 100 * pct / 100 * new_value + pct / 100 * max(
        new_value - old_value, 0
    )
    return max(raw, fees.relist_fee_min_isk)
