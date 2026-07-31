#!/usr/bin/env python3
"""Build a persistent shelter-capacity master from the official portal JSON.

The Kumamoto disaster portal loads all shelter marker attributes from
/data/shelter/shelter.json.  The response currently contains one ``items``
record per shelter, including ``facilityId``, ``name``, ``address``,
``capacity``, coordinates and municipality attributes.  Capacity is therefore
collected once from this endpoint; no map-marker clicking is required.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.capacity_matcher import (
        CAPACITY_INPUT_COLUMNS,
        capacity_match_key,
        clean_text,
    )
except ModuleNotFoundError:
    from capacity_matcher import CAPACITY_INPUT_COLUMNS, capacity_match_key, clean_text

JST = ZoneInfo("Asia/Tokyo")
BASE_ENDPOINT = "https://portal.bousai.pref.kumamoto.jp/data/shelter/shelter.json"
REQUIRED_ITEM_KEYS = {
    "facilityId",
    "name",
    "capacity",
    "address",
    "latitude",
    "longitude",
    "municipalityCd",
    "municipalityName",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CAPACITY_INPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {column: row.get(column, "") for column in CAPACITY_INPUT_COLUMNS}
            for row in rows
        )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def source_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_payload(timeout_seconds: int, retries: int) -> tuple[bytes, str, dict[str, str]]:
    """Download the statewide shelter JSON with cache busting and retries."""
    last_error = ""
    for attempt in range(1, retries + 1):
        url = f"{BASE_ENDPOINT}?request.preventCache={int(time.time() * 1000)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://portal.bousai.pref.kumamoto.jp/sp.html?p=evacuation%2Fshelter",
                "User-Agent": "GISPHN-kumamoto-shelter-archive/1.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read()
                status = getattr(response, "status", 200)
                headers = {key.lower(): value for key, value in response.headers.items()}
            if status != 200:
                raise RuntimeError(f"HTTP status {status}")
            if not body:
                raise RuntimeError("empty response body")
            return body, url, headers
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = f"attempt {attempt}/{retries}: {type(exc).__name__}: {exc}"
            print(f"WARNING: shelter JSON download failed: {last_error}")
            if attempt < retries:
                time.sleep(min(15, 2 * attempt))
    raise RuntimeError(f"公式避難所JSONを取得できませんでした。{last_error}")


def parse_capacity(value: object) -> tuple[str, str, str]:
    """Return numeric capacity, raw JSON value and a provenance-safe status.

    The portal popup renders ``capacity == 0`` as ``---``.  Zero is therefore
    treated as an unregistered/missing capacity rather than a real zero-person
    maximum.
    """
    if value is None:
        return "", "", "missing"
    raw = clean_text(value)
    if not raw:
        return "", raw, "missing"
    normalized = unicodedata.normalize("NFKC", raw).replace(",", "")
    if not re.fullmatch(r"-?\d+", normalized):
        return "", raw, "invalid"
    number = int(normalized)
    if number == 0:
        return "", raw, "missing_zero"
    if number < 0:
        return "", raw, "invalid_negative"
    return str(number), raw, "parsed"


def portal_row(item: dict[str, Any], acquired_at: str, source_url: str) -> dict[str, str]:
    facility_id = clean_text(item.get("facilityId", ""))
    municipality_code = clean_text(item.get("municipalityCd", ""))
    municipality = clean_text(item.get("municipalityName", ""))
    name = clean_text(item.get("name", ""))
    address = clean_text(item.get("address", ""))
    latitude = clean_text(item.get("latitude", ""))
    longitude = clean_text(item.get("longitude", ""))
    persons, raw_capacity, parse_status = parse_capacity(item.get("capacity"))

    return {
        "portal_shelter_id": facility_id,
        "municipality_code": municipality_code,
        "municipality": municipality,
        "shelter_name": name,
        "address": address,
        "portal_latitude": latitude,
        "portal_longitude": longitude,
        "portal_capacity_persons": persons,
        "portal_capacity_raw": raw_capacity,
        "capacity_source": "kumamoto_portal_map",
        "capacity_acquired_at_jst": acquired_at,
        "capacity_match_key": capacity_match_key(municipality, name, address),
        "capacity_parse_status": parse_status,
        "source_url": source_url,
    }


def merge_rows(
    existing: list[dict[str, str]],
    downloaded: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge by facilityId while preserving a prior positive capacity if needed."""
    merged: dict[str, dict[str, str]] = {}
    for raw in existing:
        row = {column: clean_text(raw.get(column, "")) for column in CAPACITY_INPUT_COLUMNS}
        key = row.get("portal_shelter_id") or row.get("capacity_match_key")
        if key:
            merged[key] = row

    for raw in downloaded:
        row = {column: clean_text(raw.get(column, "")) for column in CAPACITY_INPUT_COLUMNS}
        key = row.get("portal_shelter_id") or row.get("capacity_match_key")
        if not key:
            continue
        prior = merged.get(key)
        if (
            prior
            and prior.get("capacity_parse_status") == "parsed"
            and row.get("capacity_parse_status") != "parsed"
        ):
            # Retain the last verified capacity while refreshing identity/source
            # attributes from the current official response.
            row["portal_capacity_persons"] = prior.get("portal_capacity_persons", "")
            row["portal_capacity_raw"] = prior.get("portal_capacity_raw", "")
            row["capacity_parse_status"] = "preserved_previous_parsed"
        merged[key] = row

    return sorted(
        merged.values(),
        key=lambda row: (
            row.get("municipality_code", ""),
            row.get("shelter_name", ""),
            row.get("address", ""),
            row.get("portal_shelter_id", ""),
        ),
    )


