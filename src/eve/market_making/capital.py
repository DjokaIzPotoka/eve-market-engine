"""Order-slot count and buy-order escrow -- both skill-driven, neither a fee.

Order slots: base 5 + Trade/Retail/Wholesale/Tycoon, each adding a fixed number of slots
per level (EVE University wiki, "Trading"). Escrow: Margin Trading multiplies the ISK
required upfront for a buy order by a fixed factor per level (~23.7% of order value at
level V, per the same source). CCP is phasing Margin Trading out in favor of Advanced
Broker Relations, but it's already modeled in `Skills` since Faza 0, so it's honored here.
"""

from __future__ import annotations

from eve.config import MarketMaking, Skills


def max_order_slots(skills: Skills, market_making: MarketMaking) -> int:
    return (
        market_making.base_order_slots
        + market_making.trade_slots_per_level * skills.trade
        + market_making.retail_slots_per_level * skills.retail
        + market_making.wholesale_slots_per_level * skills.wholesale
        + market_making.tycoon_slots_per_level * skills.tycoon
    )


def buy_order_escrow_pct(skills: Skills, market_making: MarketMaking) -> float:
    """ISK required upfront for a buy order, as a percentage of its value (100.0 = full)."""
    factor = market_making.margin_trading_reduction_factor_per_level**skills.margin_trading
    return 100.0 * factor
