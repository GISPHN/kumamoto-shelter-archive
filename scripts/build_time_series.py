#!/usr/bin/env python3
"""日別避難所CSVから横持ち時系列CSVを更新する。

status_by_date.csv は日常的な確認・分析に使いやすい最小限の固定属性だけを
出力する。open_status_by_date.csv と congestion_by_date.csv は、監査や詳細な
解析に利用できるよう従来の詳細属性を保持する。

重要な原則として、既に横持ちCSVへ保存された過去日の値は再計算結果で
上書きしない。日別スナップショットの照合方法や施設IDが後日変化しても、
既存の歴史値を正本として保持し、新しい日付だけを右端へ追加する。
同じ最新日の再実行だけは、当日中の正当な更新として上書きを許可する。
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date
from pathlib import Path
from typing import Iterable

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_COLUMN_PATTERN = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")

# status_by_date.csv に出力する固定属性。
# 添付された利用用CSVと同じ順序を厳密に維持する。
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

# open_status_by_date.csv と congestion_by_date.csv に保持する詳細属性。
DETAIL_IDENTITY_COLUMNS = [
    "shelter_id",
    "web_shelter_id",
    "reference_common_id",
    "reference_common_ids",
    "municipality",
    "shelter_name",
    "address",
    "reference_facility_name",
    "reference_address",
    "reference_same_address_as_emergency_site",
    "reference_other_mayor_matters",
    "reference_accepted_persons",
    "reference_latitude",
    "reference_longitude",
    "reference_all_coordinates_json",
    "reference_notes",
    "reference_match_status",
    "reference_match_method",
    "reference_match_score",
    "reference_source_file",
    "reference_source_sha256",
    "portal_shelter_id",
    "portal_capacity_persons",
    "portal_capacity_raw",
    "capacity_source",
    "capacity_acquired_at_jst",
    "capacity_match_status",
    "capacity_match_method",
    "capacity_match_score",
    "portal_latitude",
    "portal_longitude",
    "manual_latitude",
    "manual_longitude",
    "manual_geocode_status",
    "manual_geocode_method",
    "manual_geocode_source_file",
    "manual_geocode_source_sha256",
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_method",
    "coordinate_status",
]

# Historical date values are immutable, but derived enrichment metadata may be
# repaired when a verified reference row becomes available later. Refresh only
# monotonic improvements so an already matched/complete row is never degraded by
# a temporary source or matching failure.
CAPACITY_REFRESH_COLUMNS = {
    "portal_capacity_persons",
    "portal_capacity_raw",
    "capacity_source",
    "capacity_acquired_at_jst",
    "capacity_match_status",
    "capacity_match_method",
    "capacity_match_score",
    "portal_latitude",
    "portal_longitude",
}
MANUAL_REFRESH_COLUMNS = {
    "manual_latitude",
    "manual_longitude",
    "manual_geocode_status",
    "manual_geocode_method",
    "manual_geocode_source_file",
    "manual_geocode_source_sha256",
}
COORDINATE_REFRESH_COLUMNS = {
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_method",
    "coordinate_status",
}


def normalize(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv_with_columns(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: Iterable[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {column: row.get(column, "") for column in columns} for row in rows
        )


def daily_files(data_root: Path) -> list[tuple[str, Path]]:
    files = [
        (path.stem, path)
        for path in data_root.glob("daily/*/*/*.csv")
        if DATE_PATTERN.fullmatch(path.stem)
    ]
    return sorted(files, key=lambda item: item[0])


def normalize_date_column(column: str) -> str | None:
    match = DATE_COLUMN_PATTERN.fullmatch(normalize(column))
    if not match:
        return None
    try:
        return date(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).isoformat()
    except ValueError:
        return None


def status_label(row: dict[str, str]) -> str:
    state = normalize(row.get("is_open")).casefold()
    congestion = normalize(row.get("congestion_status"))
    if state == "true":
        return (
            f"開設（{congestion if congestion and congestion not in {'---', '-', '未入力'} else '不明'}）"
        )
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
        return (
            congestion
            if congestion and congestion not in {"---", "-", "未入力"}
            else "不明"
        )
    return "未開設" if state == "false" else "状態不明"


def row_priority(row: dict[str, str]) -> tuple[int, int, int, int]:
    state = normalize(row.get("is_open")).casefold()
    reference = normalize(row.get("reference_match_status"))
    capacity = normalize(row.get("capacity_match_status"))
    coordinate = normalize(row.get("coordinate_status"))
    return (
        2 if state == "true" else 1 if state == "false" else 0,
        1 if reference in {"matched", "matched_multiple"} else 0,
        1 if capacity == "matched" else 0,
        1 if coordinate == "complete" else 0,
    )


def extract_existing_dates(columns: list[str]) -> list[tuple[str, str]]:
    """既存CSVの日付列を元の並び順で返す。"""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for column in columns:
        normalized = normalize_date_column(column)
        if normalized is None:
            continue
        if normalized in seen:
            raise RuntimeError(f"横持ちCSVに重複日付列があります: {normalized}")
        seen.add(normalized)
        result.append((normalized, column))
    return result


def merge_existing_timeseries(
    existing_path: Path,
    generated_rows: list[dict[str, str]],
    fixed_columns: list[str],
    generated_dates: list[str],
    default_value: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """既存の歴史値を固定し、新しい日付だけを追加する。

    既存日付はCSVに保存されている値をそのまま採用する。唯一の例外は、
    生成対象の最新日が既にCSVにも存在する場合で、これは同日再実行として
    最新日のみ再生成値へ更新する。

    既存CSVにない新しい日付が既存最終日より古い場合は、自動バックフィルで
    過去構造を変えないよう停止する。
    """
    if not existing_path.exists() or existing_path.stat().st_size == 0:
        return generated_rows, list(generated_dates)

    columns, existing_rows = read_csv_with_columns(existing_path)
    existing_date_pairs = extract_existing_dates(columns)
    existing_dates = [normalized for normalized, _ in existing_date_pairs]
    existing_date_set = set(existing_dates)

    if not generated_dates:
        return existing_rows, existing_dates

    newest_generated_date = generated_dates[-1]
    new_dates = [item for item in generated_dates if item not in existing_date_set]
    if existing_dates:
        latest_existing_date = max(existing_dates)
        historical_insertions = [item for item in new_dates if item < latest_existing_date]
        if historical_insertions:
            raise RuntimeError(
                "既存の最終日より前へ新しい日付列を自動挿入しようとしたため停止しました。\n"
                f"existing_latest={latest_existing_date}\n"
                f"historical_insertions={historical_insertions}"
            )

    merged_dates = existing_dates + new_dates
    refresh_date = (
        newest_generated_date
        if newest_generated_date in existing_date_set
        else None
    )

    generated_by_id: dict[str, dict[str, str]] = {}
    for row in generated_rows:
        shelter_id = normalize(row.get("shelter_id"))
        if not shelter_id:
            continue
        if shelter_id in generated_by_id:
            raise RuntimeError(f"生成時系列にshelter_id重複があります: {shelter_id}")
        generated_by_id[shelter_id] = row

    existing_ids: set[str] = set()
    merged_rows: list[dict[str, str]] = []

    for old_row in existing_rows:
        shelter_id = normalize(old_row.get("shelter_id"))
        if not shelter_id:
            raise RuntimeError(f"既存横持ちCSVにshelter_idのない行があります: {existing_path}")
        if shelter_id in existing_ids:
            raise RuntimeError(f"既存横持ちCSVにshelter_id重複があります: {shelter_id}")
        existing_ids.add(shelter_id)
        generated = generated_by_id.get(shelter_id)

        capacity_improved = bool(generated) and (
            normalize(generated.get("capacity_match_status")) == "matched"
            and normalize(old_row.get("capacity_match_status")) != "matched"
        )
        manual_improved = bool(generated) and (
            normalize(generated.get("manual_geocode_status")) == "matched"
            and normalize(old_row.get("manual_geocode_status")) != "matched"
        )
        coordinate_improved = bool(generated) and (
            normalize(generated.get("coordinate_status")) == "complete"
            and normalize(old_row.get("coordinate_status")) != "complete"
        )

        merged: dict[str, str] = {}
        for column in fixed_columns:
            old_value = normalize(old_row.get(column))
            generated_value = normalize(generated.get(column)) if generated else ""
            should_refresh = (
                capacity_improved and column in CAPACITY_REFRESH_COLUMNS
            ) or (
                manual_improved and column in MANUAL_REFRESH_COLUMNS
            ) or (
                coordinate_improved and column in COORDINATE_REFRESH_COLUMNS
            )
            # Stable identity fields keep their existing values. Derived
            # enrichment fields may advance from unmatched/missing to a verified
            # matched/complete value, without touching historical date cells.
            merged[column] = (
                generated_value
                if should_refresh and generated_value
                else old_value or generated_value
            )

        for normalized_date, original_column in existing_date_pairs:
            old_value = normalize(old_row.get(original_column))
            if refresh_date == normalized_date and generated is not None:
                merged[normalized_date] = normalize(generated.get(normalized_date)) or default_value
            else:
                merged[normalized_date] = old_value

        for snapshot_date in new_dates:
            merged[snapshot_date] = (
                normalize(generated.get(snapshot_date)) if generated else ""
            ) or default_value

        merged_rows.append(merged)

    # 新しく現れた施設は既存の過去日列へ値を遡及注入しない。
    # 既存履歴では未開設扱いとし、新しい日付から生成値を記録する。
    for generated in generated_rows:
        shelter_id = normalize(generated.get("shelter_id"))
        if not shelter_id or shelter_id in existing_ids:
            continue
        merged = {
            column: normalize(generated.get(column))
            for column in fixed_columns
        }
        for normalized_date, _ in existing_date_pairs:
            if refresh_date == normalized_date:
                merged[normalized_date] = normalize(generated.get(normalized_date)) or default_value
            else:
                merged[normalized_date] = default_value
        for snapshot_date in new_dates:
            merged[snapshot_date] = normalize(generated.get(snapshot_date)) or default_value
        merged_rows.append(merged)

    # 最重要の不変条件をメモリ上で再検証する。
    merged_by_id = {
        normalize(row.get("shelter_id")): row
        for row in merged_rows
        if normalize(row.get("shelter_id"))
    }
    compared_cells = 0
    differences: list[str] = []
    for old_row in existing_rows:
        shelter_id = normalize(old_row.get("shelter_id"))
        merged = merged_by_id.get(shelter_id)
        if merged is None:
            differences.append(f"{shelter_id}: マージ後に施設行が存在しません")
            continue
        for normalized_date, original_column in existing_date_pairs:
            if normalized_date == refresh_date:
                continue
            old_value = normalize(old_row.get(original_column))
            merged_value = normalize(merged.get(normalized_date))
            compared_cells += 1
            if old_value != merged_value:
                differences.append(
                    f"{shelter_id} {normalized_date}: {old_value!r} -> {merged_value!r}"
                )
                if len(differences) >= 20:
                    break
        if len(differences) >= 20:
            break

    if differences:
        raise RuntimeError(
            "既存の過去日ステータスがマージ処理で変更されるため停止しました。\n"
            + "\n".join(differences)
        )

    print(
        f"既存履歴を保持して時系列を更新します: file={existing_path.name}, "
        f"preserved_cells={compared_cells}, refresh_date={refresh_date or '-'}, "
        f"new_dates={new_dates}"
    )
    return merged_rows, merged_dates


def validate_status_schema(path: Path, dates: list[str]) -> None:
    columns, rows = read_csv_with_columns(path)
    expected = STATUS_IDENTITY_COLUMNS + dates
    if columns != expected:
        raise RuntimeError(
            "status_by_date.csvの列構成が指定と一致しません。\n"
            f"expected={expected}\nactual={columns}"
        )
    if len(columns) != len(set(columns)):
        raise RuntimeError("status_by_date.csvに重複列があります。")
    seen_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        shelter_id = normalize(row.get("shelter_id"))
        if not shelter_id:
            raise RuntimeError(
                f"status_by_date.csvの{row_number}行目にshelter_idがありません。"
            )
        if shelter_id in seen_ids:
            raise RuntimeError(
                f"status_by_date.csvにshelter_id重複があります: {shelter_id}"
            )
        seen_ids.add(shelter_id)
    print(
        f"status_by_date.csvの列構成を検証しました: "
        f"固定属性={len(STATUS_IDENTITY_COLUMNS)}列、日付={len(dates)}列、施設={len(rows)}件"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()
    data_root = Path(args.data_root)
    files = daily_files(data_root)
    if not files:
        raise SystemExit(f"日別CSVが見つかりません: {data_root / 'daily'}")

    dates = [snapshot_date for snapshot_date, _ in files]
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
            facility = facilities.setdefault(
                shelter_id,
                {
                    "metadata": {
                        column: "" for column in DETAIL_IDENTITY_COLUMNS
                    },
                    "status": {},
                    "open": {},
                    "congestion": {},
                },
            )
            metadata = facility["metadata"]
            if not isinstance(metadata, dict):
                raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")
            for column in DETAIL_IDENTITY_COLUMNS:
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
                    raise RuntimeError(
                        f"時系列データ内部形式が不正です: {shelter_id}"
                    )
                target[snapshot_date] = value

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
        if not isinstance(metadata, dict):
            raise RuntimeError(f"時系列データ内部形式が不正です: {shelter_id}")

        status_base = {
            column: normalize(metadata.get(column))
            for column in STATUS_IDENTITY_COLUMNS
        }
        detail_base = {
            column: normalize(metadata.get(column))
            for column in DETAIL_IDENTITY_COLUMNS
        }
        outputs = [
            (dict(status_base), "status", "未開設"),
            (dict(detail_base), "open", "0"),
            (dict(detail_base), "congestion", "未開設"),
        ]
        for output, key, default in outputs:
            values = facility[key]
            if not isinstance(values, dict):
                raise RuntimeError(
                    f"時系列データ内部形式が不正です: {shelter_id}"
                )
            for snapshot_date in dates:
                output[snapshot_date] = normalize(values.get(snapshot_date)) or default
        status_rows.append(outputs[0][0])
        open_rows.append(outputs[1][0])
        congestion_rows.append(outputs[2][0])

    status_path = data_root / "status_by_date.csv"
    open_path = data_root / "open_status_by_date.csv"
    congestion_path = data_root / "congestion_by_date.csv"

    status_rows, status_dates = merge_existing_timeseries(
        status_path,
        status_rows,
        STATUS_IDENTITY_COLUMNS,
        dates,
        "未開設",
    )
    open_rows, open_dates = merge_existing_timeseries(
        open_path,
        open_rows,
        DETAIL_IDENTITY_COLUMNS,
        dates,
        "0",
    )
    congestion_rows, congestion_dates = merge_existing_timeseries(
        congestion_path,
        congestion_rows,
        DETAIL_IDENTITY_COLUMNS,
        dates,
        "未開設",
    )

    write_csv(
        status_path,
        status_rows,
        STATUS_IDENTITY_COLUMNS + status_dates,
    )
    write_csv(
        open_path,
        open_rows,
        DETAIL_IDENTITY_COLUMNS + open_dates,
    )
    write_csv(
        congestion_path,
        congestion_rows,
        DETAIL_IDENTITY_COLUMNS + congestion_dates,
    )
    validate_status_schema(status_path, status_dates)

    print(
        f"横持ち時系列CSVを更新しました: 生成施設数={len(facilities)}, "
        f"status施設数={len(status_rows)}, 日数={len(status_dates)}, "
        f"開始日={status_dates[0]}, 終了日={status_dates[-1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
