"""Integrity guard for persisted municipal evacuee observations."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path


def remove_impossible_future_rows(
    path: Path,
    *,
    tolerance_minutes: int = 5,
) -> int:
    """Remove observations whose source time is later than their retrieval time.

    This repairs only objectively impossible rows. Historical values are not
    otherwise altered. The file is rewritten only when invalid rows exist.
    """
    if not path.exists() or path.stat().st_size == 0:
        return 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not fieldnames:
        return 0

    kept: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    tolerance = timedelta(minutes=tolerance_minutes)

    for row in rows:
        observed_text = (row.get("source_observed_at_jst") or "").strip()
        retrieved_text = (row.get("retrieved_at_jst") or "").strip()
        invalid = False
        if observed_text and retrieved_text:
            try:
                observed = datetime.fromisoformat(observed_text)
                retrieved = datetime.fromisoformat(retrieved_text)
                invalid = observed > retrieved + tolerance
            except ValueError:
                invalid = False
        if invalid:
            removed.append(row)
        else:
            kept.append(row)

    if not removed:
        return 0

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    print(
        "Removed impossible future municipal observations: "
        f"count={len(removed)} path={path}"
    )
    for row in removed[:20]:
        print(
            "  removed "
            f"municipality={row.get('municipality', '')} "
            f"observed={row.get('source_observed_at_jst', '')} "
            f"retrieved={row.get('retrieved_at_jst', '')} "
            f"shelter={row.get('source_shelter_name', '')}"
        )
    return len(removed)
