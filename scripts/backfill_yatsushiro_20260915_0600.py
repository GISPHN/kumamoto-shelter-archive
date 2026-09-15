"""One-time recovery of the Yatsushiro 2026-09-15 06:00 observation."""

from __future__ import annotations

import csv
from pathlib import Path

import run_municipal_evacuee_collection as runner


OBSERVED_AT = "2026-09-15T06:00+09:00"
RETRIEVED_AT = "2026-09-15T10:37:45+09:00"
PAGE_URL = "https://www.city.yatsushiro.lg.jp/kiji00326798/index.html"
DOCUMENT_URL = (
    "https://www.city.yatsushiro.lg.jp/"
    "kiji00326798/3_26798_159907_up_ubnxqzwu.pdf"
)
PDF_SHA256 = "64c21f3788c18b51227d1f6442c11c4fbc6df57ba126a9a2b56886de72477edc"
EXPECTED_NORMALIZED_SHA256 = (
    "1fcdc94910747151c1f6206cb29a9bca6df9feffe908893fb052127738c24935"
)


def main() -> int:
    collector = runner.collector
    observations_path = Path("data/municipal_evacuees/all_observations.csv")
    columns, observations = collector.read_csv_with_columns(observations_path)
    if any(
        row.get("municipality") == "八代市"
        and row.get("source_observed_at_jst") == OBSERVED_AT
        for row in observations
    ):
        print("Yatsushiro 2026-09-15 06:00 observation is already present.")
        return 0

    source_path = Path("reference/backfill/yatsushiro_20260915_0600.csv")
    with source_path.open(encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    records = [
        collector.SourceRecord(
            "八代市",
            row["source_shelter_name"],
            "",
            int(row["evacuee_count"]),
        )
        for row in source_rows
    ]
    if len(records) != 14 or sum(record.evacuee_count for record in records) != 833:
        raise RuntimeError("Recovered Yatsushiro rows do not match the official total.")

    normalized_sha256 = collector.normalized_snapshot_hash(records)
    if normalized_sha256 != EXPECTED_NORMALIZED_SHA256:
        raise RuntimeError("Recovered Yatsushiro normalized hash does not match the PDF.")
    snapshot = collector.SourceSnapshot(
        municipality="八代市",
        observed_at_jst=OBSERVED_AT,
        source_format="pdf",
        page_url=PAGE_URL,
        document_url=DOCUMENT_URL,
        raw_sha256=PDF_SHA256,
        normalized_sha256=normalized_sha256,
        records=records,
        published_total=833,
    )

    status_columns, status_rows = collector.read_csv_with_columns(
        Path("data/status_by_date.csv")
    )
    aliases = collector.load_aliases(
        Path("reference/municipal_evacuee_shelter_aliases.csv")
    )
    new_rows, issues = collector.build_observation_rows(
        snapshot, status_rows, aliases, RETRIEVED_AT, 1
    )
    if issues or len(new_rows) != 14:
        raise RuntimeError(f"Recovered Yatsushiro rows did not match: {issues}")

    wide_columns, wide_rows = collector.read_csv_with_columns(
        Path("data/evacuee_count_by_date.csv")
    )
    combined_rows = observations + [
        {column: collector.clean_text(row.get(column)) for column in columns}
        for row in new_rows
    ]
    rebuilt_columns, rebuilt_rows = collector.build_wide_rows(
        status_columns, status_rows, combined_rows
    )
    if rebuilt_columns != wide_columns or rebuilt_rows != wide_rows:
        raise RuntimeError("The earlier observation would alter the later daily values.")

    collector.write_csv(observations_path, combined_rows, columns)
    print("Recovered 14 Yatsushiro rows; official and calculated totals are 833.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
