#!/usr/bin/env python3
"""Run municipal evacuee collection with source-specific parsers and matching."""

from __future__ import annotations

import sys
from pathlib import Path

import collect_municipal_evacuees as collector
from municipal_evacuee_matcher import build_match_record
from municipal_observation_guard import remove_impossible_future_rows
from municipal_only_shelter_support import (
    build_match_record_with_municipal_only,
    build_wide_rows_with_municipal_only,
)
from uki_evacuee_html import parse_uki_html
from uki_source_fetcher import build_collect_uki
from yatsushiro_evacuee_pdf import parse_yatsushiro_pdf
from yatsushiro_source_fetcher import build_collect_yatsushiro


def _argument_path(flag: str, default: str) -> Path:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return Path(default)
    if index + 1 >= len(sys.argv):
        return Path(default)
    return Path(sys.argv[index + 1])


original_match_record = collector.match_record
municipal_only_match_record = build_match_record_with_municipal_only(
    collector,
    original_match_record,
)
collector.parse_yatsushiro_pdf = parse_yatsushiro_pdf
collector.parse_uki_html = parse_uki_html
collector.collect_yatsushiro = build_collect_yatsushiro(
    collector,
    parse_yatsushiro_pdf,
)
collector.collect_uki = build_collect_uki(
    collector,
    parse_uki_html,
)
# The normal matcher strips temporary notes such as "※8月22日から開設"
# before delegating to municipal-only alias handling.
collector.match_record = build_match_record(
    collector,
    municipal_only_match_record,
)
collector.build_wide_rows = build_wide_rows_with_municipal_only(
    collector,
    collector.build_wide_rows,
)

if __name__ == "__main__":
    observations_path = _argument_path(
        "--observations-csv",
        "data/municipal_evacuees/all_observations.csv",
    )
    remove_impossible_future_rows(observations_path)
    raise SystemExit(collector.main())
