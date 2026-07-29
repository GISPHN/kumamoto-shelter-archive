#!/usr/bin/env python3
"""Print concise diagnostics for a rendered shelter page HTML file."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: diagnose_html.py HTML_FILE")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8", errors="replace")
    print("=== Rendered HTML diagnostics ===")
    print(f"html_bytes={path.stat().st_size}")

    parser = TableParser()
    parser.feed(text)
    print(f"table_count={len(parser.tables)}")
    for index, table in enumerate(parser.tables):
        max_columns = max((len(row) for row in table), default=0)
        print(f"table[{index}] rows={len(table)} max_columns={max_columns}")
        for row_index, row in enumerate(table[:5]):
            print(f"  row[{row_index}]={row!r}")

    normalized = re.sub(r"\s+", " ", text)
    keywords = [
        "全ての避難所",
        "すべての避難所",
        "開設中の避難所",
        "避難所名",
        "開設状況",
        "混雑状況",
        "条件に一致するデータが存在しません",
        "データがありません",
        "エラー",
    ]
    for keyword in keywords:
        positions = [match.start() for match in re.finditer(re.escape(keyword), normalized)]
        print(f"keyword[{keyword}] count={len(positions)}")
        for position in positions[:3]:
            start = max(0, position - 180)
            end = min(len(normalized), position + len(keyword) + 240)
            print("  context=" + normalized[start:end])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
