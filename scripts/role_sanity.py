"""Print between-the-lines rate and median space by role group, to sanity-check the heuristics.

Run after `touchline ingest`. The rates should rise from centre backs to strikers, and
median space should fall. Nothing here is a validation against human labels.
"""

from __future__ import annotations

from touchline.config import default_paths
from touchline.db import connect
from touchline.metrics.player import ROLE_GROUPS


def main() -> None:
    con = connect(default_paths().db_path)
    print("| Role | Between the lines | Assessable | Rate % | Median space, yards | Measured |")
    print("| --- | --- | --- | --- | --- | --- |")
    for role in ("centre_back", "defensive_midfield", "winger", "striker"):
        row = con.execute(
            """
            SELECT count(*) FILTER (r.between_lines), count(r.between_lines),
                   round(100.0 * count(*) FILTER (r.between_lines) / count(r.between_lines), 1),
                   round(median(r.space_yards), 1), count(r.space_yards)
            FROM receptions r JOIN events e USING (event_id)
            WHERE r.open_play AND list_contains(?, e.position)
            """,
            [list(ROLE_GROUPS[role])],
        ).fetchone()
        assert row is not None
        print(f"| {role} | " + " | ".join(str(value) for value in row) + " |")
    con.close()


if __name__ == "__main__":
    main()
