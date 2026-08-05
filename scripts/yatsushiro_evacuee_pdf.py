"""Structured parser for Yatsushiro City's shelter evacuee PDF."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import fitz

FACILITY_MARKERS = {"○", "〇", "△", "▲", "×", "✕", "-", "―"}


@dataclass(frozen=True)
class TableSchema:
    name_index: int
    address_index: int | None
    evacuee_index: int
    facility_marker_index: int | None
    detection_method: str


def _cell(value: object) -> str:
    from collect_municipal_evacuees import clean_text

    return clean_text(value).replace("\n", " ")


def _numeric_rows(matrix: list[list[Any]]) -> int:
    return sum(_cell(row[0] if row else "").isdigit() for row in matrix)


def _header_text_by_column(matrix: list[list[Any]]) -> list[str]:
    """Join all non-data header cells by physical column."""
    width = max((len(row) for row in matrix), default=0)
    headers: list[list[str]] = [[] for _ in range(width)]
    for row in matrix:
        cells = [_cell(value) for value in row]
        if cells and cells[0].isdigit():
            break
        for index, value in enumerate(cells):
            if value and value not in headers[index]:
                headers[index].append(value)
    return [" ".join(values) for values in headers]


def _first_data_row(matrix: list[list[Any]]) -> list[str]:
    for row in matrix:
        cells = [_cell(value) for value in row]
        if cells and cells[0].isdigit():
            return cells
    return []


def _find_header_index(headers: list[str], tokens: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        normalized = header.replace(" ", "")
        if any(token in normalized for token in tokens):
            return index
    return None


def _is_facility_marker(value: str) -> bool:
    normalized = value.replace(" ", "")
    return normalized in FACILITY_MARKERS


def detect_schema(matrix: list[list[Any]]) -> TableSchema:
    """Detect both historical and current Yatsushiro table layouts.

    Historical layout:
      No., shelter, address, district, capacity, households, evacuees, ...

    Current layout (2026-08-04 18:00):
      No., shelter, district, capacity, households, evacuees, ...

    The current PDF removed the address column.  The parser therefore detects
    ``避難者数`` from the header where possible and otherwise uses the first
    facility-condition symbol (○/△/×) as a structural boundary.
    """
    headers = _header_text_by_column(matrix)
    sample = _first_data_row(matrix)
    if not sample:
        raise RuntimeError("八代市PDFの表にデータ行がありません。")

    name_index = _find_header_index(headers, ("避難所名", "施設名"))
    if name_index is None:
        name_index = 1

    address_index = _find_header_index(headers, ("住所", "所在地"))
    evacuee_index = _find_header_index(headers, ("避難者数",))

    marker_index = next(
        (index for index, value in enumerate(sample) if _is_facility_marker(value)),
        None,
    )

    method_parts: list[str] = []
    if evacuee_index is not None:
        method_parts.append("header_evacuee")
    elif marker_index is not None and marker_index >= 1:
        evacuee_index = marker_index - 1
        method_parts.append("marker_boundary")
    else:
        # Last-resort support for the two verified layouts.  A data row with an
        # address has at least seven cells before facility markers; without an
        # address it has six.
        evacuee_index = 6 if len(sample) >= 11 else 5
        method_parts.append("verified_layout_fallback")

    if address_index is None:
        # Header extraction can lose merged labels.  The old format is still
        # identifiable because column 2 contains an 八代市 address.
        if len(sample) > 2 and sample[2].startswith("八代市"):
            address_index = 2
            method_parts.append("address_value")
        else:
            method_parts.append("address_absent")
    else:
        method_parts.append("header_address")

    if evacuee_index >= len(sample):
        raise RuntimeError(
            "八代市PDFの避難者数列がデータ行の範囲外です: "
            f"evacuee_index={evacuee_index}, sample={sample}, headers={headers}"
        )
    if _is_facility_marker(sample[evacuee_index]):
        raise RuntimeError(
            "八代市PDFの避難者数列が設備記号列を指しています: "
            f"evacuee_index={evacuee_index}, sample={sample}, headers={headers}"
        )

    return TableSchema(
        name_index=name_index,
        address_index=address_index,
        evacuee_index=evacuee_index,
        facility_marker_index=marker_index,
        detection_method="+".join(method_parts),
    )


def parse_yatsushiro_pdf(
    pdf_data: bytes,
    document_url: str,
    page_url: str,
):
    """Return a SourceSnapshot using the PDF's ruled table structure."""
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

    schema = detect_schema(matrix)
    diagnostics.append(
        {
            "selected_schema": {
                "name_index": schema.name_index,
                "address_index": schema.address_index,
                "evacuee_index": schema.evacuee_index,
                "facility_marker_index": schema.facility_marker_index,
                "detection_method": schema.detection_method,
                "headers": _header_text_by_column(matrix),
                "first_data_row": _first_data_row(matrix),
            }
        }
    )

    records: list[SourceRecord] = []
    published_total: int | None = None
    seen_numbers: set[int] = set()

    for raw_row in matrix:
        row = [_cell(value) for value in raw_row]
        if not row:
            continue
        first = row[0]
        if first.isdigit():
            row_number = int(first)
            if row_number in seen_numbers:
                continue
            if max(schema.name_index, schema.evacuee_index) >= len(row):
                raise RuntimeError(
                    f"八代市PDFの行が検出スキーマより短いです: row={row_number}, values={row}, schema={schema}"
                )
            name = row[schema.name_index]
            address = (
                row[schema.address_index]
                if schema.address_index is not None and schema.address_index < len(row)
                else ""
            )
            evacuee_text = row[schema.evacuee_index]
            if not name or not evacuee_text:
                raise RuntimeError(
                    f"八代市PDFの必須セルが空欄です: row={row_number}, values={row}, schema={schema}"
                )
            evacuees = parse_integer(evacuee_text)
            seen_numbers.add(row_number)
            records.append(SourceRecord("八代市", name, address, evacuees))
            continue

        if any("合計" in value for value in row):
            if schema.evacuee_index < len(row) and row[schema.evacuee_index]:
                candidate = row[schema.evacuee_index]
                if re.fullmatch(r"[\d,]+", candidate):
                    published_total = parse_integer(candidate)

    if published_total is None:
        # The visual total row can fall just outside the table bounding box.
        # Its first three numeric values are capacity, households and evacuees.
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
            f"rows={len(records)}, schema={schema}, diagnostics="
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
