#!/usr/bin/env python3
"""Collect shelter-level evacuee counts published by Yatsushiro and Uki.

The existing ``data/status_by_date.csv`` is not modified.  This script creates a
parallel ``data/evacuee_count_by_date.csv`` with the same fixed 11 identity
columns and date columns.  Source observations and revisions are retained in a
long-form audit table.

Sources
-------
* Yatsushiro City: an index HTML page linking to a one-page PDF table.
* Uki City: an HTML table.

A blank cell means that the municipal source did not publish a value for that
shelter and date.  It must not be interpreted as zero.  An explicit published
zero is stored as ``0``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))

YATSUSHIRO_PAGE_URL = "https://www.city.yatsushiro.lg.jp/kiji00326798/index.html"
UKI_PAGE_URL = "https://www.city.uki.kumamoto.jp/kurashi/bosaiinfo/2610320"

STATUS_IDENTITY_COLUMNS = [
    "shelter_id",
    "reference_common_ids",
    "municipality",
    "shelter_name",
    "address",
    "reference_same_address_as_emergency_site",
    "reference_other_mayor_matters",
    "reference_accepted_persons",
    "portal_capacity_persons",
    "latitude",
    "longitude",
]

OBSERVATION_COLUMNS = [
    "municipality",
    "source_observed_at_jst",
    "source_observed_date",
    "retrieved_at_jst",
    "source_shelter_name",
    "source_address",
    "evacuee_count",
    "shelter_id",
    "match_status",
    "match_method",
    "match_score",
    "source_format",
    "source_page_url",
    "source_document_url",
    "raw_sha256",
    "normalized_sha256",
    "revision",
]

ISSUE_COLUMNS = OBSERVATION_COLUMNS + [
    "candidate_1_shelter_id",
    "candidate_1_name",
    "candidate_1_address",
    "candidate_1_score",
    "candidate_2_shelter_id",
    "candidate_2_name",
    "candidate_2_address",
    "candidate_2_score",
]

DATE_COLUMN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SourceRecord:
    municipality: str
    shelter_name: str
    address: str
    evacuee_count: int


@dataclass(frozen=True)
class SourceSnapshot:
    municipality: str
    observed_at_jst: str
    source_format: str
    page_url: str
    document_url: str
    raw_sha256: str
    normalized_sha256: str
    records: list[SourceRecord]
    published_total: int | None


@dataclass(frozen=True)
class MatchResult:
    status: str
    method: str
    score: float
    shelter_id: str
    candidates: list[tuple[float, dict[str, str]]]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", html.unescape(str(value)))
    text = text.replace("\u3000", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: object) -> str:
    text = clean_text(value).casefold()
    replacements = {
        "コミュニティーセンター": "コミュニティセンター",
        "コミュニティーセンタ-": "コミュニティセンター",
        "武道場": "武道館",
        "2f": "2階",
        "二階": "2階",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"^(?:熊本県)?(?:八代市|宇城市)(?:立)?", "", text)
    text = re.sub(r"[\s・･·,，.。:：;；/／\\()（）\[\]【】「」『』]", "", text)
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "", text)
    return text


def name_variants(value: object) -> set[str]:
    base = normalize_name(value)
    variants = {base}
    suffixes = ("体育館", "武道館", "交流スペース")
    for suffix in suffixes:
        if base.endswith(suffix):
            variants.add(base[: -len(suffix)])
    variants.add(base.replace("総合体育文化センター", ""))
    variants.add(base.replace("総合体育館", ""))
    variants.discard("")
    return variants


def normalize_address(value: object) -> str:
    text = clean_text(value).casefold()
    text = text.replace("熊本県", "")
    text = re.sub(r"^(?:八代市|宇城市)", "", text)
    text = text.replace("大字", "").replace("字", "")
    text = text.replace("丁目", "").replace("番地", "").replace("番", "").replace("号", "")
    text = re.sub(r"[‐‑‒–—―−ーｰ-]", "", text)
    text = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]", "", text)
    return text


def parse_integer(value: object) -> int:
    text = clean_text(value).replace(",", "")
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"人数を整数として解釈できません: {value!r}")
    return int(text)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_snapshot_hash(records: Iterable[SourceRecord]) -> str:
    payload = [
        {
            "municipality": record.municipality,
            "shelter_name": clean_text(record.shelter_name),
            "address": clean_text(record.address),
            "evacuee_count": record.evacuee_count,
        }
        for record in sorted(
            records,
            key=lambda row: (
                row.municipality,
                normalize_name(row.shelter_name),
                normalize_address(row.address),
            ),
        )
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fetch_bytes(url: str, timeout_seconds: int = 60, retries: int = 4) -> bytes:
    last_error = ""
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "GISPHN-kumamoto-shelter-archive/1.0",
                "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", 200)
                body = response.read()
            if status != 200:
                raise RuntimeError(f"HTTP status {status}")
            if not body:
                raise RuntimeError("empty response body")
            return body
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = f"attempt {attempt}/{retries}: {type(exc).__name__}: {exc}"
            print(f"WARNING fetch failed: {url}: {last_error}")
            if attempt < retries:
                time.sleep(min(12, attempt * 2))
    raise RuntimeError(f"取得できませんでした: {url}: {last_error}")


def decode_html(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def japanese_datetime_to_iso(text: str, default_year: int | None = None) -> str:
    normalized = clean_text(text)
    era_match = re.search(
        r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日.*?(\d+)\s*時\s*(\d+)\s*分",
        normalized,
    )
    if era_match:
        era_year, month, day, hour, minute = map(int, era_match.groups())
        year = 2018 + era_year
        return datetime(year, month, day, hour, minute, tzinfo=JST).isoformat(timespec="minutes")

    year_match = re.search(r"(20\d{2})\s*年", normalized)
    month_day_time = re.search(
        r"(\d+)\s*月\s*(\d+)\s*日.*?(午前|午後)?\s*(\d+)\s*時\s*(\d+)\s*分",
        normalized,
    )
    if month_day_time:
        month, day, ampm, hour, minute = month_day_time.groups()
        year = int(year_match.group(1)) if year_match else default_year
        if year is None:
            raise ValueError(f"観測年を特定できません: {text}")
        hour_value = int(hour)
        if ampm == "午後" and hour_value < 12:
            hour_value += 12
        if ampm == "午前" and hour_value == 12:
            hour_value = 0
        return datetime(
            int(year), int(month), int(day), hour_value, int(minute), tzinfo=JST
        ).isoformat(timespec="minutes")
    raise ValueError(f"観測日時を解析できません: {text}")


def pdf_lines(document: fitz.Document) -> list[str]:
    text_lines: list[str] = []
    for page in document:
        text_lines.extend(clean_text(line) for line in page.get_text("text", sort=True).splitlines())
    rows_from_text = [line for line in text_lines if re.match(r"^\d+\s+", line)]
    if len(rows_from_text) >= 5:
        return text_lines

    # Fallback for PDFs where table cells are separate text blocks.  Words on
    # nearly the same vertical coordinate are reassembled into one visual row.
    visual_lines: list[str] = []
    for page in document:
        words = sorted(page.get_text("words"), key=lambda item: (item[1], item[0]))
        groups: list[list[tuple[Any, ...]]] = []
        for word in words:
            if not groups or abs(float(word[1]) - float(groups[-1][0][1])) > 2.5:
                groups.append([word])
            else:
                groups[-1].append(word)
        for group in groups:
            visual_lines.append(clean_text(" ".join(str(item[4]) for item in sorted(group, key=lambda item: item[0]))))
    return visual_lines


def parse_yatsushiro_pdf(pdf_data: bytes, document_url: str, page_url: str) -> SourceSnapshot:
    document = fitz.open(stream=pdf_data, filetype="pdf")
    try:
        full_text = clean_text("\n".join(page.get_text("text", sort=True) for page in document))
        observed_at = japanese_datetime_to_iso(full_text)
        lines = pdf_lines(document)
    finally:
        document.close()

    records: list[SourceRecord] = []
    seen_numbers: set[int] = set()
    for line in lines:
        normalized = clean_text(line)
        match = re.match(r"^(\d+)\s+(.+?)\s+(八代市\S+)\s+(\S+)\s+([\d,]+)\s+(.+)$", normalized)
        if not match:
            continue
        row_number = int(match.group(1))
        name = clean_text(match.group(2))
        address = clean_text(match.group(3))
        remainder = clean_text(match.group(6))
        numeric_prefix = re.split(r"\s+[〇○×]", remainder, maxsplit=1)[0]
        numbers = re.findall(r"\d[\d,]*", numeric_prefix)
        if not numbers:
            continue
        evacuees = parse_integer(numbers[-1])
        if row_number in seen_numbers:
            continue
        seen_numbers.add(row_number)
        records.append(SourceRecord("八代市", name, address, evacuees))

    total_match = re.search(r"合計\s+[\d,]+\s+[\d,]+\s+([\d,]+)", full_text)
    published_total = parse_integer(total_match.group(1)) if total_match else None
    calculated_total = sum(record.evacuee_count for record in records)
    if not records:
        raise RuntimeError("八代市PDFから避難所行を取得できませんでした。")
    if published_total is not None and calculated_total != published_total:
        raise RuntimeError(
            f"八代市PDFの避難者数合計が一致しません: parsed={calculated_total}, published={published_total}, rows={len(records)}"
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


def collect_yatsushiro(debug_dir: Path) -> SourceSnapshot:
    page_data = fetch_bytes(YATSUSHIRO_PAGE_URL)
    page_html = decode_html(page_data)
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = clean_text(link.get("href"))
        label = clean_text(link.get_text(" "))
        if ".pdf" in href.casefold() and ("避難所" in label or "開設状況" in label):
            candidates.append((urllib.parse.urljoin(YATSUSHIRO_PAGE_URL, href), label))
    if not candidates:
        raise RuntimeError("八代市ページから避難所PDFリンクを検出できませんでした。")
    document_url = candidates[-1][0]
    pdf_data = fetch_bytes(document_url)
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "yatsushiro_page.html").write_bytes(page_data)
    (debug_dir / "yatsushiro_latest.pdf").write_bytes(pdf_data)
    return parse_yatsushiro_pdf(pdf_data, document_url, YATSUSHIRO_PAGE_URL)


def parse_uki_html(page_data: bytes, page_url: str) -> SourceSnapshot:
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
        headers = [clean_text(cell.get_text(" ")) for cell in rows[0].find_all(["th", "td"])]
        if "避難所名" in headers and "避難者数" in headers:
            target_table = table
            header_indexes = {header: index for index, header in enumerate(headers)}
            break
    if target_table is None:
        raise RuntimeError("宇城市ページから避難者数表を検出できませんでした。")

    records: list[SourceRecord] = []
    published_total: int | None = None
    for row in target_table.find_all("tr")[1:]:
        cells = [clean_text(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
        if not cells:
            continue
        joined = " ".join(cells)
        if "合計" in joined:
            numeric_cells = [cell for cell in cells if re.fullmatch(r"[\d,]+", cell)]
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

    calculated_total = sum(record.evacuee_count for record in records)
    if not records:
        raise RuntimeError("宇城市HTMLから避難所行を取得できませんでした。")
    if published_total is not None and calculated_total != published_total:
        raise RuntimeError(
            f"宇城市HTMLの避難者数合計が一致しません: parsed={calculated_total}, published={published_total}, rows={len(records)}"
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


def collect_uki(debug_dir: Path) -> SourceSnapshot:
    page_data = fetch_bytes(UKI_PAGE_URL)
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "uki_page.html").write_bytes(page_data)
    return parse_uki_html(page_data, UKI_PAGE_URL)


def read_csv_with_columns(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: Iterable[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def load_aliases(path: Path) -> dict[tuple[str, str, str], str]:
    _, rows = read_csv_with_columns(path)
    aliases: dict[tuple[str, str, str], str] = {}
    for row in rows:
        municipality = clean_text(row.get("municipality"))
        name = normalize_name(row.get("source_shelter_name"))
        address = normalize_address(row.get("source_address"))
        shelter_id = clean_text(row.get("shelter_id"))
        if municipality and name and shelter_id:
            aliases[(municipality, name, address)] = shelter_id
            aliases[(municipality, name, "")] = shelter_id
    return aliases


def similarity(source: SourceRecord, target: dict[str, str], observation_date: str) -> float:
    source_names = name_variants(source.shelter_name)
    target_names = name_variants(target.get("shelter_name", ""))
    name_score = max(
        SequenceMatcher(None, left, right).ratio()
        for left in source_names
        for right in target_names
    )
    source_address = normalize_address(source.address)
    target_address = normalize_address(target.get("address", ""))
    if source_address and target_address:
        address_score = (
            1.0
            if source_address == target_address
            else SequenceMatcher(None, source_address, target_address).ratio()
        )
    else:
        address_score = 0.0
    state = clean_text(target.get(observation_date, ""))
    open_bonus = 0.04 if state.startswith("開設") else 0.0
    return min(1.0, 0.64 * name_score + 0.32 * address_score + open_bonus)


def match_record(
    record: SourceRecord,
    status_rows: list[dict[str, str]],
    aliases: dict[tuple[str, str, str], str],
    observation_date: str,
) -> MatchResult:
    alias_key = (
        record.municipality,
        normalize_name(record.shelter_name),
        normalize_address(record.address),
    )
    alias_id = aliases.get(alias_key) or aliases.get((alias_key[0], alias_key[1], ""))
    by_id = {clean_text(row.get("shelter_id")): row for row in status_rows}
    if alias_id:
        if alias_id not in by_id:
            return MatchResult("unmatched", "alias_target_missing", 0.0, "", [])
        return MatchResult("matched", "manual_alias", 1.0, alias_id, [(1.0, by_id[alias_id])])

    pool = [row for row in status_rows if clean_text(row.get("municipality")) == record.municipality]
    if not pool:
        return MatchResult("unmatched", "municipality_not_found", 0.0, "", [])

    source_name_set = name_variants(record.shelter_name)
    source_address = normalize_address(record.address)
    exact_name = [
        row for row in pool if source_name_set & name_variants(row.get("shelter_name", ""))
    ]
    exact_address = [
        row for row in pool
        if source_address and normalize_address(row.get("address", "")) == source_address
    ]

    intersect = [row for row in exact_name if row in exact_address]
    if intersect:
        ranked = sorted(
            ((similarity(record, row, observation_date), row) for row in intersect),
            key=lambda item: (-item[0], clean_text(item[1].get("shelter_id"))),
        )
        open_rows = [item for item in ranked if clean_text(item[1].get(observation_date)).startswith("開設")]
        selected = open_rows[0] if len(open_rows) == 1 else ranked[0]
        if len(ranked) == 1 or (selected[0] - ranked[1][0] >= 0.025) or len(open_rows) == 1:
            return MatchResult(
                "matched",
                "exact_name_address" if len(ranked) == 1 else "exact_name_address_open_priority",
                selected[0],
                clean_text(selected[1].get("shelter_id")),
                ranked[:2],
            )

    candidates = sorted(
        ((similarity(record, row, observation_date), row) for row in pool),
        key=lambda item: (-item[0], clean_text(item[1].get("shelter_id"))),
    )
    top_score, top_row = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0

    if exact_address:
        address_ranked = sorted(
            ((similarity(record, row, observation_date), row) for row in exact_address),
            key=lambda item: (-item[0], clean_text(item[1].get("shelter_id"))),
        )
        address_top = address_ranked[0]
        address_second = address_ranked[1][0] if len(address_ranked) > 1 else 0.0
        if address_top[0] >= 0.72 and address_top[0] - address_second >= 0.04:
            return MatchResult(
                "matched",
                "exact_address_name_similarity",
                address_top[0],
                clean_text(address_top[1].get("shelter_id")),
                address_ranked[:2],
            )

    if len(exact_name) == 1:
        row = exact_name[0]
        score = similarity(record, row, observation_date)
        if score >= 0.70:
            return MatchResult(
                "matched", "exact_normalized_name", score, clean_text(row.get("shelter_id")), [(score, row)]
            )

    if top_score >= 0.86 and top_score - second_score >= 0.055:
        return MatchResult(
            "matched", "high_confidence_fuzzy", top_score, clean_text(top_row.get("shelter_id")), candidates[:2]
        )
    if top_score >= 0.74:
        return MatchResult("ambiguous", "candidate_margin_too_small", top_score, "", candidates[:2])
    return MatchResult("unmatched", "no_high_confidence_candidate", top_score, "", candidates[:2])


def observation_group_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        clean_text(row.get("municipality")),
        clean_text(row.get("source_observed_at_jst")),
        clean_text(row.get("normalized_sha256")),
        clean_text(row.get("revision")),
    )


def build_observation_rows(
    snapshot: SourceSnapshot,
    status_rows: list[dict[str, str]],
    aliases: dict[tuple[str, str, str], str],
    retrieved_at: str,
    revision: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    observation_date = snapshot.observed_at_jst[:10]
    rows: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    matched_ids: set[str] = set()

    for source in snapshot.records:
        result = match_record(source, status_rows, aliases, observation_date)
        row: dict[str, object] = {
            "municipality": source.municipality,
            "source_observed_at_jst": snapshot.observed_at_jst,
            "source_observed_date": observation_date,
            "retrieved_at_jst": retrieved_at,
            "source_shelter_name": source.shelter_name,
            "source_address": source.address,
            "evacuee_count": source.evacuee_count,
            "shelter_id": result.shelter_id,
            "match_status": result.status,
            "match_method": result.method,
            "match_score": f"{result.score:.3f}" if result.score else "",
            "source_format": snapshot.source_format,
            "source_page_url": snapshot.page_url,
            "source_document_url": snapshot.document_url,
            "raw_sha256": snapshot.raw_sha256,
            "normalized_sha256": snapshot.normalized_sha256,
            "revision": revision,
        }
        rows.append(row)
        if result.status == "matched":
            if result.shelter_id in matched_ids:
                raise RuntimeError(
                    f"同一観測内で複数の自治体行が同じshelter_idへ照合されました: {source.municipality} {result.shelter_id}"
                )
            matched_ids.add(result.shelter_id)
        else:
            issue = dict(row)
            for index in range(2):
                prefix = f"candidate_{index + 1}"
                if index < len(result.candidates):
                    score, candidate = result.candidates[index]
                    issue[f"{prefix}_shelter_id"] = candidate.get("shelter_id", "")
                    issue[f"{prefix}_name"] = candidate.get("shelter_name", "")
                    issue[f"{prefix}_address"] = candidate.get("address", "")
                    issue[f"{prefix}_score"] = f"{score:.3f}"
                else:
                    issue[f"{prefix}_shelter_id"] = ""
                    issue[f"{prefix}_name"] = ""
                    issue[f"{prefix}_address"] = ""
                    issue[f"{prefix}_score"] = ""
            issues.append(issue)
    return rows, issues


def latest_revision_rows(observations: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in observations:
        grouped.setdefault(
            (clean_text(row.get("municipality")), clean_text(row.get("source_observed_at_jst"))), []
        ).append(row)

    selected: list[dict[str, str]] = []
    for group_rows in grouped.values():
        highest = max(int(clean_text(row.get("revision")) or "0") for row in group_rows)
        selected.extend(
            row for row in group_rows if int(clean_text(row.get("revision")) or "0") == highest
        )
    return selected


def build_wide_rows(
    status_columns: list[str],
    status_rows: list[dict[str, str]],
    observations: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, str]]]:
    status_dates = [column for column in status_columns if DATE_COLUMN_RE.fullmatch(column)]
    observation_dates = sorted(
        {clean_text(row.get("source_observed_date")) for row in observations if DATE_COLUMN_RE.fullmatch(clean_text(row.get("source_observed_date")))}
    )
    dates = sorted(set(status_dates) | set(observation_dates))
    values: dict[tuple[str, str], tuple[str, int]] = {}
    for row in latest_revision_rows(observations):
        if clean_text(row.get("match_status")) != "matched":
            continue
        shelter_id = clean_text(row.get("shelter_id"))
        observed_date = clean_text(row.get("source_observed_date"))
        observed_at = clean_text(row.get("source_observed_at_jst"))
        revision = int(clean_text(row.get("revision")) or "0")
        count = clean_text(row.get("evacuee_count"))
        key = (shelter_id, observed_date)
        prior = values.get(key)
        priority = f"{observed_at}|{revision:06d}"
        if prior is None or priority > prior[0]:
            values[key] = (priority, int(count))

    wide_rows: list[dict[str, str]] = []
    for status_row in status_rows:
        output = {column: clean_text(status_row.get(column)) for column in STATUS_IDENTITY_COLUMNS}
        shelter_id = output["shelter_id"]
        for observed_date in dates:
            item = values.get((shelter_id, observed_date))
            output[observed_date] = "" if item is None else str(item[1])
        wide_rows.append(output)
    return STATUS_IDENTITY_COLUMNS + dates, wide_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--status-csv", default="data/status_by_date.csv")
    parser.add_argument(
        "--aliases-csv", default="reference/municipal_evacuee_shelter_aliases.csv"
    )
    parser.add_argument(
        "--observations-csv", default="data/municipal_evacuees/all_observations.csv"
    )
    parser.add_argument(
        "--wide-csv", default="data/evacuee_count_by_date.csv"
    )
    parser.add_argument(
        "--issues-csv", default="data/municipal_evacuees/matching_issues.csv"
    )
    parser.add_argument(
        "--latest-csv", default="data/municipal_evacuees/latest_observations.csv"
    )
    parser.add_argument(
        "--log-json", default="data/logs/municipal_evacuee_collection.json"
    )
    parser.add_argument("--debug-dir", default="debug/municipal_evacuees")
    parser.add_argument("--allow-unmatched", action="store_true")
    args = parser.parse_args()

    retrieved_at = datetime.now(JST).isoformat(timespec="seconds")
    status_columns, status_rows = read_csv_with_columns(Path(args.status_csv))
    if not status_rows:
        raise RuntimeError(f"status_by_date.csvが空です: {args.status_csv}")
    if status_columns[: len(STATUS_IDENTITY_COLUMNS)] != STATUS_IDENTITY_COLUMNS:
        raise RuntimeError(
            "status_by_date.csvの固定11列が想定と一致しません: "
            f"actual={status_columns[:len(STATUS_IDENTITY_COLUMNS)]}"
        )
    shelter_ids = [clean_text(row.get("shelter_id")) for row in status_rows]
    if not all(shelter_ids) or len(shelter_ids) != len(set(shelter_ids)):
        raise RuntimeError("status_by_date.csvのshelter_idが空欄または重複しています。")

    debug_dir = Path(args.debug_dir)
    snapshots = [collect_yatsushiro(debug_dir), collect_uki(debug_dir)]
    aliases = load_aliases(Path(args.aliases_csv))

    observations_path = Path(args.observations_csv)
    existing_columns, existing_rows = read_csv_with_columns(observations_path)
    if existing_columns and existing_columns != OBSERVATION_COLUMNS:
        raise RuntimeError(
            f"既存観測CSVの列構成が不正です: {existing_columns}"
        )

    new_rows: list[dict[str, object]] = []
    new_issues: list[dict[str, object]] = []
    changed_sources: list[dict[str, object]] = []
    existing_keys = {
        (
            clean_text(row.get("municipality")),
            clean_text(row.get("source_observed_at_jst")),
            clean_text(row.get("normalized_sha256")),
        )
        for row in existing_rows
    }

    for snapshot in snapshots:
        key = (
            snapshot.municipality,
            snapshot.observed_at_jst,
            snapshot.normalized_sha256,
        )
        if key in existing_keys:
            print(
                f"No source change: {snapshot.municipality} observed={snapshot.observed_at_jst} hash={snapshot.normalized_sha256[:12]}"
            )
            continue
        same_time_revisions = [
            int(clean_text(row.get("revision")) or "0")
            for row in existing_rows
            if clean_text(row.get("municipality")) == snapshot.municipality
            and clean_text(row.get("source_observed_at_jst")) == snapshot.observed_at_jst
        ]
        revision = max(same_time_revisions, default=0) + 1
        rows, issues = build_observation_rows(
            snapshot, status_rows, aliases, retrieved_at, revision
        )
        new_rows.extend(rows)
        new_issues.extend(issues)
        changed_sources.append(
            {
                "municipality": snapshot.municipality,
                "source_observed_at_jst": snapshot.observed_at_jst,
                "record_count": len(rows),
                "matched_count": sum(row["match_status"] == "matched" for row in rows),
                "issue_count": len(issues),
                "published_total": snapshot.published_total,
                "calculated_total": sum(int(row["evacuee_count"]) for row in rows),
                "revision": revision,
                "normalized_sha256": snapshot.normalized_sha256,
            }
        )

    combined_rows: list[dict[str, str]] = [
        {column: clean_text(row.get(column)) for column in OBSERVATION_COLUMNS}
        for row in existing_rows
    ]
    combined_rows.extend(
        {column: clean_text(row.get(column)) for column in OBSERVATION_COLUMNS}
        for row in new_rows
    )

    all_issue_rows = [
        row for row in combined_rows if clean_text(row.get("match_status")) != "matched"
    ]
    if new_issues and not args.allow_unmatched:
        write_csv(Path(args.issues_csv), new_issues, ISSUE_COLUMNS)
        raise RuntimeError(
            f"自治体避難者数の未一致・曖昧施設があります: {len(new_issues)}件。matching_issues.csvを確認してください。"
        )

    # Rebuild outputs even when the source did not change, so schema/reference
    # updates can be applied without inventing a new observation revision.
    write_csv(observations_path, combined_rows, OBSERVATION_COLUMNS)
    issue_output_rows: list[dict[str, object]] = []
    for row in all_issue_rows:
        issue = dict(row)
        for column in ISSUE_COLUMNS:
            issue.setdefault(column, "")
        issue_output_rows.append(issue)
    write_csv(Path(args.issues_csv), issue_output_rows, ISSUE_COLUMNS)

    latest = latest_revision_rows(combined_rows)
    latest_by_municipality: dict[str, str] = {}
    for row in latest:
        municipality = clean_text(row.get("municipality"))
        observed_at = clean_text(row.get("source_observed_at_jst"))
        latest_by_municipality[municipality] = max(
            observed_at, latest_by_municipality.get(municipality, "")
        )
    latest_rows = [
        row
        for row in latest
        if clean_text(row.get("source_observed_at_jst"))
        == latest_by_municipality.get(clean_text(row.get("municipality")), "")
    ]
    write_csv(Path(args.latest_csv), latest_rows, OBSERVATION_COLUMNS)

    wide_columns, wide_rows = build_wide_rows(status_columns, status_rows, combined_rows)
    write_csv(Path(args.wide_csv), wide_rows, wide_columns)

    unmatched_total = sum(
        clean_text(row.get("match_status")) != "matched" for row in combined_rows
    )
    report = {
        "retrieved_at_jst": retrieved_at,
        "status": "success",
        "source_changed": bool(changed_sources),
        "changed_sources": changed_sources,
        "observation_row_count": len(combined_rows),
        "matched_observation_row_count": len(combined_rows) - unmatched_total,
        "unmatched_or_ambiguous_row_count": unmatched_total,
        "wide_row_count": len(wide_rows),
        "wide_date_columns": [
            column for column in wide_columns if DATE_COLUMN_RE.fullmatch(column)
        ],
        "wide_fixed_columns": STATUS_IDENTITY_COLUMNS,
        "blank_means": "municipal source did not publish a value; not zero",
    }
    # Avoid an hourly commit when neither the source nor an output schema changed.
    log_path = Path(args.log_json)
    previous_report = None
    if log_path.exists():
        try:
            previous_report = json.loads(log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_report = None
    comparable = dict(report)
    comparable.pop("retrieved_at_jst", None)
    previous_comparable = dict(previous_report or {})
    previous_comparable.pop("retrieved_at_jst", None)
    if changed_sources or comparable != previous_comparable or not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
