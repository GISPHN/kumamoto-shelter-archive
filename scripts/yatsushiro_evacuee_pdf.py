"""Structured parser for Yatsushiro City's shelter evacuee PDF."""

from __future__ import annotations

import json
import re
from typing import Any

import fitz


def _cell(value: object) -> str:
    from collect_municipal_evacuees import clean_text

    return clean_text(value).replace("\n", " ")


def _numeric_rows(matrix: list[list[Any]]) -> int:
    return sum(
        _cell(row[0] if row else "").isdigit()
        for row in matrix
    )


def parse_yatsushiro_pdf(
    pdf_data: bytes,
    document_url: str,
    page_url: str,
):
    """Return a SourceSnapshot using the PDF's ruled table structure.

    Expected data columns are:
    No., shelter name, address, district, capacity, households, evacuees,
    followed by facility-condition columns.  The parser selects the detected
    table with the largest number of numeric shelter rows and validates the
    extracted evacuee sum against the PDF total row.  PyMuPDF may detect the
    total row outside the ruled table, so the full page text is used as a
    validated fallback for the published total.
    """
    from collect_municipal_evacuees import (
        SourceRecord,
        SourceSnapshot,
        clean_text,
        japanese_datetime_to_iso,
        normalized_snapshot_hash,
        parse_integer,
        sha256_bytes,
    )

    document = fitz.open(stream=pdf_data, filetype="pdf")
    diagnostics: list[dict[str, object]] = []
    try:
        full_text = clean_text(
            "\n".join(page.get_text("text", sort=True) for page in document)
        )
        observed_at = japanese_datetime_to_iso(full_text)

        candidates: list[tuple[int, list[list[Any]], int]] = []
        for page_index, page in enumerate(document):
            finder = page.find_tables(
                vertical_strategy="lines",
                horizontal_strategy="lines",
                snap_tolerance=3,
                join_tolerance=3,
                intersection_tolerance=3,
            )
            for table_index, table in enumerate(finder.tables):
                matrix = table.extract()
                numeric_count = _numeric_rows(matrix)
                diagnostics.append(
                    {
                        "page": page_index,
                        "table": table_index,
                        "row_count": len(matrix),
                        "column_count": max((len(row) for row in matrix), default=0),
                        "numeric_row_count": numeric_count,
                        "sample": [[_cell(value) for value in row] for row in matrix[:6]],
                    }
                )
                candidates.append((numeric_count, matrix, table_index))
    finally:
        document.close()

    if not candidates:
        raise RuntimeError("八代市PDFから罫線表を検出できませんでした。")
    candidates.sort(key=lambda item: (-item[0], -len(item[1])))
    numeric_count, matrix, _ = candidates[0]
    if numeric_count < 1:
        raise RuntimeError(
            "八代市PDFの表に避難所行がありません。"
            + json.dumps(diagnostics, ensure_ascii=False)
        )

    records: list[SourceRecord] = []
    published_total: int | None = None
    seen_numbers: set[int] = set()

    for raw_row in matrix:
        row = [_cell(value) for value in raw_row]
        if len(row) < 7:
            continue
        first = row[0]
        if first.isdigit():
            row_number = int(first)
            if row_number in seen_numbers:
                continue
            name = row[1]
            address = row[2]
            evacuee_text = row[6]
            if not name or not address or not evacuee_text:
                raise RuntimeError(
                    f"八代市PDFの必須セルが空欄です: row={row_number}, values={row}"
                )
            evacuees = parse_integer(evacuee_text)
            seen_numbers.add(row_number)
            records.append(SourceRecord("八代市", name, address, evacuees))
            continue

        if any("合計" in value for value in row) and row[6]:
            published_total = parse_integer(row[6])

    if published_total is None:
        # The visual total row can fall just outside the table bounding box.
        # In page text its first three numbers are capacity, households and
        # evacuees respectively; the third number is therefore the target.
        total_match = re.search(
            r"合計\s+[\d,]+\s+[\d,]+\s+([\d,]+)",
            full_text,
        )
        if total_match:
            published_total = parse_integer(total_match.group(1))

    if not records:
        raise RuntimeError(
            "八代市PDFの表から避難所行を解析できませんでした。"
            + json.dumps(diagnostics, ensure_ascii=False)
        )

    calculated_total = sum(record.evacuee_count for record in records)
    if published_total is None:
        raise RuntimeError(
            "八代市PDFの合計行から避難者数を取得できませんでした。"
            + json.dumps(diagnostics, ensure_ascii=False)
        )
    if calculated_total != published_total:
        raise RuntimeError(
            "八代市PDFの避難者数合計が一致しません: "
            f"parsed={calculated_total}, published={published_total}, "
            f"rows={len(records)}, diagnostics="
            + json.dumps(diagnostics, ensure_ascii=False)
        )

    return SourceSnapshot(
        municipality="八代市",
        observed_at_jst=observed_at,
        source_format="pdf",
        page_url=page_url,
        document_url=document_url,
        raw_sha256=sha256_bytes(pdf_data),
        normalized_sha256=normalized_snapshot_hash(records),
        records=records,
        published_total=published_total,
    )
