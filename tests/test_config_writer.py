"""Dashboard settings write-back: comments/formatting survive, invalid input never lands."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from eve.config import DEFAULT_CONFIG_PATH, Settings
from eve.ops.config_writer import apply_settings_update
from eve.web.app import create_app
from eve.web.deps import get_config_path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "settings.toml"
    shutil.copy(DEFAULT_CONFIG_PATH, path)
    return path


def _current(config: Path) -> dict:
    s = Settings(_toml_file=config)  # type: ignore[call-arg]
    return {
        "skills": s.skills.model_dump(),
        "standings": s.standings.model_dump(),
        "station_pairs": [p.model_dump() for p in s.station_pairs],
        "filters": s.filters.model_dump(),
        "hauling": {
            k: getattr(s.hauling, k)
            for k in (
                "max_cargo_value_isk",
                "listing_daily_volume_share",
                "default_hold_days",
                "time_per_jump_minutes",
            )
        },
        "market_making": {
            k: getattr(s.market_making, k)
            for k in ("min_near_top_orders", "max_capital_share_per_item")
        },
        "budget": s.budget.model_dump(),
        "cargo": s.cargo.model_dump(),
        "health": {
            k: getattr(s.health, k) for k in ("stale_after_minutes", "error_lookback_hours")
        },
        "ui": s.ui.model_dump(),
    }


def test_unchanged_update_leaves_file_byte_identical(config: Path) -> None:
    before = config.read_bytes()

    apply_settings_update(config, _current(config))

    assert config.read_bytes() == before


def test_edit_changes_values_but_keeps_comments_and_untouched_formatting(config: Path) -> None:
    update = _current(config)
    update["skills"]["accounting"] = 4
    update["standings"]["corporation"] = 5.5
    update["ui"]["haul_actionable_roi_pct"] = 12.0

    saved = apply_settings_update(config, update)

    assert saved.skills.accounting == 4
    assert saved.standings.corporation == 5.5
    text = config.read_text(encoding="utf-8")
    assert "default_isk = 500_000_000" in text  # untouched line keeps its formatting
    assert "# Jita IV-4" in text
    assert "# NPC station broker fee" in text
    assert Settings(_toml_file=config).ui.haul_actionable_roi_pct == 12.0  # type: ignore[call-arg]


def test_missing_ui_section_is_created(config: Path) -> None:
    text = config.read_text(encoding="utf-8")
    config.write_text(text[: text.index("[ui]")], encoding="utf-8")
    update = _current(config)
    update["ui"]["mm_actionable_ppd_per_isk_pct"] = 2.5

    apply_settings_update(config, update)

    assert Settings(_toml_file=config).ui.mm_actionable_ppd_per_isk_pct == 2.5  # type: ignore[call-arg]


def test_station_pairs_can_grow_and_shrink(config: Path) -> None:
    update = _current(config)
    update["station_pairs"].append({"from_station_id": 60008494, "to_station_id": 60003760})
    assert len(apply_settings_update(config, update).station_pairs) == 2

    update["station_pairs"] = update["station_pairs"][:1]
    assert len(apply_settings_update(config, update).station_pairs) == 1


def test_invalid_value_is_rejected_and_file_untouched(config: Path) -> None:
    before = config.read_bytes()
    update = _current(config)
    update["skills"]["accounting"] = 9  # max level is 5

    with pytest.raises(ValidationError):
        apply_settings_update(config, update)

    assert config.read_bytes() == before
    assert list(config.parent.glob("*.tmp")) == []


def _client(config: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_config_path] = lambda: config
    return TestClient(app)


def test_put_settings_roundtrip(config: Path) -> None:
    body = _current(config)
    body["skills"]["trade"] = 3

    response = _client(config).put("/api/settings", json=body)

    assert response.status_code == 200
    assert response.json()["skills"]["trade"] == 3
    assert Settings(_toml_file=config).skills.trade == 3  # type: ignore[call-arg]


def test_put_invalid_value_is_422_with_field_path(config: Path) -> None:
    body = _current(config)
    body["skills"]["trade"] = 9

    response = _client(config).put("/api/settings", json=body)

    assert response.status_code == 422
    assert any("skills" in d["loc"] for d in response.json()["detail"])


def test_put_rejects_unknown_fields(config: Path) -> None:
    body = _current(config)
    body["esi"] = {"contact": "attacker@example.com"}

    assert _client(config).put("/api/settings", json=body).status_code == 422
