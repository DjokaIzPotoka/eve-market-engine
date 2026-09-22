from pathlib import Path

import pytest
from pydantic import ValidationError

from eve.config import DEFAULT_CONFIG_PATH, Settings


def test_loads_default_settings() -> None:
    settings = Settings()

    assert DEFAULT_CONFIG_PATH.exists()
    assert settings.budget.default_isk > 0
    assert settings.cargo.default_m3 > 0
    assert len(settings.station_pairs) >= 1
    assert settings.station_pairs[0].from_station_id == 60003760


def test_invalid_budget_raises_clear_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "settings.toml"
    bad_config.write_text(
        """
        [skills]
        [standings]

        [budget]
        default_isk = -100

        [cargo]
        default_m3 = 5000

        [[station_pairs]]
        from_station_id = 60003760
        to_station_id = 60008494

        [filters]
        """
    )

    with pytest.raises(ValidationError, match="default_isk"):
        Settings(_toml_file=bad_config)
