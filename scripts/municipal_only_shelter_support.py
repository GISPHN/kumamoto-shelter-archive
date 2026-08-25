"""Support municipal shelter observations that are not present in portal/GSI status data."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

MUNICIPAL_ONLY_PREFIX = "municipal:"
_OPERATIONAL_NOTE_RE = re.compile(r"\s*[※＊*]\s*.*$")


def _canonical_source_name(collector: Any, value: object) -> str:
    text = collector.clean_text(value)
    return _OPERATIONAL_NOTE_RE.sub("", text).strip()


def build_match_record_with_municipal_only(
    collector: Any,
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Allow explicit manual aliases to municipal-only shelter IDs.

    Only aliases whose target starts with ``municipal:`` are handled here.
    This avoids fuzzy auto-creation and ensures a municipal-only facility must
    first be explicitly verified in the alias table.
    """

    def match_record(
        record: Any,
        status_rows: list[dict[str, str]],
        aliases: dict[tuple[str, str, str], str],
        observation_date: str,
    ) -> Any:
        alias_key = (
            record.municipality,
            collector.normalize_name(record.shelter_name),
            collector.normalize_address(record.address),
        )
        alias_id = aliases.get(alias_key) or aliases.get(
            (alias_key[0], alias_key[1], "")
        )
        if alias_id and alias_id.startswith(MUNICIPAL_ONLY_PREFIX):
            pseudo = {
                "shelter_id": alias_id,
                "municipality": record.municipality,
                "shelter_name": _canonical_source_name(collector, record.shelter_name),
                "address": collector.clean_text(record.address),
            }
            return collector.MatchResult(
                "matched",
                "manual_alias_municipal_only",
                1.0,
                alias_id,
                [(1.0, pseudo)],
            )
        return original(record, status_rows, aliases, observation_date)

    return match_record


def build_wide_rows_with_municipal_only(
    collector: Any,
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Append explicitly matched municipal-only facilities to the wide CSV."""

    def build_wide_rows(
        status_columns: list[str],
        status_rows: list[dict[str, str]],
        observations: list[dict[str, str]],
    ) -> tuple[list[str], list[dict[str, str]]]:
        columns, rows = original(status_columns, status_rows, observations)
        dates = [column for column in columns if collector.DATE_COLUMN_RE.fullmatch(column)]
        status_ids = {
            collector.clean_text(row.get("shelter_id")) for row in status_rows
        }

        latest_rows = collector.latest_revision_rows(observations)
        municipal_rows = [
            row
            for row in latest_rows
            if collector.clean_text(row.get("match_status")) == "matched"
            and collector.clean_text(row.get("shelter_id")).startswith(MUNICIPAL_ONLY_PREFIX)
        ]
        if not municipal_rows:
            return columns, rows

        metadata: dict[str, dict[str, str]] = {}
        values: dict[tuple[str, str], tuple[str, int]] = {}
        for row in municipal_rows:
            shelter_id = collector.clean_text(row.get("shelter_id"))
            observed_date = collector.clean_text(row.get("source_observed_date"))
            observed_at = collector.clean_text(row.get("source_observed_at_jst"))
            revision = int(collector.clean_text(row.get("revision")) or "0")
            priority = f"{observed_at}|{revision:06d}"

            current_meta = metadata.get(shelter_id)
            if current_meta is None or priority > current_meta["_priority"]:
                metadata[shelter_id] = {
                    "_priority": priority,
                    "municipality": collector.clean_text(row.get("municipality")),
                    "shelter_name": _canonical_source_name(
                        collector, row.get("source_shelter_name")
                    ),
                    "address": collector.clean_text(row.get("source_address")),
                }

            if collector.DATE_COLUMN_RE.fullmatch(observed_date):
                key = (shelter_id, observed_date)
                prior = values.get(key)
                count = int(collector.clean_text(row.get("evacuee_count")) or "0")
                if prior is None or priority > prior[0]:
                    values[key] = (priority, count)

        outputs: list[dict[str, str]] = []
        for shelter_id, meta in sorted(
            metadata.items(),
            key=lambda item: (
                item[1].get("municipality", ""),
                item[1].get("shelter_name", ""),
                item[0],
            ),
        ):
            if shelter_id in status_ids:
                continue
            output = {column: "" for column in collector.STATUS_IDENTITY_COLUMNS}
            output["shelter_id"] = shelter_id
            output["municipality"] = meta.get("municipality", "")
            output["shelter_name"] = meta.get("shelter_name", "")
            output["address"] = meta.get("address", "")
            for observed_date in dates:
                item = values.get((shelter_id, observed_date))
                output[observed_date] = "" if item is None else str(item[1])
            outputs.append(output)

        rows.extend(outputs)
        return columns, rows

    return build_wide_rows
