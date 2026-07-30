"""Match shelter rows to the persistent Kumamoto portal capacity master."""

from __future__ import annotations

import csv
import hashlib
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
    return re.sub(r"\s+", "", clean_text(value).casefold().replace("熊本県", ""))


def capacity_match_key(municipality: object, shelter_name: object, address: object) -> str:
    payload = "|".join(
        [normalize_municipality(municipality), normalize_name(shelter_name), normalize_address(address)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class CapacityMatch:
    status: str
    method: str
    score: float
    row: dict[str, str] | None


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
            required = {"municipality", "shelter_name", "address", "portal_capacity_persons"}
            missing = sorted(required - fields)
            if missing:
                raise ValueError(f"定員マスタに必要な列がありません: {missing}")
            for raw in reader:
                row = {column: clean_text(raw.get(column, "")) for column in CAPACITY_INPUT_COLUMNS}
                if not row["shelter_name"]:
                    continue
                row["_municipality_key"] = normalize_municipality(row["municipality"])
                row["_name_key"] = normalize_name(row["shelter_name"])
                row["_address_key"] = normalize_address(row["address"])
                row["_match_key"] = row["capacity_match_key"] or capacity_match_key(
                    row["municipality"], row["shelter_name"], row["address"]
                )
                self.rows.append(row)
                if row["portal_shelter_id"]:
                    self.by_portal_id[row["portal_shelter_id"]] = row
                self.by_key.setdefault(row["_match_key"], []).append(row)
                self.by_name.setdefault(row["_name_key"], []).append(row)

    @property
    def available(self) -> bool:
        return bool(self.rows)

    def match(self, data_row: dict[str, str]) -> CapacityMatch:
        if not self.rows:
            return CapacityMatch("master_unavailable", "capacity_master_not_found", 0.0, None)

        portal_id = clean_text(data_row.get("portal_shelter_id", ""))
        if portal_id and portal_id in self.by_portal_id:
            return CapacityMatch("matched", "portal_shelter_id", 1.0, self.by_portal_id[portal_id])

        municipality = data_row.get("municipality", "")
        name = data_row.get("shelter_name", "")
        address = data_row.get("address", "")
        key = capacity_match_key(municipality, name, address)
        exact = self.by_key.get(key, [])
        if len(exact) == 1:
            return CapacityMatch("matched", "exact_municipality_name_address", 1.0, exact[0])
        if len(exact) > 1:
            return CapacityMatch("ambiguous", "duplicate_exact_key", 0.0, None)

        name_key = normalize_name(name)
        municipality_key = normalize_municipality(municipality)
        address_key = normalize_address(address)
        same_name = self.by_name.get(name_key, [])
        same_municipality = [
            row for row in same_name
            if not municipality_key or row["_municipality_key"] == municipality_key
        ]
        if len(same_municipality) == 1:
            row = same_municipality[0]
            if not address_key or not row["_address_key"]:
                return CapacityMatch("matched", "exact_name_municipality", 0.97, row)
            address_score = SequenceMatcher(None, address_key, row["_address_key"]).ratio()
            if address_score >= 0.72:
                return CapacityMatch("matched", "exact_name_municipality_address_similar", address_score, row)
        if len(same_municipality) > 1:
            return CapacityMatch("ambiguous", "exact_name_multiple_capacity_rows", 0.0, None)

        pool = [
            row for row in self.rows
            if not municipality_key or row["_municipality_key"] == municipality_key
        ] or self.rows
        scored: list[tuple[float, float, float, dict[str, str]]] = []
        for row in pool:
            name_score = SequenceMatcher(None, name_key, row["_name_key"]).ratio()
            if name_score < 0.88:
                continue
            address_score = (
                SequenceMatcher(None, address_key, row["_address_key"]).ratio()
                if address_key and row["_address_key"] else 0.0
            )
            total = name_score if not address_key else 0.82 * name_score + 0.18 * address_score
            scored.append((total, name_score, address_score, row))
        scored.sort(key=lambda item: (-item[0], item[3]["portal_shelter_id"], item[3]["shelter_name"]))
        if not scored:
            return CapacityMatch("unmatched", "no_candidate", 0.0, None)
        top_total, top_name, top_address, top_row = scored[0]
        second_total = scored[1][0] if len(scored) > 1 else 0.0
        if (
            top_name >= 0.96
            and top_total >= 0.93
            and top_total - second_total >= 0.04
            and (not address_key or top_address >= 0.68 or top_name >= 0.99)
        ):
            return CapacityMatch("matched", "high_confidence_fuzzy", top_total, top_row)
        return CapacityMatch("unmatched", "fuzzy_below_threshold", top_total, None)

    def enrich(self, data_row: dict[str, str]) -> dict[str, str]:
        result = self.match(data_row)
        row = result.row or {}
        return {
            "portal_shelter_id": row.get("portal_shelter_id", ""),
            "portal_capacity_persons": row.get("portal_capacity_persons", ""),
            "portal_capacity_raw": row.get("portal_capacity_raw", ""),
            "capacity_source": row.get("capacity_source", ""),
            "capacity_acquired_at_jst": row.get("capacity_acquired_at_jst", ""),
            "capacity_match_status": result.status,
            "capacity_match_method": result.method,
            "capacity_match_score": f"{result.score:.3f}" if result.score else "",
            "portal_latitude": row.get("portal_latitude", ""),
            "portal_longitude": row.get("portal_longitude", ""),
        }
