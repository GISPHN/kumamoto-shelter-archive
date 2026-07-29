#!/usr/bin/env python3
"""日別避難所CSVから横持ち時系列CSVを生成する。

日次収集では data/daily/YYYY/MM/YYYY-MM-DD.csv に、1日1施設1行の
縦持ちスナップショットを保存する。本スクリプトは、その全日分を読み込み、
施設を行、観測日を列とする横持ちCSVを再構築する。
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Iterable

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 日によって変化しない、または最新の非空値を採用する施設属性。
IDENTITY_COLUMNS = [
    "shelter_id",
    "web_shelter_id",
    "reference_common_id",
    "reference_common_ids",
    "municipality",
    "shelter_name",
    "address",
    "reference_facility_name",
    "reference_address",
    "reference_latitude",
    "reference_longitude",
    "reference_accepted_persons",
    "reference_match_status",
]


def normalize(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    rows: Iterable[dict[str, str]],
    columns: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def find_daily_files(data_root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in data_root.glob("daily/*/*/*.csv"):
        snapshot_date = path.stem
        if DATE_PATTERN.fullmatch(snapshot_date):
            files.append((snapshot_date, path))
    files.sort(key=lambda item: item[0])
    return files


def status_label(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    congestion = normalize(row.get("congestion_status"))

    if state == "true":
        if not congestion or congestion in {"---", "-", "未入力"}:
            congestion = "不明"
        return f"開設（{congestion}）"
    if state == "false":
        return "未開設"
    return "状態不明"


def open_value(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    if state == "true":
        return "1"
    if state == "false":
        return "0"
    return ""


def congestion_value(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    congestion = normalize(row.get("congestion_status"))

    if state == "true":
        return congestion if congestion and congestion not in {"---", "-", "未入力"} else "不明"
    if state == "false":
        return "未開設"
    return "状態不明"


def row_priority(row: dict[str, str]) -> tuple[int, int]:
    """同一日・同一IDの重複時は開設行と参照CSV一致行を優先する。"""
    state = normalize(row.get("is_open")).casefold()
    match_status = normalize(row.get("reference_match_status"))
    return (
        2 if state == "true" else 1 if state == "false" else 0,
        1 if match_status in {"matched", "matched_multiple"} else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    files = find_daily_files(data_root)
    if not files:
        raise SystemExit(f"日別CSVが見つかりません: {data_root / 'daily'}")

    dates = [snapshot_date for snapshot_date, _ in files]
    facilities: dict[str, dict[str, object]] = {}

    for snapshot_date, path in files:
        rows_for_date: dict[str, dict[str, str]] = {}
        for row in read_csv(path):
            shelter_id = normalize(row.get("shelter_id"))
            if not shelter_id:
                continue
            current = rows_for_date.get(shelter_id)
            if current is None or row_priority(row) > row_priority(current):
                rows_for_date[shelter_id] = row

        for shelter_id, row in rows_for_date.items():
            facility = facilities.setdefault(
                shelter_id,
                {
                    "metadata": {column: "" for column in IDENTITY_COLUMNS},
                    "status": {},
                    "open": {},
                    "congestion": {},
                },
            )

            metadata = facility["metadata"]
            status = facility["status"]
            open_status = facility["open"]
            congestion = facility["congestion"]
            if not all(isinstance(value, dict) for value in (metadata, status, open_status, congestion)):
                raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")

            for column in IDENTITY_COLUMNS:
                value = normalize(row.get(column))
                if value:
                    metadata[column] = value

            status[snapshot_date] = status_label(row)
            open_status[snapshot_date] = open_value(row)
            congestion[snapshot_date] = congestion_value(row)

    def sort_key(item: tuple[str, dict[str, object]]) -> tuple[str, str, str]:
        shelter_id, facility = item
        metadata = facility["metadata"]
        if not isinstance(metadata, dict):
            return "", "", shelter_id
        return (
            normalize(metadata.get("municipality")),
            normalize(metadata.get("shelter_name")),
            shelter_id,
        )

    status_rows: list[dict[str, str]] = []
    open_rows: list[dict[str, str]] = []
    congestion_rows: list[dict[str, str]] = []

    for shelter_id, facility in sorted(facilities.items(), key=sort_key):
        metadata = facility["metadata"]
        status = facility["status"]
        open_status = facility["open"]
        congestion = facility["congestion"]
        if not all(isinstance(value, dict) for value in (metadata, status, open_status, congestion)):
            raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")

        base = {column: normalize(metadata.get(column)) for column in IDENTITY_COLUMNS}
        status_row = dict(base)
        open_row = dict(base)
        congestion_row = dict(base)

        # 日別CSVは成功した完全スナップショットであるため、その日に行がない
        # Web由来施設は「開設一覧に存在しない」＝未開設として補完する。
        for snapshot_date in dates:
            status_row[snapshot_date] = normalize(status.get(snapshot_date)) or "未開設"
            open_row[snapshot_date] = normalize(open_status.get(snapshot_date)) or "0"
            congestion_row[snapshot_date] = normalize(congestion.get(snapshot_date)) or "未開設"

        status_rows.append(status_row)
        open_rows.append(open_row)
        congestion_rows.append(congestion_row)

    columns = IDENTITY_COLUMNS + dates
    write_csv(data_root / "status_by_date.csv", status_rows, columns)
    write_csv(data_root / "open_status_by_date.csv", open_rows, columns)
    write_csv(data_root / "congestion_by_date.csv", congestion_rows, columns)

    print(
        "横持ち時系列CSVを生成しました: "
        f"施設数={len(facilities)}, 日数={len(dates)}, "
        f"開始日={dates[0]}, 終了日={dates[-1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
