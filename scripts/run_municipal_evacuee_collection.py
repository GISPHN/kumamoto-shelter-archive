#!/usr/bin/env python3
"""Run municipal evacuee collection with source-specific parsers and matching."""

from __future__ import annotations

import collect_municipal_evacuees as collector
from municipal_evacuee_matcher import build_match_record
from uki_evacuee_html import parse_uki_html
from yatsushiro_evacuee_pdf import parse_yatsushiro_pdf
from yatsushiro_source_fetcher import build_collect_yatsushiro

original_match_record = collector.match_record
collector.parse_yatsushiro_pdf = parse_yatsushiro_pdf
collector.parse_uki_html = parse_uki_html
collector.collect_yatsushiro = build_collect_yatsushiro(
    collector,
    parse_yatsushiro_pdf,
)
collector.match_record = build_match_record(collector, original_match_record)

if __name__ == "__main__":
    raise SystemExit(collector.main())
