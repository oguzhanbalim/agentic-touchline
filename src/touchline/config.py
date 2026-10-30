"""Paths and constants shared across the package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAW_DATA_BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

# UEFA Euro 2024, read from data/competitions.json in the open-data repository.
DEFAULT_COMPETITION_ID = 55
DEFAULT_SEASON_ID = 282
# The name the public data uses for the team; the data calls it "Turkey".
DEFAULT_TEAM = "Turkey"

ATTRIBUTION = "Data: StatsBomb Open Data"
DISCLAIMER = (
    "Not endorsed by any club, player or federation. Not affiliated with Anthropic or StatsBomb."
)

# StatsBomb's pitch is 120 x 80 units, nominally yards. Each team attacks towards x = 120.
PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0
GOAL_CENTRE = (PITCH_LENGTH, PITCH_WIDTH / 2)


@dataclass(frozen=True)
class Paths:
    """Where downloaded data and the DuckDB database live."""

    data_dir: Path

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "touchline.duckdb"


def default_paths() -> Paths:
    """Resolve paths from TOUCHLINE_DATA_DIR, or ./data under the working directory."""
    return Paths(Path(os.environ.get("TOUCHLINE_DATA_DIR", "data")))
