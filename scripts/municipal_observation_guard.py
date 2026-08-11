"""Integrity guard for persisted municipal evacuee observations."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path


# The only specifically verified bad observation created by the parser bug was
# an impossible future timestamp on 2026-08-11.  The 2026-08-10 22:00 snapshot
# is retained because the municipal server returned it consistently on repeated
# fetches and it is not temporally impossible.
_KNOWN_INVALID = {
    (
        "宇城市",
        "2026-08-11T22:00+09:00",
        "28fd03ac17baed625cfa1819a73e40a274715f6a04cdb9577e1adaecb09cb072",
    ),
}


def remove_impossible_future_rows(
    path: Path,
    *,
    tolerance_minutes: int = 5,
) -> int:
    """Remove objectively impossible or specifically verified invalid rows."""
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
        municipality = (row.get("municipality") or "").strip()
        observed_text = (row.get("source_observed_at_jst") or "").strip()
        retrieved_text = (row.get("retrieved_at_jst") or "").strip()
        normalized_hash = (row.get("normalized_sha256") or "").strip()

        known_invalid = (municipality, observed_text, normalized_hash) in _KNOWN_INVALID
        impossible_future = False
        if observed_text and retrieved_text:
            try:
                observed = datetime.fromisoformat(observed_text)
                retrieved = datetime.fromisoformat(retrieved_text)
                impossible_future = observed > retrieved + tolerance
            except ValueError:
                impossible_future = False

        if known_invalid or impossible_future:
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
        "Removed invalid municipal observations: "
        f"count={len(removed)} path={path}"
    )
    for row in removed[:30]:
        print(
            "  removed "
            f"municipality={row.get('municipality', '')} "
            f"observed={row.get('source_observed_at_jst', '')} "
            f"retrieved={row.get('retrieved_at_jst', '')} "
            f"hash={(row.get('normalized_sha256') or '')[:12]} "
            f"shelter={row.get('source_shelter_name', '')}"
        )
    return len(removed)
