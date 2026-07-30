#!/usr/bin/env python3
"""日別避難所CSVから横持ち時系列CSVを再構築する。"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Iterable

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 日によって変化しない、または最新の非空値を採用する施設属性。
# portal_capacity_* は熊本防災マップから一度取得する固定属性であり、
# 国土地理院の reference_accepted_persons（受入対象者）とは別項目である。
IDENTITY_COLUMNS = [
    "shelter_id", "web_shelter_id", "reference_common_id", "reference_common_ids",
    "municipality", "shelter_name", "address",
    "reference_facility_name", "reference_address",
    "reference_same_address_as_emergency_site", "reference_other_mayor_matters",
    "reference_accepted_persons", "reference_latitude", "reference_longitude",
    "reference_all_coordinates_json", "reference_notes",
    "reference_match_status", "reference_match_method", "reference_match_score",
    "reference_source_file", "reference_source_sha256",
    "portal_shelter_id", "portal_capacity_persons", "portal_capacity_raw",
    "capacity_source", "capacity_acquired_at_jst", "capacity_match_status",
    "capacity_match_method", "capacity_match_score", "portal_latitude", "portal_longitude",
]


def normalize(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def daily_files(data_root: Path) -> list[tuple[str, Path]]:
    files = [(path.stem, path) for path in data_root.glob("daily/*/*/*.csv") if DATE_PATTERN.fullmatch(path.stem)]
    return sorted(files, key=lambda item: item[0])


def status_label(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    congestion = normalize(row.get("congestion_status"))
    if state == "true":
        return f"開設（{congestion if congestion and congestion not in {'---', '-', '未入力'} else '不明'}）"
    if state == "false":
        return "未開設"
    return "状態不明"


def open_value(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    return "1" if state == "true" else "0" if state == "false" else ""


def congestion_value(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    congestion = normalize(row.get("congestion_status"))
    if state == "true":
        return congestion if congestion and congestion not in {"---", "-", "未入力"} else "不明"
    return "未開設" if state == "false" else "状態不明"


def row_priority(row: dict[str, str]) -> tuple[int, int, int]:
    state = normalize(row.get("is_open")).casefold()
    reference = normalize(row.get("reference_match_status"))
    capacity = normalize(row.get("capacity_match_status"))
    return (
        2 if state == "true" else 1 if state == "false" else 0,
        1 if reference in {"matched", "matched_multiple"} else 0,
        1 if capacity == "matched" else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()
    data_root = Path(args.data_root)
    files = daily_files(data_root)
    if not files:
        raise SystemExit(f"日別CSVが見つかりません: {data_root / 'daily'}")

    dates = [date for date, _ in files]
    facilities: dict[str, dict[str, object]] = {}
    for snapshot_date, path in files:
        selected: dict[str, dict[str, str]] = {}
        for row in read_csv(path):
            shelter_id = normalize(row.get("shelter_id"))
            if not shelter_id:
                continue
            current = selected.get(shelter_id)
            if current is None or row_priority(row) > row_priority(current):
                selected[shelter_id] = row
        for shelter_id, row in selected.items():
            facility = facilities.setdefault(shelter_id, {
                "metadata": {column: "" for column in IDENTITY_COLUMNS},
                "status": {}, "open": {}, "congestion": {},
            })
            metadata = facility["metadata"]
            if not isinstance(metadata, dict):
                raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")
            for column in IDENTITY_COLUMNS:
                value = normalize(row.get(column))
                if value:
                    metadata[column] = value
            for key, value in (
                ("status", status_label(row)),
                ("open", open_value(row)),
                ("congestion", congestion_value(row)),
            ):
                target = facility[key]
                if not isinstance(target, dict):
                    raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")
                target[snapshot_date] = value

    def sort_key(item: tuple[str, dict[str, object]]) -> tuple[str, str, str]:
        shelter_id, facility = item
        metadata = facility["metadata"]
        if not isinstance(metadata, dict):
            return "", "", shelter_id
        return normalize(metadata.get("municipality")), normalize(metadata.get("shelter_name")), shelter_id

    status_rows: list[dict[str, str]] = []
    open_rows: list[dict[str, str]] = []
    congestion_rows: list[dict[str, str]] = []
    for shelter_id, facility in sorted(facilities.items(), key=sort_key):
        metadata = facility["metadata"]
        if not isinstance(metadata, dict):
            raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")
        base = {column: normalize(metadata.get(column)) for column in IDENTITY_COLUMNS}
        outputs = [(dict(base), "status", "未開設"), (dict(base), "open", "0"), (dict(base), "congestion", "未開設")]
        for output, key, default in outputs:
            values = facility[key]
            if not isinstance(values, dict):
                raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")
            for snapshot_date in dates:
                output[snapshot_date] = normalize(values.get(snapshot_date)) or default
        status_rows.append(outputs[0][0]); open_rows.append(outputs[1][0]); congestion_rows.append(outputs[2][0])

    columns = IDENTITY_COLUMNS + dates
    write_csv(data_root / "status_by_date.csv", status_rows, columns)
    write_csv(data_root / "open_status_by_date.csv", open_rows, columns)
    write_csv(data_root / "congestion_by_date.csv", congestion_rows, columns)
    print(f"横持ち時系列CSVを生成しました: 施設数={len(facilities)}, 日数={len(dates)}, 開始日={dates[0]}, 終了日={dates[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
