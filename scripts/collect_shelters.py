#!/usr/bin/env python3
"""Collect all evacuation shelter rows from the Kumamoto disaster portal.

This collector intentionally reads the rendered public web page rather than a
JSON or other API endpoint. It uses Playwright so that JavaScript-rendered
content and paginated tables can be handled.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import os
import re
import sys
import traceback
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from scripts.reference_matcher import (
        REFERENCE_OUTPUT_COLUMNS,
        ReferenceMatcher,
        tracking_id,
    )
except ModuleNotFoundError:  # direct execution: python scripts/collect_shelters.py
    from reference_matcher import (
        REFERENCE_OUTPUT_COLUMNS,
        ReferenceMatcher,
        tracking_id,
    )

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_URL = (
    "https://portal.bousai.pref.kumamoto.jp/sp.html?"
    "p=evacuation%2Fshelter&l=15-0&"
    "ll=32.63819999999999%2C130.77610000000004&z=9&municipalityCd=430005"
)

CSV_COLUMNS = [
    "snapshot_date_jst",
    "retrieved_at_jst",
    "source_updated_at_text",
    "source_updated_at_jst",
    "shelter_id",
    "web_shelter_id",
    "municipality",
    "shelter_name",
    "opening_status",
    "is_open",
    "congestion_status",
    "evacuee_count",
    "address",
    "route_search",
    "change_type",
    "previous_opening_status",
    "previous_congestion_status",
    "previous_evacuee_count",
    "source_url",
    "record_hash",
    "raw_row_json",
] + REFERENCE_OUTPUT_COLUMNS

CHANGE_COLUMNS = CSV_COLUMNS + ["change_details"]
MATCH_ISSUE_COLUMNS = CSV_COLUMNS
LOG_COLUMNS = [
    "snapshot_date_jst",
    "retrieved_at_jst",
    "status",
    "record_count",
    "open_count",
    "closed_or_inactive_count",
    "unknown_status_count",
    "changed_count",
    "reference_matched_count",
    "reference_matched_multiple_count",
    "reference_ambiguous_count",
    "reference_unmatched_count",
    "reference_match_rate",
    "reference_source_sha256",
    "source_updated_at_text",
    "source_updated_at_jst",
    "source_url",
    "message",
]

HEADER_ALIASES = {
    "municipality": ("市町村", "自治体", "市区町村"),
    "shelter_name": ("避難所名", "避難場所名", "施設名"),
    "opening_status": ("開設状況", "開設状態", "状態"),
    "congestion_status": ("混雑状況", "混雑状態"),
    "evacuee_count": ("避難者数", "避難者人数", "収容者数", "現在避難者数"),
    "address": ("住所", "所在地"),
    "route_search": ("ルート検索", "経路検索"),
}


@dataclass
class CollectionResult:
    headers: list[str]
    rows: list[dict[str, str]]
    source_updated_at_text: str
    screenshot_path: Path | None = None
    html_path: Path | None = None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_header(value: Any) -> str:
    return normalize_text(value).replace(" ", "")


def canonical_key(header: str) -> str | None:
    normalized = normalize_header(header)
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            alias_normalized = normalize_header(alias)
            if normalized == alias_normalized or alias_normalized in normalized:
                return key
    return None


def parse_source_updated_at(text: str, retrieved_at: datetime) -> str:
    normalized = normalize_text(text)
    patterns = [
        r"(?P<year>\d{2,4})/(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        r"(?P<year>\d{2,4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*(?P<hour>\d{1,2})時(?P<minute>\d{1,2})分",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        parts = {key: int(value) for key, value in match.groupdict().items()}
        year = parts["year"]
        if year < 100:
            year += 2000
        try:
            parsed = datetime(
                year,
                parts["month"],
                parts["day"],
                parts["hour"],
                parts["minute"],
                tzinfo=JST,
            )
            return parsed.isoformat(timespec="minutes")
        except ValueError:
            continue
    return ""


def parse_int(value: str) -> str:
    normalized = normalize_text(value)
    if not normalized or normalized in {"---", "-", "不明", "未入力"}:
        return ""
    match = re.search(r"-?\d[\d,]*", normalized)
    if not match:
        return ""
    return match.group(0).replace(",", "")


def classify_open_status(value: str) -> str:
    status = normalize_text(value)
    if not status or status in {"---", "-", "不明", "未入力"}:
        return "false" if status in {"---", "-"} else "unknown"
    closed_tokens = ("閉鎖", "未開設", "閉設", "解除", "終了", "休止")
    if any(token in status for token in closed_tokens):
        return "false"
    if "開設" in status or "開所" in status:
        return "true"
    return "unknown"


def stable_shelter_id(municipality: str, shelter_name: str, address: str) -> str:
    identity = "|".join(normalize_text(v).casefold() for v in (municipality, shelter_name, address))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def record_hash(record: dict[str, str]) -> str:
    keys = (
        "municipality",
        "shelter_name",
        "opening_status",
        "congestion_status",
        "evacuee_count",
        "address",
        "reference_common_ids",
    )
    payload = "|".join(normalize_text(record.get(key, "")) for key in keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def row_to_normalized(
    raw_row: dict[str, str],
    snapshot_date: date,
    retrieved_at: datetime,
    source_updated_at_text: str,
    source_url: str,
) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for header, value in raw_row.items():
        key = canonical_key(header)
        if key and key not in canonical:
            canonical[key] = normalize_text(value)

    municipality = canonical.get("municipality", "")
    shelter_name = canonical.get("shelter_name", "")
    address = canonical.get("address", "")
    opening_status = canonical.get("opening_status", "")
    congestion_status = canonical.get("congestion_status", "")
    evacuee_count = parse_int(canonical.get("evacuee_count", ""))

    web_shelter_id = stable_shelter_id(municipality, shelter_name, address)
    normalized = {
        "snapshot_date_jst": snapshot_date.isoformat(),
        "retrieved_at_jst": retrieved_at.isoformat(timespec="seconds"),
        "source_updated_at_text": source_updated_at_text,
        "source_updated_at_jst": parse_source_updated_at(source_updated_at_text, retrieved_at),
        "shelter_id": web_shelter_id,
        "web_shelter_id": web_shelter_id,
        "municipality": municipality,
        "shelter_name": shelter_name,
        "opening_status": opening_status,
        "is_open": classify_open_status(opening_status),
        "congestion_status": congestion_status,
        "evacuee_count": evacuee_count,
        "address": address,
        "route_search": canonical.get("route_search", ""),
        "change_type": "",
        "previous_opening_status": "",
        "previous_congestion_status": "",
        "previous_evacuee_count": "",
        "source_url": source_url,
        "record_hash": "",
        "raw_row_json": json.dumps(raw_row, ensure_ascii=False, sort_keys=True),
    }
    normalized["record_hash"] = record_hash(normalized)
    return normalized


def compare_records(
    current: dict[str, str], previous: dict[str, str] | None
) -> tuple[str, str]:
    if previous is None:
        return "new", "前回スナップショットに同一避難所なし"

    changes: list[str] = []
    details: list[str] = []
    previous_is_open = previous.get("is_open", "unknown")
    current_is_open = current.get("is_open", "unknown")

    if previous_is_open != current_is_open:
        if previous_is_open == "false" and current_is_open == "true":
            changes.append("opened")
            details.append("未開設または閉鎖から開設へ変化")
        elif previous_is_open == "true" and current_is_open == "false":
            changes.append("closed")
            details.append("開設から未開設または閉鎖へ変化")
        else:
            changes.append("open_state_changed")
            details.append(f"is_open: {previous_is_open} -> {current_is_open}")

    fields = [
        ("opening_status", "opening_status_changed", "開設状況"),
        ("congestion_status", "congestion_changed", "混雑状況"),
        ("evacuee_count", "evacuee_count_changed", "避難者数"),
        ("address", "address_changed", "住所"),
    ]
    for field, label, jp_name in fields:
        before = normalize_text(previous.get(field, ""))
        after = normalize_text(current.get(field, ""))
        if before != after:
            if label not in changes:
                changes.append(label)
            details.append(f"{jp_name}: {before or '(空欄)'} -> {after or '(空欄)'}")

    if not changes:
        return "unchanged", ""
    return ";".join(changes), " / ".join(details)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def append_log(path: Path, row: dict[str, str]) -> None:
    existing = load_csv(path)
    key = (row.get("snapshot_date_jst"), row.get("retrieved_at_jst"))
    existing = [
        item
        for item in existing
        if (item.get("snapshot_date_jst"), item.get("retrieved_at_jst")) != key
    ]
    existing.append(row)
    existing.sort(key=lambda item: item.get("retrieved_at_jst", ""))
    write_csv(path, existing, LOG_COLUMNS)


def previous_daily_file(data_root: Path, current_path: Path) -> Path | None:
    candidates = sorted(data_root.glob("daily/*/*/*.csv"))
    earlier = [path for path in candidates if path != current_path and path.name < current_path.name]
    return earlier[-1] if earlier else None


def rebuild_all_snapshots(data_root: Path) -> None:
    all_rows: list[dict[str, str]] = []
    for path in sorted(data_root.glob("daily/*/*/*.csv")):
        all_rows.extend(load_csv(path))
    write_csv(data_root / "all_snapshots.csv", all_rows, CSV_COLUMNS)


def clean_rows(headers: list[str], rows: list[list[str]]) -> list[dict[str, str]]:
    cleaned_headers = [normalize_text(value) or f"column_{index + 1}" for index, value in enumerate(headers)]
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_values in rows:
        values = [normalize_text(value) for value in raw_values]
        if not any(values):
            continue
        if len(values) < len(cleaned_headers):
            values.extend([""] * (len(cleaned_headers) - len(values)))
        elif len(values) > len(cleaned_headers):
            values = values[: len(cleaned_headers)]
        row = dict(zip(cleaned_headers, values, strict=True))
        if any("条件に一致するデータが存在しません" in value for value in values):
            continue
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(row)
    return output


async def collect_rendered_page(url: str, debug_dir: Path, timeout_ms: int) -> CollectionResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r requirements.txt && "
            "python -m playwright install chromium"
        ) from exc

    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = debug_dir / "latest_page.png"
    html_path = debug_dir / "latest_page.html"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/124 Safari/537.36 KumamotoShelterArchive/1.0"
            ),
            viewport={"width": 1440, "height": 1200},
        )
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)

            # Explicitly select the site's "all shelters" mode. The site may use
            # a label, link, radio button, or JavaScript click handler.
            all_selected = False
            candidate_selectors = [
                "label:has-text('全ての避難所')",
                "a:has-text('全ての避難所')",
                "button:has-text('全ての避難所')",
                "text=全ての避難所",
            ]
            for selector in candidate_selectors:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    try:
                        await locator.first.click(force=True)
                        all_selected = True
                        await page.wait_for_timeout(2500)
                        break
                    except Exception:
                        continue

            # Attempt to select the DataTables "all rows" option where present.
            selects = page.locator("select")
            for index in range(await selects.count()):
                select = selects.nth(index)
                try:
                    options = await select.locator("option").evaluate_all(
                        "els => els.map(e => ({value: e.value, text: e.textContent.trim()}))"
                    )
                    preferred = next(
                        (
                            option["value"]
                            for option in options
                            if option["value"] == "-1"
                            or any(token in option["text"] for token in ("全件", "すべて", "全て", "All"))
                        ),
                        None,
                    )
                    if preferred is None:
                        numeric = [
                            (int(option["value"]), option["value"])
                            for option in options
                            if re.fullmatch(r"\d+", option["value"] or "")
                        ]
                        if numeric:
                            preferred = max(numeric)[1]
                    if preferred is not None:
                        await select.select_option(preferred)
                        await page.wait_for_timeout(1000)
                except Exception:
                    continue

            # Resolve the table by its semantic header instead of a fragile id.
            table_info = await page.evaluate(
                r"""
                () => {
                  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                  const tables = Array.from(document.querySelectorAll('table'));
                  for (const table of tables) {
                    const headers = Array.from(table.querySelectorAll('thead th, tr th')).map(th => norm(th.textContent));
                    if (headers.some(h => h.includes('避難所名'))) {
                      return {found: true, headers};
                    }
                  }
                  return {found: false, headers: []};
                }
                """
            )
            if not table_info.get("found"):
                raise RuntimeError(
                    "避難所名を見出しに含む表を検出できませんでした。サイト構造が変更された可能性があります。"
                )

            # First choice: use the initialized DataTables model. This returns
            # every filtered row, not only the visible page.
            extracted = await page.evaluate(
                r"""
                () => {
                  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                  const strip = value => {
                    if (value === null || value === undefined) return '';
                    if (typeof value === 'string' && value.includes('<')) {
                      const div = document.createElement('div');
                      div.innerHTML = value;
                      return norm(div.textContent);
                    }
                    if (typeof value === 'object') return norm(value.display || value._ || JSON.stringify(value));
                    return norm(String(value));
                  };
                  const table = Array.from(document.querySelectorAll('table')).find(t =>
                    Array.from(t.querySelectorAll('thead th, tr th')).some(th => norm(th.textContent).includes('避難所名'))
                  );
                  if (!table) return {mode: 'none', headers: [], rows: []};
                  const headers = Array.from(table.querySelectorAll('thead th')).map(th => norm(th.textContent));
                  if (window.jQuery && jQuery.fn && jQuery.fn.DataTable && jQuery.fn.DataTable.isDataTable(table)) {
                    const api = jQuery(table).DataTable();
                    const dtHeaders = api.columns().header().toArray().map(th => norm(th.textContent));
                    const rows = api.rows({search: 'applied'}).data().toArray().map(row => {
                      if (Array.isArray(row)) return row.map(strip);
                      if (row && typeof row === 'object') return dtHeaders.map(h => strip(row[h]));
                      return [strip(row)];
                    });
                    return {mode: 'datatable', headers: dtHeaders, rows};
                  }
                  const rows = Array.from(table.querySelectorAll('tbody tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => norm(td.textContent))
                  );
                  return {mode: 'dom', headers, rows};
                }
                """
            )

            headers = extracted.get("headers", [])
            rows = extracted.get("rows", [])

            # DOM fallback: iterate visible pagination until the Next control is
            # disabled or no new rows are found.
            if extracted.get("mode") != "datatable":
                collected_rows: list[list[str]] = []
                seen_pages: set[str] = set()
                for _ in range(500):
                    page_rows = await page.evaluate(
                        r"""
                        () => {
                          const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                          const table = Array.from(document.querySelectorAll('table')).find(t =>
                            Array.from(t.querySelectorAll('thead th, tr th')).some(th => norm(th.textContent).includes('避難所名'))
                          );
                          if (!table) return [];
                          return Array.from(table.querySelectorAll('tbody tr')).map(tr =>
                            Array.from(tr.querySelectorAll('td')).map(td => norm(td.textContent))
                          );
                        }
                        """
                    )
                    signature = json.dumps(page_rows, ensure_ascii=False)
                    if signature in seen_pages:
                        break
                    seen_pages.add(signature)
                    collected_rows.extend(page_rows)

                    next_locator = page.locator(
                        ".paginate_button.next:not(.disabled), "
                        "a[aria-label='Next']:not([aria-disabled='true']), "
                        "button[aria-label='Next']:not([disabled]), "
                        "a:has-text('次へ'), button:has-text('次へ')"
                    )
                    clicked = False
                    for index in range(await next_locator.count()):
                        candidate = next_locator.nth(index)
                        try:
                            classes = (await candidate.get_attribute("class")) or ""
                            aria_disabled = (await candidate.get_attribute("aria-disabled")) or ""
                            disabled = await candidate.is_disabled() if await candidate.evaluate("e => 'disabled' in e") else False
                            if "disabled" in classes or aria_disabled == "true" or disabled:
                                continue
                            await candidate.click(force=True)
                            await page.wait_for_timeout(500)
                            clicked = True
                            break
                        except Exception:
                            continue
                    if not clicked:
                        break
                rows = collected_rows

            body_text = normalize_text(await page.locator("body").inner_text())
            source_match = re.search(
                r"(?:\d{2,4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}|"
                r"\d{2,4}年\d{1,2}月\d{1,2}日\s*\d{1,2}時\d{1,2}分)"
                r"時点の避難所状況の一覧です。?",
                body_text,
            )
            source_updated_at_text = source_match.group(0) if source_match else ""

            await page.screenshot(path=str(screenshot_path), full_page=True)
            html_path.write_text(await page.content(), encoding="utf-8")

            cleaned = clean_rows(headers, rows)
            return CollectionResult(
                headers=[normalize_text(header) for header in headers],
                rows=cleaned,
                source_updated_at_text=source_updated_at_text,
                screenshot_path=screenshot_path,
                html_path=html_path,
            )
        except Exception:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
                html_path.write_text(await page.content(), encoding="utf-8")
            except Exception:
                pass
            raise
        finally:
            await context.close()
            await browser.close()


def validate_collection(rows: list[dict[str, str]], minimum_rows: int) -> None:
    if len(rows) < minimum_rows:
        raise RuntimeError(
            f"全避難所の取得件数が想定下限を下回りました: {len(rows)}件 < {minimum_rows}件。"
            "『全ての避難所』への切替失敗またはサイト構造変更の可能性があります。"
        )
    names = [normalize_text(row.get("shelter_name", "")) for row in rows]
    if not any(names):
        raise RuntimeError("避難所名を1件も取得できませんでした。")
    duplicate_ids = len(rows) - len({row.get("shelter_id", "") for row in rows})
    if duplicate_ids:
        print(f"WARNING: shelter_id duplicates: {duplicate_ids}", file=sys.stderr)


def build_paths(data_root: Path, snapshot_date: date) -> tuple[Path, Path]:
    daily = (
        data_root
        / "daily"
        / f"{snapshot_date.year:04d}"
        / f"{snapshot_date.month:02d}"
        / f"{snapshot_date.isoformat()}.csv"
    )
    changes = (
        data_root
        / "changes"
        / f"{snapshot_date.year:04d}"
        / f"{snapshot_date.month:02d}"
        / f"{snapshot_date.isoformat()}.csv"
    )
    return daily, changes


def build_matching_issue_path(data_root: Path, snapshot_date: date) -> Path:
    return (
        data_root
        / "matching_issues"
        / f"{snapshot_date.year:04d}"
        / f"{snapshot_date.month:02d}"
        / f"{snapshot_date.isoformat()}.csv"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("SHELTER_SOURCE_URL", DEFAULT_URL))
    parser.add_argument("--snapshot-date", default="", help="Logical observation date in YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--debug-dir", default="debug")
    parser.add_argument(
        "--reference-csv",
        default=os.getenv("SHELTER_REFERENCE_CSV", "reference/43000_1.csv"),
        help="施設属性を付与する参照CSV",
    )
    parser.add_argument("--minimum-rows", type=int, default=int(os.getenv("MINIMUM_ROWS", "100")))
    parser.add_argument(
        "--minimum-match-rate",
        type=float,
        default=float(os.getenv("MINIMUM_MATCH_RATE", "0.75")),
        help="参照CSVへの照合率の下限（0.0～1.0）",
    )
    parser.add_argument("--timeout-ms", type=int, default=90000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    retrieved_at = datetime.now(JST)
    snapshot_date = date.fromisoformat(args.snapshot_date) if args.snapshot_date else retrieved_at.date()
    data_root = Path(args.data_root)
    debug_dir = Path(args.debug_dir)
    daily_path, changes_path = build_paths(data_root, snapshot_date)
    matching_issue_path = build_matching_issue_path(data_root, snapshot_date)
    log_path = data_root / "logs" / "collection_log.csv"

    try:
        reference_matcher = ReferenceMatcher(Path(args.reference_csv))
        result = asyncio.run(collect_rendered_page(args.url, debug_dir, args.timeout_ms))
        normalized_rows = [
            row_to_normalized(
                raw_row,
                snapshot_date,
                retrieved_at,
                result.source_updated_at_text,
                args.url,
            )
            for raw_row in result.rows
        ]

        for row in normalized_rows:
            enrichment = reference_matcher.enrich(row)
            row.update(enrichment)
            row["shelter_id"] = tracking_id(row["web_shelter_id"], enrichment)
            row["record_hash"] = record_hash(row)

        validate_collection(normalized_rows, args.minimum_rows)

        reference_matched_count = sum(
            row.get("reference_match_status") == "matched" for row in normalized_rows
        )
        reference_matched_multiple_count = sum(
            row.get("reference_match_status") == "matched_multiple" for row in normalized_rows
        )
        reference_ambiguous_count = sum(
            row.get("reference_match_status") == "ambiguous" for row in normalized_rows
        )
        reference_unmatched_count = sum(
            row.get("reference_match_status") == "unmatched" for row in normalized_rows
        )
        reference_accepted_count = reference_matched_count + reference_matched_multiple_count
        reference_match_rate = (
            reference_accepted_count / len(normalized_rows) if normalized_rows else 0.0
        )
        matching_issues = [
            row
            for row in normalized_rows
            if row.get("reference_match_status") in {"ambiguous", "unmatched"}
        ]
        write_csv(matching_issue_path, matching_issues, MATCH_ISSUE_COLUMNS)
        write_csv(data_root / "latest_matching_issues.csv", matching_issues, MATCH_ISSUE_COLUMNS)
        if reference_match_rate < args.minimum_match_rate:
            raise RuntimeError(
                "参照CSVへの照合率が下限を下回りました: "
                f"{reference_match_rate:.1%} < {args.minimum_match_rate:.1%}。"
                f" 未一致・曖昧候補は {matching_issue_path} に保存しました。"
            )

        previous_path = previous_daily_file(data_root, daily_path)
        previous_rows = load_csv(previous_path) if previous_path else []
        previous_by_id = {row.get("shelter_id", ""): row for row in previous_rows}
        current_ids: set[str] = set()
        change_rows: list[dict[str, str]] = []

        for row in normalized_rows:
            shelter_id = row["shelter_id"]
            current_ids.add(shelter_id)
            previous = previous_by_id.get(shelter_id)
            change_type, details = compare_records(row, previous)
            row["change_type"] = change_type
            if previous:
                row["previous_opening_status"] = previous.get("opening_status", "")
                row["previous_congestion_status"] = previous.get("congestion_status", "")
                row["previous_evacuee_count"] = previous.get("evacuee_count", "")
            if change_type != "unchanged":
                changed = dict(row)
                changed["change_details"] = details
                change_rows.append(changed)

        # Track shelters removed from the published all-shelter listing.
        for shelter_id, previous in previous_by_id.items():
            if shelter_id in current_ids:
                continue
            removed = dict(previous)
            removed.update(
                {
                    "snapshot_date_jst": snapshot_date.isoformat(),
                    "retrieved_at_jst": retrieved_at.isoformat(timespec="seconds"),
                    "source_updated_at_text": result.source_updated_at_text,
                    "source_updated_at_jst": parse_source_updated_at(result.source_updated_at_text, retrieved_at),
                    "change_type": "removed_from_listing",
                    "previous_opening_status": previous.get("opening_status", ""),
                    "previous_congestion_status": previous.get("congestion_status", ""),
                    "previous_evacuee_count": previous.get("evacuee_count", ""),
                    "source_url": args.url,
                    "change_details": "前回スナップショットには存在したが今回の全避難所一覧に存在しない",
                }
            )
            change_rows.append(removed)

        normalized_rows.sort(
            key=lambda row: (
                row.get("municipality", ""),
                row.get("shelter_name", ""),
                row.get("address", ""),
            )
        )
        change_rows.sort(
            key=lambda row: (
                row.get("municipality", ""),
                row.get("shelter_name", ""),
                row.get("change_type", ""),
            )
        )

        write_csv(daily_path, normalized_rows, CSV_COLUMNS)
        write_csv(changes_path, change_rows, CHANGE_COLUMNS)
        write_csv(data_root / "latest.csv", normalized_rows, CSV_COLUMNS)
        write_csv(data_root / "latest_changes.csv", change_rows, CHANGE_COLUMNS)
        rebuild_all_snapshots(data_root)

        open_count = sum(row["is_open"] == "true" for row in normalized_rows)
        inactive_count = sum(row["is_open"] == "false" for row in normalized_rows)
        unknown_count = sum(row["is_open"] == "unknown" for row in normalized_rows)
        append_log(
            log_path,
            {
                "snapshot_date_jst": snapshot_date.isoformat(),
                "retrieved_at_jst": retrieved_at.isoformat(timespec="seconds"),
                "status": "success",
                "record_count": str(len(normalized_rows)),
                "open_count": str(open_count),
                "closed_or_inactive_count": str(inactive_count),
                "unknown_status_count": str(unknown_count),
                "changed_count": str(len(change_rows)),
                "reference_matched_count": str(reference_matched_count),
                "reference_matched_multiple_count": str(reference_matched_multiple_count),
                "reference_ambiguous_count": str(reference_ambiguous_count),
                "reference_unmatched_count": str(reference_unmatched_count),
                "reference_match_rate": f"{reference_match_rate:.6f}",
                "reference_source_sha256": reference_matcher.sha256,
                "source_updated_at_text": result.source_updated_at_text,
                "source_updated_at_jst": parse_source_updated_at(result.source_updated_at_text, retrieved_at),
                "source_url": args.url,
                "message": f"previous={previous_path or 'none'}; headers={result.headers}",
            },
        )
        print(
            json.dumps(
                {
                    "snapshot_date": snapshot_date.isoformat(),
                    "records": len(normalized_rows),
                    "open": open_count,
                    "closed_or_inactive": inactive_count,
                    "unknown": unknown_count,
                    "changes": len(change_rows),
                    "reference_matched": reference_matched_count,
                    "reference_matched_multiple": reference_matched_multiple_count,
                    "reference_ambiguous": reference_ambiguous_count,
                    "reference_unmatched": reference_unmatched_count,
                    "reference_match_rate": round(reference_match_rate, 6),
                    "matching_issues_csv": str(matching_issue_path),
                    "daily_csv": str(daily_path),
                    "changes_csv": str(changes_path),
                    "source_updated_at": result.source_updated_at_text,
                    "all_shelters_click_attempted": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        append_log(
            log_path,
            {
                "snapshot_date_jst": snapshot_date.isoformat(),
                "retrieved_at_jst": retrieved_at.isoformat(timespec="seconds"),
                "status": "failure",
                "record_count": "0",
                "open_count": "0",
                "closed_or_inactive_count": "0",
                "unknown_status_count": "0",
                "changed_count": "0",
                "reference_matched_count": "0",
                "reference_matched_multiple_count": "0",
                "reference_ambiguous_count": "0",
                "reference_unmatched_count": "0",
                "reference_match_rate": "",
                "reference_source_sha256": "",
                "source_updated_at_text": "",
                "source_updated_at_jst": "",
                "source_url": args.url,
                "message": f"{type(exc).__name__}: {exc}",
            },
        )
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
