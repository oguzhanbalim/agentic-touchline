"""Pitch charts as PNG files. Every chart carries the StatsBomb logo and a data credit.

Colour follows a validated palette: blue and orange are the first two categorical slots,
and the sequential ramp for space at reception is a single blue hue. No team colours or
badges are used, so nothing suggests a link with any club or federation.
"""

from __future__ import annotations

import textwrap
from importlib import resources
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors
import matplotlib.image as mimage
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from mplsoccer import Pitch, VerticalPitch

from touchline.config import ATTRIBUTION, DISCLAIMER
from touchline.metrics._sql import (
    IN_PLAY,
    PROGRESSIVE_CARRY,
    PROGRESSIVE_PASS,
    fetch_dicts,
)
from touchline.metrics.definitions import (
    PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS,
    PROGRESSIVE_MIN_RELATIVE_GAIN,
)
from touchline.metrics.network import pass_network
from touchline.metrics.team import match_row

_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_MUTED = "#52514e"
_PITCH_LINE = "#b9b8b2"
_NEUTRAL_MARK = "#c9c8c1"
_BLUE = "#2a78d6"
_ORANGE = "#eb6834"
_BLUE_RAMP = ("#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b")

# Space colours saturate here, so one open reception does not flatten the ramp.
_SPACE_SCALE_MAX_YARDS = 12.0


def _logo_path() -> Path:
    return Path(str(resources.files("touchline") / "assets" / "statsbomb_logo.png"))


def _stamp(fig: Figure) -> None:
    """Add the logo and credit the StatsBomb agreement requires, plus the disclaimer."""
    logo = mimage.imread(_logo_path())
    height_px, width_px = logo.shape[:2]
    fig_width, fig_height = fig.get_size_inches()
    width = 0.17
    height = width * (height_px / width_px) * (fig_width / fig_height)
    ax = fig.add_axes((0.98 - width, 0.018, width, height))
    ax.imshow(logo)
    ax.axis("off")
    fig.text(0.02, 0.040, ATTRIBUTION, fontsize=8.5, color=_INK, ha="left", va="center")
    fig.text(0.02, 0.018, DISCLAIMER, fontsize=6.5, color=_INK_MUTED, ha="left", va="center")


def _titled(fig: Figure, title: str, subtitle: str) -> None:
    fig.text(0.02, 0.968, title, fontsize=14, color=_INK, weight="bold", ha="left", va="center")
    fig.text(
        0.02, 0.915, textwrap.fill(subtitle, 118),
        fontsize=9, color=_INK_MUTED, ha="left", va="center", linespacing=1.3,
    )  # fmt: skip


def _save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=_SURFACE)
    plt.close(fig)
    return path


def _new_figure() -> Figure:
    return plt.figure(figsize=(9.2, 7.2), facecolor=_SURFACE)


def _pitch_axes(fig: Figure, bottom: float = 0.13) -> tuple[Pitch, Axes]:
    pitch = Pitch(
        pitch_type="statsbomb",
        pitch_color=_SURFACE,
        line_color=_PITCH_LINE,
        linewidth=1,
        goal_type="box",
    )
    ax = fig.add_axes((0.03, bottom, 0.94, 0.87 - bottom))
    pitch.draw(ax=ax)
    return pitch, ax


def _match_label(con: duckdb.DuckDBPyConnection, match_id: int) -> str:
    match = match_row(con, match_id)
    return (
        f"{match['home_team']} {match['home_score']}-{match['away_score']} {match['away_team']}, "
        f"{match['competition']} {match['season']}, {match['stage']}"
    )


def pass_network_chart(
    con: duckdb.DuckDBPyConnection, match_id: int, team: str, path: Path
) -> Path:
    """Average positions and pass links for a team before its first change."""
    network = pass_network(con, match_id, team)
    fig = _new_figure()
    pitch, ax = _pitch_axes(fig)
    nodes = {node["player_id"]: node for node in network.nodes}
    busiest = max((link["passes"] for link in network.links), default=1)
    biggest = max((node["touches"] for node in network.nodes), default=1)

    minimum_shown = 3
    for link in network.links:
        if link["passes"] < minimum_shown:
            continue
        a, b = nodes.get(link["player_a"]), nodes.get(link["player_b"])
        if a is None or b is None:
            continue
        pitch.lines(
            a["avg_x"], a["avg_y"], b["avg_x"], b["avg_y"],
            lw=0.6 + 6 * link["passes"] / busiest,
            color=_BLUE, alpha=0.2 + 0.55 * link["passes"] / busiest,
            zorder=2, ax=ax,
        )  # fmt: skip
    for node in network.nodes:
        pitch.scatter(
            node["avg_x"], node["avg_y"],
            s=120 + 500 * node["touches"] / biggest,
            color=_SURFACE, edgecolors=_BLUE, linewidth=2, zorder=3, ax=ax,
        )  # fmt: skip
        label = "" if node["jersey_number"] is None else str(node["jersey_number"])
        pitch.annotate(
            label, (node["avg_x"], node["avg_y"]),
            va="center", ha="center", fontsize=9.5, weight="bold", color=_INK, zorder=4, ax=ax,
        )  # fmt: skip

    legend = "   ".join(
        f"{node['jersey_number']} {node['player']}"
        for node in sorted(network.nodes, key=lambda item: item["jersey_number"] or 0)
    )
    fig.text(
        0.5, 0.098, _wrap(legend, 118), fontsize=7.5, color=_INK_MUTED, ha="center", va="center"
    )
    window = (
        f"before the first change (minute {network.window_end_minute})"
        if network.window_end_minute is not None
        else "whole match, no changes"
    )
    _titled(
        fig,
        f"{team} pass network",
        f"{_match_label(con, match_id)}. Completed open-play passes {window}; "
        f"links of {minimum_shown}+ passes. Attacking to the right.",
    )
    _stamp(fig)
    return _save(fig, path)


