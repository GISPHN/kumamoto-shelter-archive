from pathlib import Path

import pytest

from scripts.snapshot_retention import recent_daily_snapshot_files


def test_returns_only_most_recent_daily_files(tmp_path):
    data_root = tmp_path / "data"
    paths = [
        data_root / "daily" / "2026" / "07" / "2026-07-31.csv",
        data_root / "daily" / "2026" / "08" / "2026-08-01.csv",
        data_root / "daily" / "2026" / "09" / "2026-09-12.csv",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value\n1\n", encoding="utf-8")

    assert recent_daily_snapshot_files(data_root, limit=2) == paths[-2:]


def test_rejects_non_positive_limit(tmp_path):
    with pytest.raises(ValueError, match="at least 1"):
        recent_daily_snapshot_files(Path(tmp_path), limit=0)


def test_stops_before_source_size_budget(tmp_path):
    data_root = tmp_path / "data"
    paths = []
    for day in range(1, 5):
        path = data_root / "daily" / "2026" / "09" / f"2026-09-{day:02d}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        paths.append(path)

    assert recent_daily_snapshot_files(
        data_root,
        limit=30,
        max_source_bytes=25,
    ) == paths[-2:]
