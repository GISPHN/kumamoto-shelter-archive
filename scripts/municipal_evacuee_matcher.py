"""Supplemental matching for municipal sources that omit shelter addresses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_match_record(collector: Any, original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap the standard matcher with conservative name-only rules.

    Yatsushiro City's PDF previously included addresses, but the
    2026-08-04 18:00 layout removed that column.  The standard score weights
    address similarity, so a unique exact facility-name match scored only
    0.68 and was rejected.  This wrapper is used only when the municipal
    source address is empty.

    Acceptance order for address-less rows:
      1. Existing manual aliases and any match already accepted by the
         standard matcher.
      2. One unique normalized facility-name match in the municipality.
      3. One unique open row among multiple exact-name candidates.
      4. One unique ``web:`` row among the open exact-name candidates.

    Any unresolved duplicate remains ambiguous; fuzzy name-only matching is
    intentionally not introduced.
    """

    def match_record(
        record: Any,
        status_rows: list[dict[str, str]],
        aliases: dict[tuple[str, str, str], str],
        observation_date: str,
    ) -> Any:
        standard = original(record, status_rows, aliases, observation_date)
        if standard.status == "matched":
            return standard

        source_address = collector.normalize_address(record.address)
        if source_address:
            return standard

        pool = [
            row
            for row in status_rows
            if collector.clean_text(row.get("municipality")) == record.municipality
        ]
        source_names = collector.name_variants(record.shelter_name)
        exact_name = [
            row
            for row in pool
            if source_names & collector.name_variants(row.get("shelter_name", ""))
        ]
        ranked = sorted(
            (
                (collector.similarity(record, row, observation_date), row)
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