def _wrap(text: str, width: int) -> str:
    lines: list[str] = []
    current = ""
    for chunk in text.split("   "):
        candidate = f"{current}   {chunk}" if current else chunk
        if len(candidate) > width and current:
            lines.append(current)
            current = chunk
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def reception_map(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None, path: Path
) -> Path:
    """Every open-play reception, coloured by space, with between-the-lines marked."""
    params: list[int] = [player_id]
    scope = ""
    if match_id is not None:
        scope = "AND match_id = ?"
        params.append(match_id)
    rows = fetch_dicts(
        con,
        f"SELECT x, y, space_yards, between_lines FROM receptions "
        f"WHERE player_id = ? AND open_play {scope}",
        params,
    )
    player = fetch_dicts(
        con, "SELECT any_value(player) AS player FROM lineups WHERE player_id = ?", [player_id]
    )[0]["player"]

    fig = _new_figure()
    pitch, ax = _pitch_axes(fig, bottom=0.175)
    for x in (40, 80):
        ax.plot([x, x], [0, 80], color=_PITCH_LINE, lw=0.6, ls=(0, (2, 3)), zorder=1)
    for y in (18, 30, 50, 62):
        ax.plot([0, 120], [y, y], color=_PITCH_LINE, lw=0.6, ls=(0, (2, 3)), zorder=1)

    unmeasured = [row for row in rows if row["space_yards"] is None]
    measured = [row for row in rows if row["space_yards"] is not None]
    pitch.scatter(
        [row["x"] for row in unmeasured], [row["y"] for row in unmeasured],
        s=26, color=_NEUTRAL_MARK, edgecolors=_SURFACE, linewidth=0.6, zorder=2, ax=ax,
    )  # fmt: skip
    cmap = mcolors.LinearSegmentedColormap.from_list("space", _BLUE_RAMP)
    points = pitch.scatter(
        [row["x"] for row in measured], [row["y"] for row in measured],
        c=[row["space_yards"] for row in measured],
        cmap=cmap, vmin=0, vmax=_SPACE_SCALE_MAX_YARDS,
        s=46, edgecolors=_SURFACE, linewidth=0.8, zorder=3, ax=ax,
    )  # fmt: skip
    between = [row for row in rows if row["between_lines"]]
    pitch.scatter(
        [row["x"] for row in between], [row["y"] for row in between],
        s=96, facecolors="none", edgecolors=_ORANGE, linewidth=1.5, zorder=4, ax=ax,
    )  # fmt: skip

    colorbar_ax = fig.add_axes((0.06, 0.112, 0.26, 0.014))
    colorbar = fig.colorbar(points, cax=colorbar_ax, orientation="horizontal")
    colorbar.ax.tick_params(labelsize=7.5, colors=_INK_MUTED, length=2)
    colorbar.outline.set_visible(False)
    colorbar.ax.xaxis.set_label_position("top")
    colorbar.set_label(
        f"Yards to nearest visible opponent ({_SPACE_SCALE_MAX_YARDS:.0f}+ saturates)",
        fontsize=7.5,
        color=_INK_MUTED,
        labelpad=1,
    )
    handles = [
        Line2D([], [], marker="o", ls="", markerfacecolor="none", markeredgecolor=_ORANGE,
               markeredgewidth=1.5, markersize=9, label="Between the lines (heuristic)"),
        Line2D([], [], marker="o", ls="", markerfacecolor=_NEUTRAL_MARK, markeredgecolor=_SURFACE,
               markersize=6, label="Space not measured (no frame or out of shot)"),
    ]  # fmt: skip
    fig.legend(
        handles=handles, loc="center", bbox_to_anchor=(0.66, 0.122), ncol=2, frameon=False,
        fontsize=7.5, labelcolor=_INK_MUTED,
    )  # fmt: skip
    scope_label = _match_label(con, match_id) if match_id is not None else "All ingested matches"
    _titled(
        fig,
        f"{player}: where the ball was received",
        f"{scope_label}. {len(rows)} completed open-play receptions. Attacking to the right.",
    )
    _stamp(fig)
    return _save(fig, path)


