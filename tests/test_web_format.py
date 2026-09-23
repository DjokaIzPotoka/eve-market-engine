"""Tests for the dashboard's display filters."""

from __future__ import annotations

from eve.web.templating import num, station_label


def test_num_adds_thousands_separators_and_fixed_decimals() -> None:
    assert num(1234567.891, 2) == "1,234,567.89"
    assert num(1234567.891) == "1,234,568"
    assert num(999) == "999"
    assert num(0) == "0"


def test_num_handles_none() -> None:
    assert num(None, 2) == "n/a"


def test_station_label_uses_name_and_falls_back_to_id() -> None:
    assert station_label(60003760, {60003760: "Jita IV - Moon 4"}) == "Jita IV - Moon 4"
    assert station_label(60003760, {}) == "60003760"
    assert station_label(60003760, None) == "60003760"
    assert station_label(None, {}) == ""
