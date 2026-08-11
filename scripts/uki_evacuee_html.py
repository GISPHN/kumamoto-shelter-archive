"""Structured parser for Uki City's shelter evacuee HTML table."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup


_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)(?!\d)")


def _numeric_tokens(text: str) -> list[int]:
    """Extract integer tokens from a table cell without trusting surrounding labels."""
    values: list[int] = []
    for token in _NUMBER_RE.findall(text):
        try:
            values.append(int(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _cell_integer(text: str) -> int:
    """Parse one numeric table cell, tolerating responsive label text."""
    values = _numeric_tokens(text)
    if not values:
        raise ValueError(f"numeric value not found: {text!r}")
    # Responsive markup can prepend a header label but the actual cell value is
    # rendered last.  Use the last integer token in the target cell.
    return values[-1]


def parse_uki_html(page_data: bytes, page_url: str):
    from collect_municipal_evacuees import (
        SourceRecord,
        SourceSnapshot,
        clean_text,
        decode_html,
        japanese_datetime_to_iso,
        normalized_snapshot_hash,
        sha256_bytes,
    )

    page_html = decode_html(page_data)
    soup = BeautifulSoup(page_html, "html.parser")
    page_text = clean_text(soup.get_text(" "))

    update_year_match = re.search(r"(20\d{2})年\s*\d+月\s*\d+日更新", page_text)
    default_year = int(update_year_match.group(1)) if update_year_match else None
    observed_text_match = re.search(
        r"\d+月\s*\d+日.*?(?:午前|午後)?\s*\d+時\s*\d+分時点",
        page_text,
    )
    if not observed_text_match:
        raise RuntimeError("宇城市ページから観測時刻を検出できませんでした。")
    observed_at = japanese_datetime_to_iso(observed_text_match.group(0), default_year)

    target_table = None
    header_indexes: dict[str, int] = {}
    header_row_index = -1
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        # Do not assume the first <tr> is the header.  CMS responsive markup may
        # insert auxiliary rows before the visible heading row.
        for row_index, row in enumerate(rows):
            headers = [
                clean_text(cell.get_text(" "))
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if "避難所名" in headers and "避難者数" in headers:
                target_table = table
                header_indexes = {
                    header: index for index, header in enumerate(headers) if header
                }
                header_row_index = row_index
                break
        if target_table is not None:
            break
    if target_table is None:
        raise RuntimeError("宇城市ページから避難者数表を検出できませんでした。")

    required_headers = {"避難所名", "住所", "避難者数"}
    missing_headers = sorted(required_headers - set(header_indexes))
    if missing_headers:
        raise RuntimeError(f"宇城市表に必要列がありません: {missing_headers}")

    records: list[SourceRecord] = []
    total_candidates: list[int] = []
    rows = target_table.find_all("tr")
    for row in rows[header_row_index + 1 :]:
        cells = [
            clean_text(cell.get_text(" "))
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        if not cells:
            continue
        joined = " ".join(cells)
        if re.search(r"合\s*計", joined):
            # Keep every number from the total row.  On responsive CMS markup
            # the visual columns may be represented with colspan or hidden
            # labels, so positional assumptions are unsafe.  The final
            # integrity check selects the candidate equal to the sum of the
            # shelter-level evacuee counts.
            for cell in cells:
                total_candidates.extend(_numeric_tokens(cell))
            continue

        try:
            name = cells[header_indexes["避難所名"]]
            address = cells[header_indexes["住所"]]
            count = _cell_integer(cells[header_indexes["避難者数"]])
        except (IndexError, KeyError, ValueError):
            continue
        if not name:
            continue
        records.append(SourceRecord("宇城市", name, address, count))

    if not records:
        raise RuntimeError("宇城市HTMLから避難所行を取得できませんでした。")

    calculated_total = sum(record.evacuee_count for record in records)

    # Also collect candidates from the article text as a fallback.  This covers
    # responsive markup where the total row is visually rendered outside the
    # same physical column structure as the body rows.
    for total_match in re.finditer(
        r"合\s*計(?P<tail>.{0,120})",
        page_text,
    ):
        total_candidates.extend(_numeric_tokens(total_match.group("tail")))

    published_total: int | None = None
    if calculated_total in total_candidates:
        published_total = calculated_total
    elif total_candidates:
        # Fail closed rather than accepting a potentially wrong total.  Include
        # candidates in the error to make future CMS changes diagnosable.
        raise RuntimeError(
            "宇城市HTMLの避難者数合計が一致しません: "
            f"parsed={calculated_total}, total_candidates={sorted(set(total_candidates))}, "
            f"rows={len(records)}"
        )
    else:
        raise RuntimeError(
            "宇城市HTMLから避難者数合計を取得できませんでした: "
            f"parsed={calculated_total}, rows={len(records)}"
        )

    return SourceSnapshot(
        municipality="宇城市",
        observed_at_jst=observed_at,
        source_format="html",
        page_url=page_url,
        document_url=page_url,
        raw_sha256=sha256_bytes(page_data),
        normalized_sha256=normalized_snapshot_hash(records),
        records=records,
        published_total=published_total,
    )
