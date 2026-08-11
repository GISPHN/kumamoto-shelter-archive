"""Resilient fetch wrapper for Uki City's evacuee-count page."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def build_collect_uki(
    collector: Any,
    parse_html: Callable[[bytes, str], Any],
    *,
    attempts: int = 10,
    retry_interval_seconds: int = 30,
    stability_interval_seconds: int = 10,
):
    """Build a collector-compatible Uki source function with integrity retries.

    A candidate is accepted only when:
      * its observation time is not in the future; and
      * the same observation timestamp and normalized table hash are obtained
        twice consecutively.

    This prevents a CMS page being accepted while its heading, rows, and total
    are being updated in separate steps.
    """

    def collect_uki(debug_dir: Path):
        debug_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: list[str] = []
        pending_key: tuple[str, str] | None = None

        for attempt in range(1, attempts + 1):
            try:
                page_data = collector.fetch_bytes(
                    collector.UKI_PAGE_URL,
                    timeout_seconds=45,
                    retries=2,
                )
                (debug_dir / f"uki_page_attempt_{attempt:02d}.html").write_bytes(page_data)
                snapshot = parse_html(page_data, collector.UKI_PAGE_URL)

                observed_at = datetime.fromisoformat(snapshot.observed_at_jst)
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=JST)
                now = datetime.now(JST)
                if observed_at > now + timedelta(minutes=5):
                    raise RuntimeError(
                        "宇城市の観測時刻が現在時刻より未来です: "
                        f"observed={snapshot.observed_at_jst}, now={now.isoformat(timespec='seconds')}"
                    )

                key = (snapshot.observed_at_jst, snapshot.normalized_sha256)
                if pending_key == key:
                    (debug_dir / "uki_page.html").write_bytes(page_data)
                    (debug_dir / "uki_selected_source.txt").write_text(
                        "\n".join(
                            [
                                f"attempt={attempt}",
                                f"stable_twice=true",
                                f"observed_at_jst={snapshot.observed_at_jst}",
                                f"normalized_sha256={snapshot.normalized_sha256}",
                                f"url={collector.UKI_PAGE_URL}",
                            ]
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    return snapshot

                pending_key = key
                message = (
                    f"attempt={attempt}/{attempts}: valid candidate waiting for "
                    f"second identical fetch observed={snapshot.observed_at_jst} "
                    f"hash={snapshot.normalized_sha256[:12]}"
                )
                diagnostics.append(message)
                print(f"INFO Uki source stability check: {message}")
                if attempt < attempts:
                    time.sleep(stability_interval_seconds)
                continue

            except Exception as exc:
                pending_key = None
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
            f" {attempts}回確認済み。最後の状態: {diagnostics[-1]}"
        )

    return collect_uki
