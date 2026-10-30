"""Regenerate docs/metrics.md from the metric registry."""

from __future__ import annotations

from pathlib import Path

from touchline.metrics.definitions import render_definitions_markdown


def main() -> None:
    Path("docs/metrics.md").write_text(render_definitions_markdown(), encoding="utf-8")


if __name__ == "__main__":
    main()
