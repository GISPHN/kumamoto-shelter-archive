#!/usr/bin/env python3
"""Run municipal evacuee collection with source-specific structured parsers."""

from __future__ import annotations

import collect_municipal_evacuees as collector
from uki_evacuee_html import parse_uki_html
from yatsushiro_evacuee_pdf import parse_yatsushiro_pdf

collector.parse_yatsushiro_pdf = parse_yatsushiro_pdf
collector.parse_uki_html = parse_uki_html

if __name__ == "__main__":
    raise SystemExit(collector.main())
