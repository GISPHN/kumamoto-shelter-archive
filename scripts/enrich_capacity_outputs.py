#!/usr/bin/env python3
"""Join the persistent portal capacity master to saved shelter CSV outputs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

try:
    from scripts.capacity_matcher import CAPACITY_OUTPUT_COLUMNS, CapacityMatcher
except ModuleNotFoundError:
    from capacity_matcher import CAPACITY_OUTPUT_COLUMNS, CapacityMatcher


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def enrich_file(path: Path, matcher: CapacityMatcher) -> tuple[int, int]:
    columns, rows = read_csv(path)
    if not columns:
        return 0, 0
    for column in CAPACITY_OUTPUT_COLUMNS:
        if column not in columns:
            columns.append(column)
    matched = 0
    for row in rows:
        enrichment = matcher.enrich(row)
        row.update(enrichment)
        if enrichment["capacity_match_status"] == "matched":
            matched += 1
    write_csv(path, columns, rows)
    return matched, len(rows)


def rebuild_all_snapshots(data_root: Path) -> None:
    daily_files = sorted(data_root.glob("daily/*/*/*.csv"))
    if not daily_files:
        return
    all_columns: list[str] = []
    all_rows: list[dict[str, str]] = []
    for path in daily_files:
        columns, rows = read_csv(path)
        for column in columns:
            if column not in all_columns:
                all_columns.append(column)
        all_rows.extend(rows)
    write_csv(data_root / "all_snapshots.csv", all_columns, all_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--capacity-csv", default="reference/portal_shelter_capacity.csv")
    parser.add_argument("--snapshot-date", default="")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    matcher = CapacityMatcher(Path(args.capacity_csv))

    paths: list[Path] = []
    if args.snapshot_date:
        year, month, _ = args.snapshot_date.split("-")
        paths.extend([
            data_root / "daily" / year / month / f"{args.snapshot_date}.csv",
            data_root / "changes" / year / month / f"{args.snapshot_date}.csv",
            data_root / "matching_issues" / year / month / f"{args.snapshot_date}.csv",
        ])
    else:
        paths.extend(sorted(data_root.glob("daily/*/*/*.csv")))
        paths.extend(sorted(data_root.glob("changes/*/*/*.csv")))
        paths.extend(sorted(data_root.glob("matching_issues/*/*/*.csv")))

    paths.extend([
        data_root / "latest.csv",
        data_root / "latest_open.csv",
        data_root / "latest_changes.csv",
        data_root / "latest_matching_issues.csv",
    ])

    seen: set[Path] = set()
    total_matched = 0
    total_rows = 0
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        matched, count = enrich_file(path, matcher)
        total_matched += matched
        total_rows += count
        print(f"Capacity enrichment: {path} matched={matched}/{count}")

    rebuild_all_snapshots(data_root)

    latest = data_root / "latest.csv"
    issues_path = data_root / "latest_capacity_matching_issues.csv"
    if latest.exists():
        columns, rows = read_csv(latest)
        issues = [row for row in rows if row.get("capacity_match_status") != "matched"]
        write_csv(issues_path, columns, issues)
        print(f"Capacity matching issues: {len(issues)} rows -> {issues_path}")

    if matcher.available:
        print(f"Capacity master loaded: {len(matcher.rows)} rows; enriched matches={total_matched}/{total_rows}")
    else:
        print("Capacity master is not available yet; capacity columns were added with master_unavailable status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
