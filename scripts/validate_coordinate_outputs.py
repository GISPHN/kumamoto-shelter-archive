#!/usr/bin/env python3
"""Validate that every shelter row has a complete unified coordinate pair."""

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
    return [
        path
        for path in paths
        if path.exists() and not (path in seen or seen.add(path))
    ]


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

    for path in target_paths(data_root):
        columns, rows = read_csv(path)
        if "shelter_id" not in columns:
            continue
        missing_columns = sorted(REQUIRED_COLUMNS - set(columns))
        if missing_columns:
            all_errors.append(
                {
                    "file": path.as_posix(),
                    "shelter_id": "",
                    "error": f"missing_columns:{','.join(missing_columns)}",
                }
            )
            reports.append(
                {
                    "file": path.as_posix(),
                    "row_count": len(rows),
                    "shelter_row_count": sum(bool(row.get("shelter_id")) for row in rows),
                    "complete_coordinate_count": 0,
                    "error_count": 1,
                    "coordinate_source_counts": {},
                }
            )
            continue

        file_errors: list[dict[str, str]] = []
        file_sources: Counter[str] = Counter()
        shelter_rows = 0
        complete = 0
        for row in rows:
            shelter_id = (row.get("shelter_id") or "").strip()
            if not shelter_id:
                continue
            shelter_rows += 1
            all_output_ids.add(shelter_id)
            web_id = (row.get("web_shelter_id") or "").strip()
            if web_id:
                all_output_ids.add(web_id if web_id.startswith("web:") else f"web:{web_id}")

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
                "error_count": len(file_errors),
                "coordinate_source_counts": dict(sorted(file_sources.items())),
                "error_examples": file_errors[:10],
            }
        )

    manual_ids = set(manual.by_id)
    manual_ids_not_present = sorted(manual_ids - all_output_ids)
    report = {
        "validated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "status": "success" if not all_errors else "failure",
        "manual_geocoding": {
            "record_count": len(manual.rows),
            "unique_shelter_id_count": len(manual.by_id),
            "loaded_files": manual.loaded_files,
            "manual_ids_not_present_in_outputs_count": len(manual_ids_not_present),
            "manual_ids_not_present_in_outputs": manual_ids_not_present,
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
            f"緯度経度が未付与または不正な行があります: {len(all_errors)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
