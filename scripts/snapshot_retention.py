"""Retention rules for the combined shelter snapshot export."""

from __future__ import annotations

from pathlib import Path


# The complete archive remains in data/daily.  The combined convenience export
# is intentionally bounded so that it stays below GitHub's 100 MiB file limit.
ALL_SNAPSHOTS_MAX_DAILY_FILES = 30
ALL_SNAPSHOTS_MAX_SOURCE_BYTES = 90 * 1024 * 1024


def recent_daily_snapshot_files(
    data_root: Path,
    limit: int = ALL_SNAPSHOTS_MAX_DAILY_FILES,
    max_source_bytes: int = ALL_SNAPSHOTS_MAX_SOURCE_BYTES,
) -> list[Path]:
    """Return the most recent daily snapshot files retained in the export."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if max_source_bytes < 1:
        raise ValueError("max_source_bytes must be at least 1")
    daily_files = sorted(data_root.glob("daily/*/*/*.csv"))
    selected: list[Path] = []
    selected_bytes = 0
    for path in reversed(daily_files):
        size = path.stat().st_size
        if selected and selected_bytes + size > max_source_bytes:
            break
        selected.append(path)
        selected_bytes += size
        if len(selected) >= limit:
            break
    return list(reversed(selected))
