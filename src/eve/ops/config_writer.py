"""Write dashboard-edited values back into `config/settings.toml`.

Only the keys the dashboard exposes are touched, and only when their value actually changed:
`tomlkit` keeps comments, key order and untouched formatting (e.g. `500_000_000`), so the
hand-annotated file (fee formula sources, `# Jita IV-4` markers) survives an edit. The result
is validated by loading it as `Settings` *before* it replaces the real file, and the
replacement is atomic so a cron run never reads a half-written config.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Table

from eve.config import Settings

# Sections whose keys are all editable as a flat table.
_TABLE_SECTIONS = (
    "skills",
    "standings",
    "filters",
    "hauling",
    "market_making",
    "budget",
    "cargo",
    "health",
    "ui",
)


def _set_table(doc: tomlkit.TOMLDocument, section: str, values: dict[str, Any]) -> None:
    if section not in doc:
        doc[section] = tomlkit.table()
    table = doc[section]
    assert isinstance(table, Table)
    for key, value in values.items():
        if key not in table or table[key] != value:
            table[key] = value


def _set_station_pairs(doc: tomlkit.TOMLDocument, pairs: list[dict[str, Any]]) -> None:
    aot = doc.get("station_pairs")
    if not isinstance(aot, AoT):
        aot = tomlkit.aot()
        doc["station_pairs"] = aot
    for i, pair in enumerate(pairs):
        if i < len(aot):
            for key, value in pair.items():
                if aot[i].get(key) != value:
                    aot[i][key] = value
        else:
            entry = tomlkit.table()
            for key, value in pair.items():
                entry[key] = value
            aot.append(entry)
    while len(aot) > len(pairs):
        del aot[-1]


def apply_settings_update(path: Path, update: dict[str, Any]) -> Settings:
    """Merge `update` (the dashboard's editable subset) into the TOML at `path`.

    Raises `pydantic.ValidationError` (file untouched) when the merged config is invalid.
    Returns the freshly loaded `Settings`.
    """
    raw = path.read_bytes().decode("utf-8")
    doc = tomlkit.parse(raw)

    for section in _TABLE_SECTIONS:
        if section in update:
            _set_table(doc, section, update[section])
    if "station_pairs" in update:
        _set_station_pairs(doc, update["station_pairs"])

    new_text = tomlkit.dumps(doc)
    if new_text == raw:
        return Settings(_toml_file=path)  # type: ignore[call-arg]

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_text.encode("utf-8"))
        validated = Settings(_toml_file=tmp)  # type: ignore[call-arg]
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return validated