def validate_items(
    payload: Any,
    minimum_records: int,
    minimum_parsed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError(f"公式JSONのトップレベルがobjectではありません: {type(payload).__name__}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("公式JSONにitems配列がありません。")
    if len(items) < minimum_records:
        raise ValueError(f"公式JSONの施設数が下限未満です: {len(items)} < {minimum_records}")

    invalid_type = [index for index, item in enumerate(items) if not isinstance(item, dict)]
    if invalid_type:
        raise ValueError(f"itemsにobject以外が含まれます: {invalid_type[:10]}")

    missing_key_counts: dict[str, int] = {
        key: sum(key not in item for item in items)
        for key in sorted(REQUIRED_ITEM_KEYS)
    }
    missing_required = {key: count for key, count in missing_key_counts.items() if count}
    if missing_required:
        raise ValueError(f"公式JSONの必須フィールドが欠落しています: {missing_required}")

    facility_ids = [clean_text(item.get("facilityId", "")) for item in items]
    empty_ids = sum(not value for value in facility_ids)
    duplicate_ids = len(facility_ids) - len(set(facility_ids))
    if empty_ids or duplicate_ids:
        raise ValueError(
            f"facilityIdが一意ではありません: empty={empty_ids}, duplicate={duplicate_ids}"
        )

    capacity_statuses = [parse_capacity(item.get("capacity"))[2] for item in items]
    parsed_count = sum(status == "parsed" for status in capacity_statuses)
    if parsed_count < minimum_parsed:
        raise ValueError(
            f"正の定員を持つ施設数が下限未満です: {parsed_count} < {minimum_parsed}"
        )

    reported_total = payload.get("total")
    if reported_total not in (None, ""):
        try:
            if int(reported_total) != len(items):
                raise ValueError(
                    f"公式JSONのtotalとitems件数が不一致です: total={reported_total}, items={len(items)}"
                )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "不一致" in str(exc):
                raise
            raise ValueError(f"公式JSONのtotalを整数として解釈できません: {reported_total}") from exc

    stats = {
        "reported_total": reported_total,
        "record_count": len(items),
        "parsed_capacity_count": parsed_count,
        "missing_zero_capacity_count": sum(status == "missing_zero" for status in capacity_statuses),
        "missing_capacity_count": sum(status == "missing" for status in capacity_statuses),
        "invalid_capacity_count": sum(status.startswith("invalid") for status in capacity_statuses),
        "municipality_count": len(
            {clean_text(item.get("municipalityCd", "")) for item in items if clean_text(item.get("municipalityCd", ""))}
        ),
        "missing_required_key_counts": missing_key_counts,
    }
    return items, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reference/portal_shelter_capacity.csv")
    parser.add_argument(
        "--metadata-output",
        default="reference/portal_shelter_capacity_metadata.json",
    )
    parser.add_argument("--history-dir", default="reference/capacity_history")
    parser.add_argument("--minimum-records", type=int, default=2000)
    parser.add_argument("--minimum-parsed", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()

    acquired_at = datetime.now(JST).isoformat(timespec="seconds")
    body, requested_url, headers = fetch_payload(args.timeout_seconds, args.retries)
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"公式避難所JSONを解析できませんでした: {exc}") from exc

    items, stats = validate_items(payload, args.minimum_records, args.minimum_parsed)
    downloaded = [portal_row(item, acquired_at, BASE_ENDPOINT) for item in items]

    output = Path(args.output)
    existing = read_csv(output)
    merged = merge_rows(existing, downloaded)
    if len(merged) < len(downloaded):
        raise RuntimeError(
            f"定員マスタ統合後の施設数が減少しました: merged={len(merged)}, downloaded={len(downloaded)}"
        )

    write_csv(output, merged)
    history_dir = Path(args.history_dir)
    history_date = datetime.now(JST).date().isoformat()
    history_csv = history_dir / f"{history_date}.csv"
    write_csv(history_csv, merged)

    metadata = {
        "capacity_source": "kumamoto_portal_map",
        "source_endpoint": BASE_ENDPOINT,
        "requested_url": requested_url,
        "acquired_at_jst": acquired_at,
        "response_content_type": headers.get("content-type", ""),
        "response_bytes": len(body),
        "response_sha256": source_sha256(body),
        "existing_master_count": len(existing),
        "downloaded_record_count": len(downloaded),
        "merged_master_count": len(merged),
        **stats,
        "interpretation": {
            "positive_capacity": "portal_capacity_personsとして保存",
            "capacity_zero": "ポップアップでは---表示のため欠損扱い",
        },
    }
    metadata_output = Path(args.metadata_output)
    write_json(metadata_output, metadata)
    write_json(history_dir / f"{history_date}.metadata.json", metadata)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"Persistent capacity master: {output}")
    print(f"Capacity history: {history_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
