"""Match shelter rows to the persistent Kumamoto portal capacity master.

Stable portal IDs and exact normalized attributes are preferred. When names or
addresses differ between the GSI reference data and the Kumamoto portal, source
coordinates may resolve the record only under strict distance, name-similarity
and uniqueness criteria.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

CAPACITY_INPUT_COLUMNS = [
    "portal_shelter_id",
    "municipality_code",
    "municipality",
    "shelter_name",
    "address",
    "portal_latitude",
    "portal_longitude",
    "portal_capacity_persons",
    "portal_capacity_raw",
    "capacity_source",
    "capacity_acquired_at_jst",
    "capacity_match_key",
    "capacity_parse_status",
    "source_url",
]

CAPACITY_OUTPUT_COLUMNS = [
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


def infer_municipality(value: object) -> str:
    text = clean_text(value).replace("熊本県", "", 1)
    match = re.match(r"(.+?(?:市|町|村))", text)
    return match.group(1) if match else ""


def capacity_match_key(
    municipality: object, shelter_name: object, address: object
) -> str:
    payload = "|".join(
        [
            normalize_municipality(municipality),
            normalize_name(shelter_name),
            normalize_address(address),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def parse_coordinate(value: object, minimum: float, maximum: float) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if minimum <= number <= maximum else None


def haversine_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius = 6_371_008.8
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    term = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a)
        * math.cos(phi_b)
        * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(term)))


@dataclass(frozen=True)
class CapacityMatch:
    status: str
    method: str
    score: float
    row: dict[str, str] | None


@dataclass(frozen=True)
class SpatialCandidate:
    distance_m: float
    name_score: float
    address_score: float
    combined_score: float
    row: dict[str, str]


class CapacityMatcher:
    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict[str, str]] = []
        self.by_portal_id: dict[str, dict[str, str]] = {}
        self.by_key: dict[str, list[dict[str, str]]] = {}
        self.by_name: dict[str, list[dict[str, str]]] = {}

        if not path.exists() or path.stat().st_size == 0:
            return

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            required = {
                "municipality",
                "shelter_name",
                "address",
                "portal_capacity_persons",
                "portal_latitude",
                "portal_longitude",
            }
            missing = sorted(required - fields)
            if missing:
                raise ValueError(f"定員マスタに必要な列がありません: {missing}")
            for raw in reader:
                row = {
                    column: clean_text(raw.get(column, ""))
                    for column in CAPACITY_INPUT_COLUMNS
                }
                if not row["shelter_name"]:
                    continue
                row["_municipality_key"] = normalize_municipality(
                    row["municipality"]
                )
                row["_name_key"] = normalize_name(row["shelter_name"])
                row["_address_key"] = normalize_address(row["address"])
                row["_match_key"] = row["capacity_match_key"] or capacity_match_key(
                    row["municipality"], row["shelter_name"], row["address"]
                )
                latitude = parse_coordinate(row["portal_latitude"], -90, 90)
                longitude = parse_coordinate(row["portal_longitude"], -180, 180)
                row["_latitude"] = "" if latitude is None else str(latitude)
                row["_longitude"] = "" if longitude is None else str(longitude)
                self.rows.append(row)
                if row["portal_shelter_id"]:
                    self.by_portal_id[row["portal_shelter_id"]] = row
                self.by_key.setdefault(row["_match_key"], []).append(row)
                self.by_name.setdefault(row["_name_key"], []).append(row)

    @property
    def available(self) -> bool:
        return bool(self.rows)

    @staticmethod
    def _source_coordinates(
        data_row: dict[str, str],
    ) -> tuple[float, float] | None:
        latitude = parse_coordinate(
            data_row.get("reference_latitude")
            or data_row.get("portal_latitude")
            or data_row.get("latitude"),
            -90,
            90,
        )
        longitude = parse_coordinate(
            data_row.get("reference_longitude")
            or data_row.get("portal_longitude")
            or data_row.get("longitude"),
            -180,
            180,
        )
        if latitude is None or longitude is None:
            return None
        return latitude, longitude

    @staticmethod
    def _spatial_candidates(
        data_row: dict[str, str],
        rows: list[dict[str, str]],
        maximum_distance_m: float = 250.0,
    ) -> list[SpatialCandidate]:
        coordinates = CapacityMatcher._source_coordinates(data_row)
        if coordinates is None:
            return []
        source_latitude, source_longitude = coordinates
        name_key = normalize_name(data_row.get("shelter_name", ""))
        address_key = normalize_address(data_row.get("address", ""))

        candidates: list[SpatialCandidate] = []
        for row in rows:
            latitude = parse_coordinate(row.get("_latitude"), -90, 90)
            longitude = parse_coordinate(row.get("_longitude"), -180, 180)
            if latitude is None or longitude is None:
                continue
            distance = haversine_meters(
                source_latitude, source_longitude, latitude, longitude
            )
            if distance > maximum_distance_m:
                continue
            name_score = SequenceMatcher(
                None, name_key, row.get("_name_key", "")
            ).ratio()
            address_score = (
                SequenceMatcher(
                    None, address_key, row.get("_address_key", "")
                ).ratio()
                if address_key and row.get("_address_key")
                else 0.0
            )
            proximity_score = max(0.0, 1.0 - distance / maximum_distance_m)
            combined = (
                0.58 * name_score
                + 0.17 * address_score
                + 0.25 * proximity_score
            )
            candidates.append(
                SpatialCandidate(
                    distance,
                    name_score,
                    address_score,
                    combined,
                    row,
                )
            )
        candidates.sort(
            key=lambda item: (
                item.distance_m,
                -item.name_score,
                -item.address_score,
                item.row.get("portal_shelter_id", ""),
            )
        )
        return candidates

    @staticmethod
    def _accept_spatial(
        data_row: dict[str, str], rows: list[dict[str, str]]
    ) -> CapacityMatch | None:
        candidates = CapacityMatcher._spatial_candidates(data_row, rows)
        if not candidates:
            return None

        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        near_tie = (
            second is not None
            and second.distance_m - top.distance_m < 12.0
            and second.name_score >= top.name_score - 0.04
        )
        if near_tie:
            return CapacityMatch("ambiguous", "coordinate_near_tie", 0.0, None)

        accepted = False
        method = ""
        if top.distance_m <= 20.0 and top.name_score >= 0.55:
            accepted = True
            method = "coordinate_with_name_20m"
        elif (
            top.distance_m <= 75.0
            and top.name_score >= 0.72
            and (top.address_score >= 0.35 or top.name_score >= 0.90)
        ):
            accepted = True
            method = "coordinate_name_address_75m"
        elif (
            top.distance_m <= 200.0
            and top.name_score >= 0.90
            and top.address_score >= 0.55
        ):
            accepted = True
            method = "coordinate_high_similarity_200m"

        if not accepted:
            return None

        score = min(
            0.999,
            max(
                0.80,
                top.combined_score
                + (0.08 if top.distance_m <= 20.0 else 0.03),
            ),
        )
        return CapacityMatch("matched", method, score, top.row)

    def match(self, data_row: dict[str, str]) -> CapacityMatch:
        if not self.rows:
            return CapacityMatch(
                "master_unavailable", "capacity_master_not_found", 0.0, None
            )

        portal_id = clean_text(data_row.get("portal_shelter_id", ""))
        if portal_id and portal_id in self.by_portal_id:
            return CapacityMatch(
                "matched", "portal_shelter_id", 1.0, self.by_portal_id[portal_id]
            )

        municipality = clean_text(data_row.get("municipality", ""))
        name = data_row.get("shelter_name", "")
        address = data_row.get("address", "")
        if not municipality:
            municipality = infer_municipality(address)
        municipality_key = normalize_municipality(municipality)
        name_key = normalize_name(name)
        address_key = normalize_address(address)

        key = capacity_match_key(municipality, name, address)
        exact = self.by_key.get(key, [])
        if len(exact) == 1:
            return CapacityMatch(
                "matched", "exact_municipality_name_address", 1.0, exact[0]
            )
        if len(exact) > 1:
            spatial = self._accept_spatial(data_row, exact)
            if spatial is not None:
                return spatial
            return CapacityMatch("ambiguous", "duplicate_exact_key", 0.0, None)

        same_name = self.by_name.get(name_key, [])
        same_municipality = [
            row
            for row in same_name
            if not municipality_key
            or row["_municipality_key"] == municipality_key
        ]
        if len(same_municipality) == 1:
            row = same_municipality[0]
            if not address_key or not row["_address_key"]:
                return CapacityMatch(
                    "matched", "exact_name_municipality", 0.97, row
                )
            address_score = SequenceMatcher(
                None, address_key, row["_address_key"]
            ).ratio()
            if address_score >= 0.72:
                return CapacityMatch(
                    "matched",
                    "exact_name_municipality_address_similar",
                    address_score,
                    row,
                )
        if len(same_municipality) > 1:
            spatial = self._accept_spatial(data_row, same_municipality)
            if spatial is not None:
                return spatial
            return CapacityMatch(
                "ambiguous", "exact_name_multiple_capacity_rows", 0.0, None
            )

        municipality_pool = [
            row
            for row in self.rows
            if not municipality_key
            or row["_municipality_key"] == municipality_key
        ]
        pool = municipality_pool or self.rows

        spatial = self._accept_spatial(data_row, pool)
        if spatial is not None:
            return spatial

        scored: list[tuple[float, float, float, dict[str, str]]] = []
        for row in pool:
            name_score = SequenceMatcher(
                None, name_key, row["_name_key"]
            ).ratio()
            if name_score < 0.80:
                continue
            address_score = (
                SequenceMatcher(
                    None, address_key, row["_address_key"]
                ).ratio()
                if address_key and row["_address_key"]
                else 0.0
            )
            total = (
                name_score
                if not address_key
                else 0.82 * name_score + 0.18 * address_score
            )
            scored.append((total, name_score, address_score, row))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[3]["portal_shelter_id"],
                item[3]["shelter_name"],
            )
        )
        if not scored:
            return CapacityMatch("unmatched", "no_candidate", 0.0, None)
        top_total, top_name, top_address, top_row = scored[0]
        second_total = scored[1][0] if len(scored) > 1 else 0.0
        if (
            top_name >= 0.96
            and top_total >= 0.93
            and top_total - second_total >= 0.04
            and (
                not address_key
                or top_address >= 0.68
                or top_name >= 0.99
            )
        ):
            return CapacityMatch(
                "matched", "high_confidence_fuzzy", top_total, top_row
            )
        return CapacityMatch(
            "unmatched", "fuzzy_below_threshold", top_total, None
        )

    def enrich(self, data_row: dict[str, str]) -> dict[str, str]:
        result = self.match(data_row)
        row = result.row or {}
        return {
            "portal_shelter_id": row.get("portal_shelter_id", ""),
            "portal_capacity_persons": row.get(
                "portal_capacity_persons", ""
            ),
            "portal_capacity_raw": row.get("portal_capacity_raw", ""),
            "capacity_source": row.get("capacity_source", ""),
            "capacity_acquired_at_jst": row.get(
                "capacity_acquired_at_jst", ""
            ),
            "capacity_match_status": result.status,
            "capacity_match_method": result.method,
            "capacity_match_score": (
                f"{result.score:.3f}" if result.score else ""
            ),
            "portal_latitude": row.get("portal_latitude", ""),
            "portal_longitude": row.get("portal_longitude", ""),
        }
