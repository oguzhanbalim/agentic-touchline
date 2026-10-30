"""Download StatsBomb Open Data files into the local, gitignored cache.

The StatsBomb agreement does not allow redistribution, so nothing fetched here is
ever committed. Each file is validated as JSON and written atomically, so an
interrupted download cannot leave a truncated file behind.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from touchline.config import RAW_DATA_BASE_URL

Downloader = Callable[[str], bytes]

_TIMEOUT_SECONDS = 120
_ATTEMPTS = 3


def _download(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise FileNotFoundError(f"not in StatsBomb Open Data: {url}") from error
            last_error = error
        except OSError as error:
            last_error = error
        time.sleep(2**attempt)
    raise ConnectionError(f"could not download {url}: {last_error}")


class OpenDataCache:
    """Read-through cache keyed on the path inside the open-data repository."""

    def __init__(self, cache_dir: Path, downloader: Downloader = _download) -> None:
        self._cache_dir = cache_dir
        self._downloader = downloader

    def load(self, relative_path: str, refresh: bool = False) -> Any:
        target = self._cache_dir / relative_path
        if refresh or not target.exists():
            raw = self._downloader(f"{RAW_DATA_BASE_URL}/{relative_path}")
            parsed = json.loads(raw)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(f".partial{os.getpid()}")
            partial.write_bytes(raw)
            partial.replace(target)
            return parsed
        with target.open(encoding="utf-8") as handle:
            return json.load(handle)

    def competitions(self) -> Any:
        return self.load("competitions.json")

    def matches(self, competition_id: int, season_id: int) -> Any:
        return self.load(f"matches/{competition_id}/{season_id}.json")

    def events(self, match_id: int) -> Any:
        return self.load(f"events/{match_id}.json")

    def lineups(self, match_id: int) -> Any:
        return self.load(f"lineups/{match_id}.json")

    def frames(self, match_id: int) -> Any:
        """Return 360 frames for a match, or an empty list when none were published."""
        try:
            return self.load(f"three-sixty/{match_id}.json")
        except FileNotFoundError:
            return []
