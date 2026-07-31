#!/usr/bin/env python3
"""Provide complete, provenance-preserving coordinates for shelter outputs.

Source-specific coordinate columns are never overwritten.  The unified
``latitude`` and ``longitude`` columns are selected in this order:

1. GSI reference coordinates
2. Kumamoto portal coordinates
3. User-supplied manual geocoding

Manual geocoding is matched by stable shelter/web ID first and by exact
normalized municipality, facility name and address only as a fallback.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MANUAL_REQUIRED_COLUMNS = {
    "shelter_id",
    "municipality",
    "shelter_name",
    "address",
    "manual_latitude",
    "manual_longitude",
    "coordinate_source",
    "source_file",
    "source_sha256",
}

COORDINATE_OUTPUT_COLUMNS = [
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


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u3000", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: object) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[・･·]", "", text)
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "", text)
    text = re.sub(r"[，,．。:：;；/／\\()（）\[\]【】]", "", text)
    return text


def normalize_address(value: object) -> str:
    text = clean_text(value).casefold().replace("熊本県", "")
    text = re.sub(r"\s+", "", text)
    text = text.replace("大字", "").replace("字", "")
    text = text.replace("番地", "").replace("番", "").replace("号", "")
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "-", text)
    text = re.sub(r"[，,．。・:：;；/／\\]", "", text)
    return text


def normalize_municipality(value: object) -> str:
    return re.sub(
        r"\s+", "", clean_text(value).casefold().replace("熊本県", "")
    )


def identity_key(
    municipality: object, shelter_name: object, address: object
) -> tuple[str, str, str]:
    return (
        normalize_municipality(municipality),
        normalize_name(shelter_name),
        normalize_address(address),
    )


def parse_coordinate(
    value: object, minimum: float, maximum: float
) -> tuple[str, float] | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not minimum <= number <= maximum:
        return None
    return text, number


def valid_pair(latitude: object, longitude: object) -> tuple[str, str] | None:
    lat = parse_coordinate(latitude, 31.0, 34.0)
    lon = parse_coordinate(longitude, 129.0, 132.5)
    if lat is None or lon is None:
        return None
    return lat[0], lon[0]


@dataclass(frozen=True)
class ManualMatch:
    row: dict[str, str] | None
    method: str
    status: str


class CoordinateEnricher:
    def __init__(self, manual_path: Path):
        self.manual_path = manual_path
        self.rows: list[dict[str, str]] = []
        self.by_id: dict[str, dict[str, str]] = {}
        self.by_identity: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        self.loaded_files: list[str] = []

        if manual_path.is_dir():
            paths = sorted(manual_path.glob("*.csv"))
        elif manual_path.exists():
            paths = [manual_path]
        else:
            paths = []

        for path in paths:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = set(reader.fieldnames or [])
                missing = sorted(MANUAL_REQUIRED_COLUMNS - fields)
                if missing:
                    raise ValueError(
                        f"手動ジオコーディングCSVに必要な列がありません: "
                        f"{path}: {missing}"
                    )
                for raw in reader:
                    row = {key: clean_text(value) for key, value in raw.items()}
                    shelter_id = row.get("shelter_id", "")
                    if not shelter_id:
                        raise ValueError(f"手動座標にshelter_idがありません: {path}")
                    if shelter_id in self.by_id:
                        raise ValueError(
                            f"手動座標のshelter_idが重複しています: {shelter_id}"
                        )
                    if valid_pair(
                        row.get("manual_latitude"), row.get("manual_longitude")
                    ) is None:
                        raise ValueError(
                            f"手動座標が不正です: {shelter_id} "
                            f"lat={row.get('manual_latitude')} "
                            f"lon={row.get('manual_longitude')}"
                        )
                    self.rows.append(row)
                    self.by_id[shelter_id] = row
                    key = identity_key(
                        row.get("municipality"),
                        row.get("shelter_name"),
                        row.get("address"),
                    )
                    self.by_identity.setdefault(key, []).append(row)
            self.loaded_files.append(path.as_posix())

    @property
    def available(self) -> bool:
        return bool(self.rows)

    def match_manual(self, data_row: dict[str, str]) -> ManualMatch:
        shelter_id = clean_text(data_row.get("shelter_id"))
        if shelter_id and shelter_id in self.by_id:
            return ManualMatch(self.by_id[shelter_id], "manual_shelter_id", "matched")

        web_shelter_id = clean_text(data_row.get("web_shelter_id"))
        if web_shelter_id:
            candidates = [
                web_shelter_id,
                web_shelter_id
                if web_shelter_id.startswith("web:")
                else f"web:{web_shelter_id}",
            ]
            for candidate in candidates:
                if candidate in self.by_id:
                    return ManualMatch(
                        self.by_id[candidate], "manual_web_shelter_id", "matched"
                    )

        key = identity_key(
            data_row.get("municipality"),
            data_row.get("shelter_name"),
            data_row.get("address"),
        )
        matches = self.by_identity.get(key, [])
        if len(matches) == 1:
            return ManualMatch(matches[0], "manual_exact_identity", "matched")
        if len(matches) > 1:
            return ManualMatch(None, "manual_duplicate_identity", "ambiguous")
        return ManualMatch(None, "manual_no_match", "unmatched")

    def enrich(self, data_row: dict[str, str]) -> dict[str, str]:
        manual = self.match_manual(data_row)
        manual_row = manual.row or {}
        manual_pair = valid_pair(
            manual_row.get("manual_latitude"),
            manual_row.get("manual_longitude"),
        )

        reference_pair = valid_pair(
            data_row.get("reference_latitude"),
            data_row.get("reference_longitude"),
        )
        portal_pair = valid_pair(
            data_row.get("portal_latitude"),
            data_row.get("portal_longitude"),
        )

        selected: tuple[str, str] | None = None
        source = ""
        method = ""
        if reference_pair is not None:
            selected = reference_pair
            source = "gsi_reference"
            method = "reference_latitude_longitude"
        elif portal_pair is not None:
            selected = portal_pair
            source = "kumamoto_portal"
            method = "portal_latitude_longitude"
        elif manual_pair is not None:
            selected = manual_pair
            source = "manual_geocoding"
            method = manual.method

        return {
            "manual_latitude": manual_row.get("manual_latitude", ""),
            "manual_longitude": manual_row.get("manual_longitude", ""),
            "manual_geocode_status": manual.status,
            "manual_geocode_method": manual.method,
            "manual_geocode_source_file": manual_row.get("source_file", ""),
            "manual_geocode_source_sha256": manual_row.get("source_sha256", ""),
            "latitude": selected[0] if selected else "",
            "longitude": selected[1] if selected else "",
            "coordinate_source": source,
            "coordinate_method": method,
            "coordinate_status": "complete" if selected else "missing",
        }
