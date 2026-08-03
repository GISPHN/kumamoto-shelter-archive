"""Structured parser for Uki City's shelter evacuee HTML table."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup


def parse_uki_html(page_data: bytes, page_url: str):
    from collect_municipal_evacuees import (
        SourceRecord,
        SourceSnapshot,
        clean_text,
        decode_html,
        japanese_datetime_to_iso,
        normalized_snapshot_hash,
        parse_integer,
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
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [
            clean_text(cell.get_text(" "))
            for cell in rows[0].find_all(["th", "td"])
        ]
        if "避難所名" in headers and "避難者数" in headers:
            target_table = table
            header_indexes = {header: index for index, header in enumerate(headers)}
            break
    if target_table is None:
        raise RuntimeError("宇城市ページから避難者数表を検出できませんでした。")

    records: list[SourceRecord] = []
    published_total: int | None = None
    for row in target_table.find_all("tr")[1:]:
        cells = [
            clean_text(cell.get_text(" "))
            for cell in row.find_all(["th", "td"])
        ]
        if not cells:
            continue
        joined = " ".join(cells)
        if re.search(r"合\s*計", joined):
            numeric_cells = [
                cell for cell in cells if re.fullmatch(r"[\d,]+", cell)
            ]
            if numeric_cells:
                published_total = parse_integer(numeric_cells[-1])
            continue
        try:
            name = cells[header_indexes["避難所名"]]
            address = cells[header_indexes["住所"]]
            count = parse_integer(cells[header_indexes["避難者数"]])
        except (IndexError, KeyError, ValueError):
            continue
        records.append(SourceRecord("宇城市", name, address, count))

    if published_total is None:
        # Responsive table markup can separate the total label and numeric
        # values into different cells.  The plain article text still retains
        # the order households, evacuees; the second number is the target.
        total_match = re.search(
            r"合\s*計\s+([\d,]+)\s+([\d,]+)",
            page_text,
        )
        if total_match:
            published_total = parse_integer(total_match.group(2))

    calculated_total = sum(record.evacuee_count for record in records)
    if not records:
        raise RuntimeError("宇城市HTMLから避難所行を取得できませんでした。")
    if published_total is None:
        raise RuntimeError("宇城市HTMLから避難者数合計を取得できませんでした。")
    if calculated_total != published_total:
        raise RuntimeError(
            "宇城市HTMLの避難者数合計が一致しません: "
            f"parsed={calculated_total}, published={published_total}, rows={len(records)}"
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
