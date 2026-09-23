"""Structured parser for Uki City's shelter evacuee HTML table."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup


_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)(?!\d)")
_OBSERVED_RE = re.compile(
    # The CMS may render weekdays as a circled compatibility character such
    # as ㈫. NFKC normalization expands it to (火), producing nested
    # parentheses in strings such as 9月22日((火)曜日)22時00分時点.
    # Match a short, non-numeric weekday decoration instead of assuming one
    # flat pair of parentheses.
    r"\d+月\s*\d+日(?:\s*[^\d]{0,24}?)?\s*(?:午前|午後)?\s*\d+時\s*\d+分時点"
)


def _numeric_tokens(text: str) -> list[int]:
    values: list[int] = []
    for token in _NUMBER_RE.findall(text):
        try:
            values.append(int(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _cell_integer(text: str) -> int:
    values = _numeric_tokens(text)
    if not values:
        raise ValueError(f"numeric value not found: {text!r}")
    return values[-1]


def _nearest_observation_text(table, clean_text) -> str:
    """Find the observation phrase immediately preceding the selected table."""
    context = ""
    for node in table.find_all_previous(string=True, limit=80):
        text = clean_text(node)
        if not text:
            continue

        # Inline CMS markup can split one visible sentence across several
        # text nodes (for example 9月22日(, ㈫曜日, and )22時00分時点).
        # Reassemble a bounded amount of preceding text in document order
        # before applying the observation-time pattern.
        context = clean_text(f"{text} {context}")[-1200:]
        if "避難者数" not in context or "時点" not in context:
            continue
        matches = list(_OBSERVED_RE.finditer(context))
        if matches:
            # The last match is the one nearest the selected table.
            return matches[-1].group(0)
    raise RuntimeError(
        "宇城市の避難者数表の直前から観測時刻を検出できませんでした。"
    )


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

    target_table = None
    header_indexes: dict[str, int] = {}
    header_row_index = -1
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
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

    observed_text = _nearest_observation_text(target_table, clean_text)
    observed_at = japanese_datetime_to_iso(observed_text, default_year)

    records: list[SourceRecord] = []
    total_row_values: list[int] | None = None
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
            # Responsive/colspan markup may append additional numeric values to
            # the total row. Preserve every numeric token and later identify the
            # published evacuee total by agreement with the independently parsed
            # shelter-row sum instead of relying on a physical cell position.
            values: list[int] = []
            for cell in cells:
                values.extend(_numeric_tokens(cell))
            if values:
                total_row_values = values
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
    if not total_row_values:
        raise RuntimeError(
            "宇城市HTMLの選択表から合計行を取得できませんでした: "
            f"parsed={calculated_total}, rows={len(records)}"
        )

    matching_totals = [value for value in total_row_values if value == calculated_total]
    if not matching_totals:
        raise RuntimeError(
            "宇城市HTMLの避難者数合計が一致しません: "
            f"parsed={calculated_total}, total_row_values={total_row_values}, "
            f"rows={len(records)}"
        )
    published_total = matching_totals[0]

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
