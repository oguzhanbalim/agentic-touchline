"""Charts render real PNG files and always carry the data credit."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pytest

import touchline.charts as charts
from synthetic import ALPHA, FORWARD_A, MATCH_ID, MID_A
from touchline.config import ATTRIBUTION

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _render_all(con, directory: Path) -> list[Path]:
    return [
        charts.pass_network_chart(con, MATCH_ID, ALPHA, directory / "network.png"),
        charts.shot_map(con, MATCH_ID, ALPHA, directory / "shots.png"),
        charts.reception_map(con, FORWARD_A, MATCH_ID, directory / "receptions.png"),
        charts.reception_map(con, FORWARD_A, None, directory / "receptions_all.png"),
        charts.progressive_map(con, MID_A, MATCH_ID, directory / "progressive.png"),
    ]


def test_charts_are_png_files(con, tmp_path: Path) -> None:
    for path in _render_all(con, tmp_path):
        assert path.read_bytes().startswith(PNG_MAGIC)
        assert path.stat().st_size > 10_000


def test_every_chart_gets_logo_and_credit(con, tmp_path: Path, monkeypatch) -> None:
    captured: list[list[str]] = []
    original_save = charts._save

    def spy(fig, path):
        captured.append([text.get_text() for text in fig.texts])
        images = [image for ax in fig.axes for image in ax.get_images()]
        assert images, "the StatsBomb logo must be on the chart"
        return original_save(fig, path)

    monkeypatch.setattr(charts, "_save", spy)
    _render_all(con, tmp_path)
    assert len(captured) == 5
    for texts in captured:
        assert ATTRIBUTION in texts
        assert any("Not affiliated" in text for text in texts)


def test_charts_handle_a_player_with_no_actions(con, tmp_path: Path) -> None:
    path = charts.progressive_map(con, FORWARD_A, MATCH_ID, tmp_path / "empty.png")
    assert path.exists()
    assert plt.get_fignums() == [], "figures must be closed after saving"


def test_unknown_match_raises(con, tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        charts.shot_map(con, 999, ALPHA, tmp_path / "x.png")
