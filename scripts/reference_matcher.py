"""Match web shelter rows to the supplied Kumamoto shelter reference CSV.

The public web table does not expose the reference CSV's 共通ID. Matching is
therefore performed conservatively using normalized facility name, address and
municipality. Ambiguous matches are never silently forced.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

REFERENCE_INPUT_COLUMNS = [
    "NO",
    "共通ID",
    "施設・場所名",
    "住所",
    "指定緊急避難場所との住所同一",
    "その他市町村長が必要と認める事項",
    "受入対象者",
    "緯度",
    "経度",
    "備考",
]

REFERENCE_OUTPUT_COLUMNS = [
    "reference_match_status",
    "reference_match_method",
    "reference_match_score",
    "reference_match_count",
    "reference_no",
    "reference_common_id",
    "reference_common_ids",
    "reference_facility_name",
    "reference_address",
    "reference_same_address_as_emergency_site",
    "reference_other_mayor_matters",
    "reference_accepted_persons",
    "reference_latitude",
    "reference_longitude",
    "reference_all_coordinates_json",
    "reference_notes",
    "reference_rows_json",
    "reference_candidates_json",
    "reference_source_file",
    "reference_source_sha256",
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
    text = re.sub(r"[・･·･]", "", text)
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "", text)
    text = re.sub(r"[，,．。・:：;；/／\\]", "", text)
    return text


def normalize_address(value: object) -> str:
    text = clean_text(value).casefold()
    text = text.replace("熊本県", "")
    text = re.sub(r"\s+", "", text)
    text = text.replace("大字", "").replace("字", "")
    text = text.replace("番地", "").replace("番", "").replace("号", "")
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "-", text)
    text = re.sub(r"[，,．。・:：;；/／\\]", "", text)
    return text


def normalize_municipality(value: object) -> str:
    text = clean_text(value).casefold()
    text = text.replace("熊本県", "")
    return re.sub(r"\s+", "", text)


def unique_join(values: Iterable[str], separator: str = " | ") -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = clean_text(value)
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return separator.join(output)


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class MatchResult:
    status: str
    method: str
    score: float
    matches: tuple[dict[str, str], ...]
    candidates: tuple[dict[str, str], ...] = ()


class ReferenceMatcher:
    def __init__(self, path: Path):
        self.path = path
        self.sha256 = source_sha256(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in REFERENCE_INPUT_COLUMNS if column not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"参照CSVに必要な列がありません: {missing}")
            rows = []
            for raw in reader:
                row = {column: clean_text(raw.get(column, "")) for column in REFERENCE_INPUT_COLUMNS}
                row["_name_key"] = normalize_name(row["施設・場所名"])
                row["_address_key"] = normalize_address(row["住所"])
                rows.append(row)
        if not rows:
            raise ValueError("参照CSVが空です。")
        common_ids = [row["共通ID"] for row in rows]
        if not all(common_ids) or len(common_ids) != len(set(common_ids)):
            raise ValueError("参照CSVの共通IDに空欄または重複があります。")
        self.rows = rows
        self.by_name: dict[str, list[dict[str, str]]] = {}
        self.by_name_address: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in rows:
            self.by_name.setdefault(row["_name_key"], []).append(row)
            self.by_name_address.setdefault((row["_name_key"], row["_address_key"]), []).append(row)

    @staticmethod
    def _same_municipality(row: dict[str, str], municipality: str) -> bool:
        municipality_key = normalize_municipality(municipality)
        if not municipality_key:
            return True
        return municipality_key in normalize_address(row.get("住所", ""))

    @staticmethod
    def _accepted_duplicate_group(rows: list[dict[str, str]]) -> bool:
        """Accept one-to-many only when all rows describe the same name/address."""
        if not rows:
            return False
        names = {row["_name_key"] for row in rows}
        addresses = {row["_address_key"] for row in rows}
        return len(names) == 1 and len(addresses) == 1

    def match(self, shelter_name: str, address: str, municipality: str) -> MatchResult:
        name_key = normalize_name(shelter_name)
        address_key = normalize_address(address)
        if not name_key:
            return MatchResult("unmatched", "missing_name", 0.0, ())

        if address_key:
            exact = self.by_name_address.get((name_key, address_key), [])
            if exact:
                status = "matched_multiple" if len(exact) > 1 else "matched"
                return MatchResult(status, "exact_name_address", 1.0, tuple(exact))

        same_name = self.by_name.get(name_key, [])
        if same_name:
            municipality_rows = [row for row in same_name if self._same_municipality(row, municipality)]
            if len(municipality_rows) == 1:
                return MatchResult("matched", "exact_name_municipality", 0.99, tuple(municipality_rows))
            if self._accepted_duplicate_group(municipality_rows):
                return MatchResult(
                    "matched_multiple", "exact_name_municipality_same_address", 0.99, tuple(municipality_rows)
                )
            if len(same_name) == 1:
                if not normalize_municipality(municipality):
                    return MatchResult("matched", "exact_unique_name", 0.97, tuple(same_name))
                return MatchResult(
                    "unmatched",
                    "exact_name_municipality_conflict",
                    0.0,
                    (),
                    tuple(same_name),
                )
            if self._accepted_duplicate_group(same_name):
                return MatchResult("matched_multiple", "exact_name_same_address", 0.97, tuple(same_name))
            candidates = municipality_rows or same_name
            return MatchResult("ambiguous", "exact_name_multiple_addresses", 0.0, (), tuple(candidates))

        # Conservative fuzzy fallback. Restrict candidates to the same
        # municipality whenever possible and require both a high name score and
        # a clear margin over the second candidate.
        pool = [row for row in self.rows if self._same_municipality(row, municipality)]
        if not pool:
            pool = self.rows
        scored: list[tuple[float, float, float, dict[str, str]]] = []
        for row in pool:
            name_score = SequenceMatcher(None, name_key, row["_name_key"]).ratio()
            if name_score < 0.80:
                continue
            address_score = (
                SequenceMatcher(None, address_key, row["_address_key"]).ratio()
                if address_key and row["_address_key"]
                else 0.0
            )
            total = name_score if not address_key else (0.82 * name_score + 0.18 * address_score)
            scored.append((total, name_score, address_score, row))
        scored.sort(key=lambda item: (-item[0], item[3]["共通ID"]))
        suggestions = tuple(item[3] for item in scored[:3])
        if not scored:
            return MatchResult("unmatched", "no_candidate", 0.0, ())

        top_total, top_name, top_address, top_row = scored[0]
        second_total = scored[1][0] if len(scored) > 1 else 0.0
        margin = top_total - second_total
        accepted = (
            top_name >= 0.94
            and top_total >= 0.92
            and margin >= 0.035
            and (not address_key or top_address >= 0.65 or top_name >= 0.985)
        )
        if accepted:
            duplicate_group = self.by_name_address.get(
                (top_row["_name_key"], top_row["_address_key"]), [top_row]
            )
            status = "matched_multiple" if len(duplicate_group) > 1 else "matched"
            return MatchResult(status, "high_confidence_fuzzy", top_total, tuple(duplicate_group), suggestions)
        return MatchResult("unmatched", "fuzzy_below_threshold", top_total, (), suggestions)

    def enrich(self, web_row: dict[str, str]) -> dict[str, str]:
        result = self.match(
            web_row.get("shelter_name", ""),
            web_row.get("address", ""),
            web_row.get("municipality", ""),
        )
        matches = sorted(result.matches, key=lambda row: row["共通ID"])
        primary = matches[0] if matches else {}

        def aggregate(column: str) -> str:
            return unique_join((row.get(column, "") for row in matches))

        coordinates = [
            {"共通ID": row["共通ID"], "緯度": row["緯度"], "経度": row["経度"]}
            for row in matches
        ]
        accepted_rows = [
            {column: row.get(column, "") for column in REFERENCE_INPUT_COLUMNS}
            for row in matches
        ]
        candidate_rows = [
            {column: row.get(column, "") for column in REFERENCE_INPUT_COLUMNS}
            for row in result.candidates
        ]
        return {
            "reference_match_status": result.status,
            "reference_match_method": result.method,
            "reference_match_score": f"{result.score:.3f}" if result.score else "",
            "reference_match_count": str(len(matches)),
            "reference_no": primary.get("NO", ""),
            "reference_common_id": primary.get("共通ID", ""),
            "reference_common_ids": ";".join(row["共通ID"] for row in matches),
            "reference_facility_name": aggregate("施設・場所名"),
            "reference_address": aggregate("住所"),
            "reference_same_address_as_emergency_site": aggregate("指定緊急避難場所との住所同一"),
            "reference_other_mayor_matters": aggregate("その他市町村長が必要と認める事項"),
            "reference_accepted_persons": aggregate("受入対象者"),
            "reference_latitude": primary.get("緯度", ""),
            "reference_longitude": primary.get("経度", ""),
            "reference_all_coordinates_json": json.dumps(coordinates, ensure_ascii=False, sort_keys=True),
            "reference_notes": aggregate("備考"),
            "reference_rows_json": json.dumps(accepted_rows, ensure_ascii=False, sort_keys=True),
            "reference_candidates_json": json.dumps(candidate_rows, ensure_ascii=False, sort_keys=True),
            "reference_source_file": self.path.as_posix(),
            "reference_source_sha256": self.sha256,
        }


def tracking_id(web_shelter_id: str, enrichment: dict[str, str]) -> str:
    ids = [value for value in enrichment.get("reference_common_ids", "").split(";") if value]
    if len(ids) == 1:
        return f"ref:{ids[0]}"
    if len(ids) > 1:
        digest = hashlib.sha256("|".join(sorted(ids)).encode("utf-8")).hexdigest()[:20]
        return f"refgroup:{digest}"
    return f"web:{web_shelter_id}"
