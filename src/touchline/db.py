"""DuckDB schema and connection helpers."""

from __future__ import annotations

from pathlib import Path

import duckdb

DDL = """
CREATE OR REPLACE TABLE matches (
    match_id BIGINT PRIMARY KEY,
    competition_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    competition VARCHAR NOT NULL,
    season VARCHAR NOT NULL,
    match_date DATE NOT NULL,
    stage VARCHAR NOT NULL,
    home_team VARCHAR NOT NULL,
    away_team VARCHAR NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL
);
CREATE OR REPLACE TABLE lineups (
    match_id BIGINT NOT NULL,
    team VARCHAR NOT NULL,
    player_id BIGINT NOT NULL,
    player VARCHAR NOT NULL,
    jersey_number INTEGER,
    sent_off_period INTEGER,
    sent_off_minute INTEGER,
    sent_off_second INTEGER
);
CREATE OR REPLACE TABLE player_minutes (
    match_id BIGINT NOT NULL,
    team VARCHAR NOT NULL,
    player_id BIGINT NOT NULL,
    position VARCHAR NOT NULL,
    minutes DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE events (
    event_id VARCHAR PRIMARY KEY,
    match_id BIGINT NOT NULL,
    idx INTEGER NOT NULL,
    period INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    second INTEGER NOT NULL,
    type VARCHAR NOT NULL,
    possession INTEGER NOT NULL,
    possession_team VARCHAR NOT NULL,
    play_pattern VARCHAR NOT NULL,
    team VARCHAR NOT NULL,
    player_id BIGINT,
    player VARCHAR,
    position VARCHAR,
    x DOUBLE,
    y DOUBLE,
    under_pressure BOOLEAN NOT NULL,
    pass_recipient_id BIGINT,
    pass_recipient VARCHAR,
    pass_length DOUBLE,
    pass_end_x DOUBLE,
    pass_end_y DOUBLE,
    pass_height VARCHAR,
    pass_type VARCHAR,
    pass_outcome VARCHAR,
    pass_cross BOOLEAN,
    pass_through_ball BOOLEAN,
    pass_shot_assist BOOLEAN,
    pass_goal_assist BOOLEAN,
    carry_end_x DOUBLE,
    carry_end_y DOUBLE,
    shot_xg DOUBLE,
    shot_outcome VARCHAR,
    shot_type VARCHAR,
    shot_body_part VARCHAR,
    shot_end_x DOUBLE,
    shot_end_y DOUBLE
);
CREATE OR REPLACE TABLE frames (
    event_id VARCHAR PRIMARY KEY,
    match_id BIGINT NOT NULL,
    visible_area DOUBLE[] NOT NULL
);
CREATE OR REPLACE TABLE frame_players (
    event_id VARCHAR NOT NULL,
    match_id BIGINT NOT NULL,
    teammate BOOLEAN NOT NULL,
    actor BOOLEAN NOT NULL,
    keeper BOOLEAN NOT NULL,
    x DOUBLE NOT NULL,
    y DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE receptions (
    event_id VARCHAR PRIMARY KEY,
    match_id BIGINT NOT NULL,
    team VARCHAR NOT NULL,
    player_id BIGINT NOT NULL,
    period INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    x DOUBLE NOT NULL,
    y DOUBLE NOT NULL,
    open_play BOOLEAN NOT NULL,
    frame_status VARCHAR NOT NULL,
    space_yards DOUBLE,
    space_censored BOOLEAN NOT NULL,
    visible_outfield_opponents INTEGER NOT NULL,
    between_lines BOOLEAN
);
"""


def connect(db_path: Path, *, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open the database. Everything except ingest uses a read-only connection."""
    if read_only and not db_path.exists():
        raise FileNotFoundError(f"no database at {db_path}; run `touchline ingest` first")
    return duckdb.connect(str(db_path), read_only=read_only)
