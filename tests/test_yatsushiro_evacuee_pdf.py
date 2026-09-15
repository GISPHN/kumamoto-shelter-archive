from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import yatsushiro_evacuee_pdf
from yatsushiro_evacuee_pdf import (
    _validate_rows_without_published_total,
    parse_yatsushiro_pdf,
)


class _FakeTable:
    def __init__(self, matrix: list[list[str]]) -> None:
        self._matrix = matrix

    def extract(self) -> list[list[str]]:
        return self._matrix


class _FakeFinder:
    def __init__(self, matrix: list[list[str]]) -> None:
        self.tables = [_FakeTable(matrix)]


class _FakePage:
    def __init__(self, matrix: list[list[str]]) -> None:
        self._matrix = matrix

    def get_text(self, *_args: object, **_kwargs: object) -> str:
        return "避難所開設状況一覧 令和8年9月15日 6時00分現在"

    def find_tables(self, **_kwargs: object) -> _FakeFinder:
        return _FakeFinder(self._matrix)


class _FakeDocument:
    def __init__(self, matrix: list[list[str]]) -> None:
        self._pages = [_FakePage(matrix)]

    def __iter__(self):
        return iter(self._pages)

    def close(self) -> None:
        pass


def test_missing_total_accepts_one_complete_contiguous_table() -> None:
    _validate_rows_without_published_total(
        seen_numbers=set(range(1, 15)),
        record_count=14,
        selected_numeric_count=14,
        candidate_numeric_counts=[14],
    )


def test_parser_keeps_complete_rows_when_pdf_omits_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = [
        ["No. 避難所名 地区 最大収容人数 世帯数 避難者数 水", "", "", "", "", "", ""],
        ["1", "避難所A", "地区A", "100", "4", "7", "〇"],
        ["2", "避難所B", "地区B", "200", "5", "11", "〇"],
    ]
    monkeypatch.setattr(
        yatsushiro_evacuee_pdf.fitz,
        "open",
        lambda **_kwargs: _FakeDocument(matrix),
    )

    snapshot = parse_yatsushiro_pdf(b"%PDF-fake", "document", "page")

    assert [record.evacuee_count for record in snapshot.records] == [7, 11]
    assert snapshot.published_total is None


@pytest.mark.parametrize(
    ("seen_numbers", "record_count", "selected_count", "candidate_counts"),
    [
        ({1, 2, 4}, 3, 3, [3]),
        ({1, 2, 3}, 3, 4, [4]),
        ({1, 2, 3}, 3, 3, [3, 2]),
    ],
)
def test_missing_total_rejects_incomplete_or_fragmented_tables(
    seen_numbers: set[int],
    record_count: int,
    selected_count: int,
    candidate_counts: list[int],
) -> None:
    with pytest.raises(RuntimeError, match="完全性も確認できませんでした"):
        _validate_rows_without_published_total(
            seen_numbers=seen_numbers,
            record_count=record_count,
            selected_numeric_count=selected_count,
            candidate_numeric_counts=candidate_counts,
        )