def shot_map(con: duckdb.DuckDBPyConnection, match_id: int, team: str, path: Path) -> Path:
    """A team's shots on the attacking half, sized by StatsBomb xG."""
    rows = fetch_dicts(
        con,
        f"""
        SELECT x, y, shot_xg, shot_outcome FROM events
        WHERE match_id = ? AND team = ? AND type = 'Shot' AND {IN_PLAY}
        """,
        [match_id, team],
    )
    fig = _new_figure()
    pitch = VerticalPitch(
        half=True,
        pitch_type="statsbomb",
        pitch_color=_SURFACE,
        line_color=_PITCH_LINE,
        linewidth=1,
        goal_type="box",
    )
    ax = fig.add_axes((0.08, 0.1, 0.84, 0.78))
    pitch.draw(ax=ax)
    goals = [row for row in rows if row["shot_outcome"] == "Goal"]
    others = [row for row in rows if row["shot_outcome"] != "Goal"]

    def size(xg: float) -> float:
        return 40 + 900 * xg

    pitch.scatter(
        [row["x"] for row in others], [row["y"] for row in others],
        s=[size(row["shot_xg"]) for row in others],
        facecolors="none", edgecolors=_BLUE, linewidth=1.6, zorder=2, ax=ax,
    )  # fmt: skip
    pitch.scatter(
        [row["x"] for row in goals], [row["y"] for row in goals],
        s=[size(row["shot_xg"]) for row in goals],
        color=_ORANGE, edgecolors=_SURFACE, linewidth=1.2, zorder=3, ax=ax,
    )  # fmt: skip
    handles = [
        Line2D([], [], marker="o", ls="", markerfacecolor=_ORANGE, markeredgecolor=_SURFACE,
               markersize=10, label="Goal"),
        Line2D([], [], marker="o", ls="", markerfacecolor="none", markeredgecolor=_BLUE,
               markeredgewidth=1.6, markersize=10, label="No goal"),
    ]  # fmt: skip
    fig.legend(
        handles=handles, loc="center", bbox_to_anchor=(0.5, 0.075), ncol=2, frameon=False,
        fontsize=8, labelcolor=_INK_MUTED,
    )  # fmt: skip
    total = sum(row["shot_xg"] for row in rows)
    _titled(
        fig,
        f"{team} shots",
        f"{_match_label(con, match_id)}. {len(rows)} shots, {total:.2f} xG. Marker area scales "
        "with StatsBomb xG.",
    )
    _stamp(fig)
    return _save(fig, path)


def progressive_map(
    con: duckdb.DuckDBPyConnection, player_id: int, match_id: int | None, path: Path
) -> Path:
    """A player's progressive passes and carries as arrows."""
    params: list[int] = [player_id]
    scope = ""
    if match_id is not None:
        scope = "AND match_id = ?"
        params.append(match_id)
    rows = fetch_dicts(
        con,
        f"""
        SELECT type, x, y,
               CASE WHEN type = 'Pass' THEN pass_end_x ELSE carry_end_x END AS end_x,
               CASE WHEN type = 'Pass' THEN pass_end_y ELSE carry_end_y END AS end_y
        FROM events
        WHERE player_id = ? AND {IN_PLAY} AND ({PROGRESSIVE_PASS} OR {PROGRESSIVE_CARRY}) {scope}
        """,
        params,
    )
    player = fetch_dicts(
        con, "SELECT any_value(player) AS player FROM lineups WHERE player_id = ?", [player_id]
    )[0]["player"]
    fig = _new_figure()
    pitch, ax = _pitch_axes(fig)
    passes = [row for row in rows if row["type"] == "Pass"]
    carries = [row for row in rows if row["type"] == "Carry"]
    for group, colour in ((passes, _BLUE), (carries, _ORANGE)):
        if not group:
            continue
        pitch.arrows(
            [row["x"] for row in group], [row["y"] for row in group],
            [row["end_x"] for row in group], [row["end_y"] for row in group],
            color=colour, width=1.6, headwidth=4.5, headlength=4.5, zorder=2, alpha=0.9, ax=ax,
        )  # fmt: skip
    handles = [
        Line2D([], [], color=_BLUE, lw=2, label=f"Progressive passes ({len(passes)})"),
        Line2D([], [], color=_ORANGE, lw=2, label=f"Progressive carries ({len(carries)})"),
    ]
    fig.legend(
        handles=handles, loc="center", bbox_to_anchor=(0.5, 0.09), ncol=2, frameon=False,
        fontsize=8, labelcolor=_INK_MUTED,
    )  # fmt: skip
    scope_label = _match_label(con, match_id) if match_id is not None else "All ingested matches"
    _titled(
        fig,
        f"{player}: progressive actions",
        f"{scope_label}. Definition: {_short_progressive()}. Attacking to the right.",
    )
    _stamp(fig)
    return _save(fig, path)


def _short_progressive() -> str:
    return (
        f"at least {PROGRESSIVE_MIN_RELATIVE_GAIN:.0%} and "
        f"{PROGRESSIVE_MIN_ABSOLUTE_GAIN_YARDS:.0f} yards closer to goal"
    )
