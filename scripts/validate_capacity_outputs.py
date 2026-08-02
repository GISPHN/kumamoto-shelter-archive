#!/usr/bin/env python3
"""Validate the persistent capacity master and capacity-enriched outputs.

``status_by_date.csv`` is intentionally a compact user-facing export.  It keeps
only 11 fixed attributes plus date columns, while ``latest.csv`` and
``open_status_by_date.csv`` retain the detailed capacity provenance fields.
The validator therefore validates the detailed files against the capacity
master, then verifies that every compact status row exactly reproduces its 11
fixed attributes from the detailed time-series file.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DATE_COLUMN_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_VALUE_PATTERN = re.compile(r"^(?:未開設|状態不明|開設（.*）)$")

# Must remain identical to scripts/build_time_series.py.
STATUS_IDENTITY_COLUMNS = [
    "shelter_id",
    "reference_common_ids",
    "municipality",
    "shelter_name",
    "address",
    "reference_same_address_as_emergency_site",
    "reference_other_mayor_matters",
    "reference_accepted_persons",
    "portal_capacity_persons",
    "latitude",
    "longitude",
]


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


def parse_coordinate(value: str, minimum: float, maximum: float) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if minimum <= number <= maximum else None


def validate_detailed_enriched_rows(
    label: str,
    rows: list[dict[str, str]],
    columns: list[str],
    master_by_id: dict[str, dict[str, str]],
) -> dict[str, object]:
    """Validate a detailed output that retains capacity provenance columns."""
    required_columns = {
        "shelter_id",
        "portal_shelter_id",
        "portal_capacity_persons",
        "portal_capacity_raw",
        "capacity_source",
        "capacity_acquired_at_jst",
        "capacity_match_status",
        "capacity_match_method",
        "portal_latitude",
        "portal_longitude",
        "latitude",
        "longitude",
    }
    missing_columns = sorted(required_columns - set(columns))
    require(not missing_columns, f"{label} missing capacity columns: {missing_columns}")

    shelter_ids = [row.get("shelter_id", "").strip() for row in rows]
    require(all(shelter_ids), f"{label} contains empty shelter_id")
    require(
        len(shelter_ids) == len(set(shelter_ids)),
        f"{label} contains duplicate shelter_id values",
    )

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
    incomplete_coordinates: list[str] = []
    for row in rows:
        latitude = parse_coordinate(row.get("latitude", ""), -90.0, 90.0)
        longitude = parse_coordinate(row.get("longitude", ""), -180.0, 180.0)
        if latitude is None or longitude is None:
            incomplete_coordinates.append(row.get("shelter_id", ""))

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
    require(
        not incomplete_coordinates,
        f"{label} contains incomplete or invalid coordinates: {incomplete_coordinates[:10]}",
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
        "complete_unified_coordinate_count": len(rows) - len(incomplete_coordinates),
        "unknown_master_id_count": len(unknown_master_ids),
        "capacity_mismatch_count": len(mismatched_capacity),
    }


def validate_compact_status_by_date(
    columns: list[str],
    rows: list[dict[str, str]],
    detailed_rows: list[dict[str, str]],
) -> dict[str, object]:
    """Validate the compact 11-column status export against detailed rows."""
    require(
        columns[: len(STATUS_IDENTITY_COLUMNS)] == STATUS_IDENTITY_COLUMNS,
        "status_by_date.csv fixed columns differ from the specified 11-column schema: "
        f"actual={columns[:len(STATUS_IDENTITY_COLUMNS)]}",
    )
    date_columns = columns[len(STATUS_IDENTITY_COLUMNS) :]
    require(date_columns, "status_by_date.csv has no date columns")
    require(
        all(DATE_COLUMN_PATTERN.fullmatch(column) for column in date_columns),
        f"status_by_date.csv contains non-date columns after the fixed schema: {date_columns}",
    )
    require(
        date_columns == sorted(date_columns),
        f"status_by_date.csv date columns are not ascending: {date_columns}",
    )
    require(
        len(columns) == len(set(columns)),
        "status_by_date.csv contains duplicate columns",
    )
    for column in date_columns:
        try:
            date.fromisoformat(column)
        except ValueError as exc:
            raise RuntimeError(f"status_by_date.csv contains invalid date: {column}") from exc

    detailed_by_id = {
        row.get("shelter_id", "").strip(): row
        for row in detailed_rows
        if row.get("shelter_id", "").strip()
    }
    status_by_id = {
        row.get("shelter_id", "").strip(): row
        for row in rows
        if row.get("shelter_id", "").strip()
    }
    require(
        len(status_by_id) == len(rows),
        "status_by_date.csv contains empty or duplicate shelter_id values",
    )
    require(
        set(status_by_id) == set(detailed_by_id),
        "status_by_date.csv shelter IDs differ from open_status_by_date.csv: "
        f"status_only={sorted(set(status_by_id) - set(detailed_by_id))[:10]}, "
        f"detail_only={sorted(set(detailed_by_id) - set(status_by_id))[:10]}",
    )

    fixed_value_mismatches: list[dict[str, str]] = []
    invalid_status_values: list[dict[str, str]] = []
    incomplete_coordinates: list[str] = []
    for shelter_id, row in status_by_id.items():
        detail = detailed_by_id[shelter_id]
        for column in STATUS_IDENTITY_COLUMNS:
            compact_value = row.get(column, "").strip()
            detailed_value = detail.get(column, "").strip()
            if compact_value != detailed_value:
                fixed_value_mismatches.append(
                    {
                        "shelter_id": shelter_id,
                        "column": column,
                        "status_value": compact_value,
                        "detail_value": detailed_value,
                    }
                )
                if len(fixed_value_mismatches) >= 20:
                    break
        latitude = parse_coordinate(row.get("latitude", ""), -90.0, 90.0)
        longitude = parse_coordinate(row.get("longitude", ""), -180.0, 180.0)
        if latitude is None or longitude is None:
            incomplete_coordinates.append(shelter_id)
        for column in date_columns:
            value = row.get(column, "").strip()
            if not value or not STATUS_VALUE_PATTERN.fullmatch(value):
                invalid_status_values.append(
                    {"shelter_id": shelter_id, "date": column, "value": value}
                )
                if len(invalid_status_values) >= 20:
                    break

    require(
        not fixed_value_mismatches,
        "status_by_date.csv fixed attributes differ from open_status_by_date.csv: "
        f"{fixed_value_mismatches[:10]}",
    )
    require(
        not incomplete_coordinates,
        "status_by_date.csv contains incomplete or invalid coordinates: "
        f"{incomplete_coordinates[:10]}",
    )
    require(
        not invalid_status_values,
        "status_by_date.csv contains invalid status values: "
        f"{invalid_status_values[:10]}",
    )

    return {
        "row_count": len(rows),
        "fixed_column_count": len(STATUS_IDENTITY_COLUMNS),
        "date_column_count": len(date_columns),
        "first_date": date_columns[0],
        "last_date": date_columns[-1],
        "complete_unified_coordinate_count": len(rows) - len(incomplete_coordinates),
        "fixed_attribute_mismatch_count": len(fixed_value_mismatches),
        "invalid_status_value_count": len(invalid_status_values),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="reference/portal_shelter_capacity.csv")
    parser.add_argument(
        "--metadata", default="reference/portal_shelter_capacity_metadata.json"
    )
    parser.add_argument("--latest", default="data/latest.csv")
    parser.add_argument("--status-by-date", default="data/status_by_date.csv")
    parser.add_argument(
        "--open-status-by-date", default="data/open_status_by_date.csv"
    )
    parser.add_argument("--output", default="data/logs/capacity_validation.json")
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
        int(metadata.get("parsed_capacity_count", -1)) == positive_capacity_count,
        "Metadata parsed_capacity_count differs from capacity master",
    )
    require(
        int(metadata.get("invalid_capacity_count", -1)) == 0,
        "Metadata reports invalid capacity values",
    )

    latest_columns, latest_rows = read_csv(Path(args.latest))
    detail_columns, detail_rows = read_csv(Path(args.open_status_by_date))
    status_columns, status_rows = read_csv(Path(args.status_by_date))

    latest_report = validate_detailed_enriched_rows(
        "latest.csv", latest_rows, latest_columns, master_by_id
    )
    detailed_time_series_report = validate_detailed_enriched_rows(
        "open_status_by_date.csv", detail_rows, detail_columns, master_by_id
    )
    status_report = validate_compact_status_by_date(
        status_columns, status_rows, detail_rows
    )

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
        "open_status_by_date": detailed_time_series_report,
        "status_by_date": status_report,
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
