#!/usr/bin/env python3
"""Validate the persistent capacity master and its enrichment outputs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def count_values(rows: list[dict[str, str]], column: str) -> dict[str, int]:
    counts = Counter((row.get(column) or "(blank)") for row in rows)
    return dict(sorted(counts.items()))


def validate_enriched_rows(
    label: str,
    rows: list[dict[str, str]],
    columns: list[str],
    master_by_id: dict[str, dict[str, str]],
) -> dict[str, object]:
    required_columns = {
        "portal_shelter_id",
        "portal_capacity_persons",
        "portal_capacity_raw",
        "capacity_source",
        "capacity_acquired_at_jst",
        "capacity_match_status",
        "capacity_match_method",
        "portal_latitude",
        "portal_longitude",
    }
    missing_columns = sorted(required_columns - set(columns))
    require(not missing_columns, f"{label} missing capacity columns: {missing_columns}")

    matched_rows = [row for row in rows if row.get("capacity_match_status") == "matched"]
    master_unavailable = [
        row for row in rows if row.get("capacity_match_status") == "master_unavailable"
    ]
    require(
        not master_unavailable,
        f"{label} still contains master_unavailable rows: {len(master_unavailable)}",
    )

    unknown_master_ids: list[str] = []
    mismatched_capacity: list[dict[str, str]] = []
    for row in matched_rows:
        portal_id = row.get("portal_shelter_id", "")
        master = master_by_id.get(portal_id)
        if master is None:
            unknown_master_ids.append(portal_id)
            continue
        if row.get("portal_capacity_persons", "") != master.get(
            "portal_capacity_persons", ""
        ):
            mismatched_capacity.append(
                {
                    "portal_shelter_id": portal_id,
                    "output_capacity": row.get("portal_capacity_persons", ""),
                    "master_capacity": master.get("portal_capacity_persons", ""),
                }
            )

    require(
        not unknown_master_ids,
        f"{label} matched rows reference unknown master IDs: {unknown_master_ids[:10]}",
    )
    require(
        not mismatched_capacity,
        f"{label} capacity differs from master: {mismatched_capacity[:10]}",
    )

    return {
        "row_count": len(rows),
        "capacity_match_status_counts": count_values(rows, "capacity_match_status"),
        "capacity_match_method_counts": count_values(rows, "capacity_match_method"),
        "matched_row_count": len(matched_rows),
        "matched_with_positive_capacity_count": sum(
            bool(row.get("portal_capacity_persons")) for row in matched_rows
        ),
        "matched_with_source_coordinates_count": sum(
            bool(row.get("portal_latitude")) and bool(row.get("portal_longitude"))
            for row in matched_rows
        ),
        "unknown_master_id_count": len(unknown_master_ids),
        "capacity_mismatch_count": len(mismatched_capacity),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--master", default="reference/portal_shelter_capacity.csv"
    )
    parser.add_argument(
        "--metadata", default="reference/portal_shelter_capacity_metadata.json"
    )
    parser.add_argument("--latest", default="data/latest.csv")
    parser.add_argument("--status-by-date", default="data/status_by_date.csv")
    parser.add_argument(
        "--output", default="data/logs/capacity_validation.json"
    )
    parser.add_argument("--minimum-master-records", type=int, default=2000)
    args = parser.parse_args()

    master_columns, master_rows = read_csv(Path(args.master))
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))

    required_master_columns = {
        "portal_shelter_id",
        "municipality_code",
        "municipality",
        "shelter_name",
        "address",
        "portal_latitude",
        "portal_longitude",
        "portal_capacity_persons",
        "portal_capacity_raw",
        "capacity_parse_status",
        "capacity_source",
        "capacity_acquired_at_jst",
        "source_url",
    }
    missing_master_columns = sorted(required_master_columns - set(master_columns))
    require(
        not missing_master_columns,
        f"Capacity master missing columns: {missing_master_columns}",
    )
    require(
        len(master_rows) >= args.minimum_master_records,
        f"Capacity master has too few rows: {len(master_rows)}",
    )

    portal_ids = [row.get("portal_shelter_id", "") for row in master_rows]
    require(all(portal_ids), "Capacity master contains empty portal_shelter_id")
    require(
        len(portal_ids) == len(set(portal_ids)),
        "Capacity master contains duplicate portal_shelter_id",
    )
    master_by_id = {row["portal_shelter_id"]: row for row in master_rows}

    parse_counts = count_values(master_rows, "capacity_parse_status")
    positive_capacity_count = sum(
        bool(row.get("portal_capacity_persons")) for row in master_rows
    )
    require(
        int(metadata.get("record_count", -1)) == len(master_rows),
        "Metadata record_count differs from capacity master",
    )
    require(
        int(metadata.get("parsed_capacity_count", -1))
        == positive_capacity_count,
        "Metadata parsed_capacity_count differs from capacity master",
    )
    require(
        int(metadata.get("invalid_capacity_count", -1)) == 0,
        "Metadata reports invalid capacity values",
    )

    latest_columns, latest_rows = read_csv(Path(args.latest))
    status_columns, status_rows = read_csv(Path(args.status_by_date))

    latest_report = validate_enriched_rows(
        "latest.csv", latest_rows, latest_columns, master_by_id
    )
    status_report = validate_enriched_rows(
        "status_by_date.csv", status_rows, status_columns, master_by_id
    )

    date_columns = [
        column
        for column in status_columns
        if len(column) == 10
        and column[4:5] == "-"
        and column[7:8] == "-"
        and column.replace("-", "").isdigit()
    ]
    require(date_columns, "status_by_date.csv has no date columns")

    report = {
        "validated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "status": "success",
        "master": {
            "row_count": len(master_rows),
            "unique_portal_shelter_id_count": len(set(portal_ids)),
            "municipality_count": len(
                {
                    row.get("municipality_code", "")
                    for row in master_rows
                    if row.get("municipality_code", "")
                }
            ),
            "positive_capacity_count": positive_capacity_count,
            "capacity_parse_status_counts": parse_counts,
            "metadata_response_sha256": metadata.get("response_sha256", ""),
            "metadata_acquired_at_jst": metadata.get("acquired_at_jst", ""),
            "metadata_record_count": metadata.get("record_count"),
        },
        "latest": latest_report,
        "status_by_date": {
            **status_report,
            "date_column_count": len(date_columns),
            "first_date": date_columns[0],
            "last_date": date_columns[-1],
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
