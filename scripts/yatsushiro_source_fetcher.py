"""Robust discovery of Yatsushiro City's latest shelter PDF.

The city page can be edited around publication time.  During that window the
article may already show a new observation time while the downloadable document
link is temporarily absent or its link label changes.  The collector therefore
must not depend on a specific anchor label.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup


def _clean(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _candidate_urls(page_html: str, page_url: str) -> list[tuple[int, str, str]]:
    """Return ranked document candidates from anchors and embedded elements."""
    soup = BeautifulSoup(page_html, "html.parser")
    found: dict[str, tuple[int, str]] = {}

    def add(raw_url: object, label: object, base_score: int = 0) -> None:
        raw = _clean(raw_url)
        if not raw or raw.startswith(("javascript:", "mailto:", "#")):
            return
        absolute = urllib.parse.urljoin(page_url, raw)
        parsed = urllib.parse.urlparse(absolute)
        path_lower = parsed.path.lower()
        label_text = _clean(label)
        haystack = f"{label_text} {absolute}".lower()

        score = base_score
        if path_lower.endswith(".pdf"):
            score += 100
        elif ".pdf" in haystack:
            score += 80
        else:
            return
        if "避難所" in label_text:
            score += 25
        if "開設" in label_text:
            score += 20
        if "26798" in absolute:
            score += 8
        if "_up_" in absolute:
            score += 5

        previous = found.get(absolute)
        if previous is None or score > previous[0]:
            found[absolute] = (score, label_text)

    for element in soup.find_all(["a", "iframe", "embed", "object"]):
        if element.name == "object":
            raw_url = element.get("data")
        else:
            raw_url = element.get("href") or element.get("src")
        label = element.get_text(" ") or element.get("title") or element.get("aria-label") or ""
        add(raw_url, label, 5 if element.name == "a" else 0)

    # Some municipal CMS revisions temporarily render the URL in attributes or
    # script-generated markup that BeautifulSoup does not expose as a normal
    # anchor.  A conservative PDF-only regex provides a second discovery path.
    for match in re.finditer(r"(?i)(?:href|src|data)\s*=\s*[\"']([^\"']+?\.pdf(?:\?[^\"']*)?)[\"']", page_html):
        add(match.group(1), "regex_pdf_candidate")

    return sorted(
        ((score, url, label) for url, (score, label) in found.items()),
        key=lambda item: (-item[0], item[1]),
    )


def build_collect_yatsushiro(
    collector: Any,
    parse_pdf: Callable[[bytes, str, str], Any],
    *,
    discovery_attempts: int = 12,
    retry_interval_seconds: int = 45,
):
    """Build a collector-compatible Yatsushiro source function.

    Each attempt reloads the article with no-cache headers through the parent
    collector's fetch function.  Candidate URLs are accepted only when the
    downloaded bytes start with the PDF signature, preventing HTML error pages
    or stale redirect targets from being parsed as a document.
    """

    def collect_yatsushiro(debug_dir: Path):
        debug_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: list[str] = []

        for attempt in range(1, discovery_attempts + 1):
            page_data = collector.fetch_bytes(
                collector.YATSUSHIRO_PAGE_URL,
                timeout_seconds=45,
                retries=2,
            )
            page_html = collector.decode_html(page_data)
            (debug_dir / f"yatsushiro_page_attempt_{attempt:02d}.html").write_bytes(page_data)
            candidates = _candidate_urls(page_html, collector.YATSUSHIRO_PAGE_URL)

            if not candidates:
                page_text = collector.clean_text(
                    BeautifulSoup(page_html, "html.parser").get_text(" ")
                )
                excerpt = page_text[:600]
                diagnostics.append(
                    f"attempt={attempt}: no PDF candidate; page_excerpt={excerpt!r}"
                )
                print(
                    "WARNING Yatsushiro PDF link not yet available: "
                    f"attempt {attempt}/{discovery_attempts}"
                )
            else:
                candidate_errors: list[str] = []
                for score, document_url, label in candidates:
                    try:
                        pdf_data = collector.fetch_bytes(
                            document_url,
                            timeout_seconds=60,
                            retries=2,
                        )
                    except Exception as exc:  # network diagnostics are retained
                        candidate_errors.append(
                            f"{document_url}: fetch {type(exc).__name__}: {exc}"
                        )
                        continue

                    if not pdf_data.startswith(b"%PDF"):
                        candidate_errors.append(
                            f"{document_url}: response is not a PDF; prefix={pdf_data[:20]!r}"
                        )
                        continue

                    (debug_dir / "yatsushiro_page.html").write_bytes(page_data)
                    (debug_dir / "yatsushiro_latest.pdf").write_bytes(pdf_data)
                    (debug_dir / "yatsushiro_selected_source.txt").write_text(
                        "\n".join(
                            [
                                f"attempt={attempt}",
                                f"score={score}",
                                f"label={label}",
                                f"url={document_url}",
                            ]
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    return parse_pdf(
                        pdf_data,
                        document_url,
                        collector.YATSUSHIRO_PAGE_URL,
                    )

                diagnostics.append(
                    f"attempt={attempt}: candidates={len(candidates)}; errors={candidate_errors}"
                )
                print(
                    "WARNING Yatsushiro PDF candidates were not usable: "
                    f"attempt {attempt}/{discovery_attempts}; {candidate_errors}"
                )

            if attempt < discovery_attempts:
                time.sleep(retry_interval_seconds)

        (debug_dir / "yatsushiro_source_discovery_failure.txt").write_text(
            "\n".join(diagnostics) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "八代市ページから有効な避難所PDFを取得できませんでした。"
            f" {discovery_attempts}回再試行済み。"
        )

    return collect_yatsushiro
