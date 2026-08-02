#!/usr/bin/env python3
"""Run the municipal evacuee collector with the structured PDF parser."""

from __future__ import annotations

import collect_municipal_evacuees as collector
from yatsushiro_evacuee_pdf import parse_yatsushiro_pdf

collector.parse_yatsushiro_pdf = parse_yatsushiro_pdf

if __name__ == "__main__":
    raise SystemExit(collector.main())
