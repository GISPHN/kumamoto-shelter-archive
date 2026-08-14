"""Supplemental matching for municipal shelter evacuee sources."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any


# PyMuPDF can extract the character 麦 from some embedded Japanese PDF fonts as
# the CJK radical variant ⻨. They are semantically the same in the affected
# Yatsushiro shelter names, but Unicode NFKC does not collapse this pair.
_PDF_GLYPH_EQUIVALENTS = {
    "⻨": "麦",
}

# Municipal HTML may append temporary operational notes directly to a shelter
# name, e.g. "不知火体育館 ※8/13木11時から開設".  These notes are valuable audit
# information and therefore remain untouched in SourceRecord/output CSVs, but
# they must not participate in facility identity matching.
_OPERATIONAL_NOTE_RE = re.compile(r"\s*[※＊*]\s*.*$")


def build_match_record(collector: Any, original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap the standard matcher with conservative normalization rules.

    In addition to the standard matcher this wrapper:
      * removes trailing temporary operational notes only for identity matching;
      * normalizes known PDF glyph substitutions;
      * resolves address-less Yatsushiro rows conservatively.

    Source text itself is never rewritten in the audit observations.
    """

    def identity_record(record: Any) -> Any:
        source_name = collector.clean_text(record.shelter_name)
        match_name = _OPERATIONAL_NOTE_RE.sub("", source_name).strip()
        if match_name == source_name:
            return record
        return collector.SourceRecord(
            record.municipality,
            match_name,
            record.address,
            record.evacuee_count,
        )

    def canonical_name_variants(value: object) -> set[str]:
        text = collector.clean_text(value)
        text = _OPERATIONAL_NOTE_RE.sub("", text).strip()
        for extracted, canonical in _PDF_GLYPH_EQUIVALENTS.items():
            text = text.replace(extracted, canonical)
        return collector.name_variants(text)

    def has_positive_capacity(row: dict[str, str]) -> bool:
        text = collector.clean_text(row.get("portal_capacity_persons", "")).replace(",", "")
        try:
            return float(text) > 0
        except (TypeError, ValueError):
            return False

    def has_specific_address(row: dict[str, str]) -> bool:
        normalized = collector.normalize_address(row.get("address", ""))
        # Municipality-only placeholders normalize to an empty or very short
        # value. Verified shelter addresses contain substantially more detail.
        return len(normalized) >= 5

    def match_record(
        record: Any,
        status_rows: list[dict[str, str]],
        aliases: dict[tuple[str, str, str], str],
        observation_date: str,
    ) -> Any:
        matched_record = identity_record(record)
        standard = original(matched_record, status_rows, aliases, observation_date)
        if standard.status == "matched":
            return standard

        source_address = collector.normalize_address(matched_record.address)
        if source_address:
            # For address-bearing sources the standard matcher is deliberately
            # authoritative. Do not add looser name-only fallback matching.
            return standard

        pool = [
            row
            for row in status_rows
            if collector.clean_text(row.get("municipality")) == matched_record.municipality
        ]
        source_names = canonical_name_variants(matched_record.shelter_name)
        exact_name = [
            row
            for row in pool
            if source_names & canonical_name_variants(row.get("shelter_name", ""))
        ]
        ranked = sorted(
            (
                (collector.similarity(matched_record, row, observation_date), row)
                for row in exact_name
            ),
            key=lambda item: (
                -item[0],
                collector.clean_text(item[1].get("shelter_id")),
            ),
        )

        if len(ranked) == 1:
            row = ranked[0][1]
            return collector.MatchResult(
                "matched",
                "exact_normalized_name_without_source_address",
                0.97,
                collector.clean_text(row.get("shelter_id")),
                [(0.97, row)],
            )

        if len(ranked) > 1:
            high_quality_web = [
                item
                for item in ranked
                if collector.clean_text(item[1].get("shelter_id", "")).startswith("web:")
                and has_positive_capacity(item[1])
                and has_specific_address(item[1])
            ]
            if len(high_quality_web) == 1:
                row = high_quality_web[0][1]
                return collector.MatchResult(
                    "matched",
                    "exact_name_web_master_quality_without_source_address",
                    0.95,
                    collector.clean_text(row.get("shelter_id")),
                    ranked[:2],
                )

            open_ranked = [
                item
                for item in ranked
                if collector.clean_text(item[1].get(observation_date)).startswith("開設")
            ]
            if len(open_ranked) == 1:
                row = open_ranked[0][1]
                return collector.MatchResult(
                    "matched",
                    "exact_normalized_name_open_priority_without_source_address",
                    0.96,
                    collector.clean_text(row.get("shelter_id")),
                    open_ranked[:2],
                )

            open_web = [
                item
                for item in open_ranked
                if collector.clean_text(item[1].get("shelter_id")).startswith("web:")
            ]
            if len(open_web) == 1:
                row = open_web[0][1]
                return collector.MatchResult(
                    "matched",
                    "exact_normalized_name_open_web_priority_without_source_address",
                    0.95,
                    collector.clean_text(row.get("shelter_id")),
                    open_ranked[:2],
                )

            return collector.MatchResult(
                "ambiguous",
                "exact_name_multiple_without_source_address",
                ranked[0][0],
                "",
                ranked[:2],
            )

        return standard

    return match_record
