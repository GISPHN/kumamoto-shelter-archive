#!/usr/bin/env python3
"""Validate complete coordinates and exact application of the manual master."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts.coordinate_enricher import CoordinateEnricher, valid_pair
except ModuleNotFoundError:
    from coordinate_enricher import CoordinateEnricher, valid_pair

JST = ZoneInfo("Asia/Tokyo")
REQUIRED_COLUMNS = {
    "manual_latitude",
    "manual_longitude",
    "manual_geocode_status",
    "manual_geocode_method",
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_method",
    "coordinate_status",
}
ALLOWED_SOURCES = {"gsi_reference", "kumamoto_portal", "manual_geocoding"}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def target_paths(data_root: Path) -> list[Path]:
    paths: list[Path] = []
    paths.extend(sorted(data_root.glob("daily/*/*/*.csv")))
    paths.extend(sorted(data_root.glob("changes/*/*/*.csv")))
    paths.extend(sorted(data_root.glob("matching_issues/*/*/*.csv")))
    paths.extend(
        [
            data_root / "latest.csv",
            data_root / "latest_open.csv",
            data_root / "latest_changes.csv",
            data_root / "latest_matching_issues.csv",
            data_root / "latest_capacity_matching_issues.csv",
            data_root / "all_snapshots.csv",
            data_root / "status_by_date.csv",
            data_root / "open_status_by_date.csv",
            data_root / "congestion_by_date.csv",
        ]
    )
    seen: set[Path] = set()
    output: list[Path] = []
    for path in paths:
        if path.exists() and path not in seen:
            seen.add(path)
            output.append(path)
    return output


def normalized_web_id(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return value if value.startswith("web:") else f"web:{value}"


def same_coordinate_pair(
    first: tuple[str, str] | None, second: tuple[str, str] | None
) -> bool:
    if first is None or second is None:
        return False
    return (
        abs(float(first[0]) - float(second[0])) <= 1e-10
        and abs(float(first[1]) - float(second[1])) <= 1e-10
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--manual-geocoding", default="reference/manual_geocoding"
    )
    parser.add_argument(
        "--output", default="data/logs/coordinate_validation.json"
    )
    parser.add_argument("--expected-manual-records", type=int, default=138)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    manual = CoordinateEnricher(Path(args.manual_geocoding))
    if len(manual.rows) != args.expected_manual_records:
        raise RuntimeError(
            f"手動ジオコーディング件数が想定と異なります: "
            f"{len(manual.rows)} != {args.expected_manual_records}"
        )
    if len(manual.by_id) != len(manual.rows):
        raise RuntimeError("手動ジオコーディングのshelter_idが一意ではありません。")

    reports: list[dict[str, object]] = []
    all_errors: list[dict[str, str]] = []
    source_counts: Counter[str] = Counter()
    all_output_ids: set[str] = set()
    manual_ids_selected: set[str] = set()
    manual_ids_requiring_selection: set[str] = set()
    manual_selection_occurrences = 0

    for path in target_paths(data_root):
        columns, rows = read_csv(path)
        if "shelter_id" not in columns:
            continue
        missing_columns = sorted(REQUIRED_COLUMNS - set(columns))
        if missing_columns:
            error = {
                "file": path.as_posix(),
                "shelter_id": "",
                "error": f"missing_columns:{','.join(missing_columns)}",
            }
            all_errors.append(error)
            reports.append(
                {
                    "file": path.as_posix(),
                    "row_count": len(rows),
                    "shelter_row_count": sum(bool(row.get("shelter_id")) for row in rows),
                    "complete_coordinate_count": 0,
                    "manual_selection_count": 0,
                    "error_count": 1,
                    "coordinate_source_counts": {},
                    "error_examples": [error],
                }
            )
            continue

        file_errors: list[dict[str, str]] = []
        file_sources: Counter[str] = Counter()
        shelter_rows = 0
        complete = 0
        file_manual_selections = 0

        for row in rows:
            shelter_id = (row.get("shelter_id") or "").strip()
            if not shelter_id:
                continue
            shelter_rows += 1
            all_output_ids.add(shelter_id)
            web_id = normalized_web_id(row.get("web_shelter_id") or "")
            if web_id:
                all_output_ids.add(web_id)

            pair = valid_pair(row.get("latitude"), row.get("longitude"))
            source = (row.get("coordinate_source") or "").strip()
            method = (row.get("coordinate_method") or "").strip()
            status = (row.get("coordinate_status") or "").strip()

            errors: list[str] = []
            if pair is None:
                errors.append("missing_or_invalid_coordinate_pair")
            if status != "complete":
                errors.append(f"coordinate_status={status or '(blank)'}")
            if source not in ALLOWED_SOURCES:
                errors.append(f"coordinate_source={source or '(blank)'}")
            if not method:
                errors.append("coordinate_method=(blank)")

            manual_match = manual.match_manual(row)
            manual_row = manual_match.row or {}
            manual_pair = valid_pair(
                manual_row.get("manual_latitude"),
                manual_row.get("manual_longitude"),
            )
            reference_pair = valid_pair(
                row.get("reference_latitude"), row.get("reference_longitude")
            )

            if manual_pair is not None:
                manual_id = manual_row.get("shelter_id", "")
                output_manual_pair = valid_pair(
                    row.get("manual_latitude"), row.get("manual_longitude")
                )
                if not same_coordinate_pair(output_manual_pair, manual_pair):
                    errors.append("manual_columns_do_not_match_manual_master")
                if row.get("manual_geocode_status") != "matched":
                    errors.append(
                        f"manual_geocode_status={row.get('manual_geocode_status') or '(blank)'}"
                    )

                # Manual coordinates were supplied for records without GSI
                # coordinates. In that situation the unified pair must exactly
                # equal the manual master and must not silently use portal data.
                if reference_pair is None:
                    manual_ids_requiring_selection.add(manual_id)
                    if source != "manual_geocoding":
                        errors.append(
                            f"manual_coordinate_not_selected:source={source or '(blank)'}"
                        )
                    if not same_coordinate_pair(pair, manual_pair):
                        errors.append("unified_pair_does_not_match_manual_master")
                    if source == "manual_geocoding" and same_coordinate_pair(
                        pair, manual_pair
                    ):
                        manual_ids_selected.add(manual_id)
                        manual_selection_occurrences += 1
                        file_manual_selections += 1

            if errors:
                item = {
                    "file": path.as_posix(),
                    "shelter_id": shelter_id,
                    "municipality": row.get("municipality", ""),
                    "shelter_name": row.get("shelter_name", ""),
                    "address": row.get("address", ""),
                    "error": ";".join(errors),
                }
                file_errors.append(item)
                all_errors.append(item)
            else:
                complete += 1
                file_sources[source] += 1
                source_counts[source] += 1

        reports.append(
            {
                "file": path.as_posix(),
                "row_count": len(rows),
                "shelter_row_count": shelter_rows,
                "complete_coordinate_count": complete,
                "manual_selection_count": file_manual_selections,
                "error_count": len(file_errors),
                "coordinate_source_counts": dict(sorted(file_sources.items())),
                "error_examples": file_errors[:10],
            }
        )

    manual_ids = set(manual.by_id)
    manual_ids_not_present = sorted(manual_ids - all_output_ids)
    manual_ids_not_selected = sorted(
        manual_ids_requiring_selection - manual_ids_selected
    )
    if manual_ids_not_present:
        all_errors.append(
            {
                "file": "manual_master",
                "shelter_id": "",
                "error": (
                    "manual_ids_not_present_in_outputs:"
                    + ",".join(manual_ids_not_present[:20])
                ),
            }
        )
    if manual_ids_not_selected:
        all_errors.append(
            {
                "file": "manual_master",
                "shelter_id": "",
                "error": (
                    "manual_ids_not_selected_where_required:"
                    + ",".join(manual_ids_not_selected[:20])
                ),
            }
        )

    report = {
        "validated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "status": "success" if not all_errors else "failure",
        "coordinate_priority": [
            "gsi_reference",
            "manual_geocoding",
            "kumamoto_portal",
        ],
        "manual_geocoding": {
            "record_count": len(manual.rows),
            "unique_shelter_id_count": len(manual.by_id),
            "loaded_files": manual.loaded_files,
            "manual_ids_not_present_in_outputs_count": len(manual_ids_not_present),
            "manual_ids_not_present_in_outputs": manual_ids_not_present,
            "manual_ids_requiring_selection_count": len(
                manual_ids_requiring_selection
            ),
            "manual_unique_ids_selected_count": len(manual_ids_selected),
            "manual_ids_not_selected_where_required_count": len(
                manual_ids_not_selected
            ),
            "manual_ids_not_selected_where_required": manual_ids_not_selected,
            "manual_selection_occurrence_count": manual_selection_occurrences,
        },
        "validated_file_count": len(reports),
        "total_coordinate_source_counts": dict(sorted(source_counts.items())),
        "total_error_count": len(all_errors),
        "files": reports,
        "error_examples": all_errors[:50],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if all_errors:
        raise RuntimeError(
            f"緯度経度または手動座標の適用に問題があります: {len(all_errors)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
