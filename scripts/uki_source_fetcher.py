"""Resilient fetch wrapper for Uki City's evacuee-count page."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


def build_collect_uki(
    collector: Any,
    parse_html: Callable[[bytes, str], Any],
    *,
    attempts: int = 10,
    retry_interval_seconds: int = 30,
):
    """Build a collector-compatible Uki source function with integrity retries.

    The city page can be edited around the scheduled 08:00 publication. During
    that short transition the table body and total row may temporarily be out of
    sync. A parser integrity failure is therefore retried instead of immediately
    failing the GitHub Actions run. The final attempt still fails closed.
    """

    def collect_uki(debug_dir: Path):
        debug_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: list[str] = []

        for attempt in range(1, attempts + 1):
            try:
                page_data = collector.fetch_bytes(
                    collector.UKI_PAGE_URL,
                    timeout_seconds=45,
                    retries=2,
                )
                (debug_dir / f"uki_page_attempt_{attempt:02d}.html").write_bytes(page_data)
                snapshot = parse_html(page_data, collector.UKI_PAGE_URL)
                (debug_dir / "uki_page.html").write_bytes(page_data)
                (debug_dir / "uki_selected_source.txt").write_text(
                    f"attempt={attempt}\nurl={collector.UKI_PAGE_URL}\n",
                    encoding="utf-8",
                )
                return snapshot
            except Exception as exc:
                message = (
                    f"attempt={attempt}/{attempts}: "
                    f"{type(exc).__name__}: {exc}"
                )
                diagnostics.append(message)
                print(f"WARNING Uki source is not stable yet: {message}")
                if attempt < attempts:
                    time.sleep(retry_interval_seconds)

        (debug_dir / "uki_source_failure.txt").write_text(
            "\n".join(diagnostics) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "宇城市の避難者数ページを安定状態で取得できませんでした。"
            f" {attempts}回再試行済み。最後のエラー: {diagnostics[-1]}"
        )

    return collect_uki
