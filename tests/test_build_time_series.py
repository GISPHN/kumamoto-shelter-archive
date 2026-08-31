import csv

from scripts.build_time_series import merge_existing_timeseries


FIXED_COLUMNS = [
    "shelter_id",
    "portal_shelter_id",
    "portal_capacity_persons",
    "capacity_match_status",
    "capacity_match_method",
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_method",
    "coordinate_status",
]


def write_existing(path, row):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIXED_COLUMNS + ["2026-08-30"])
        writer.writeheader()
        writer.writerow(row)


def test_repairs_enrichment_metadata_without_changing_history(tmp_path):
    path = tmp_path / "open_status_by_date.csv"
    write_existing(
        path,
        {
            "shelter_id": "web:test",
            "portal_shelter_id": "stable-id",
            "capacity_match_status": "unmatched",
            "capacity_match_method": "no_candidate",
            "coordinate_status": "missing",
            "2026-08-30": "1",
        },
    )
    generated = [{
        "shelter_id": "web:test",
        "portal_shelter_id": "replacement-id",
        "portal_capacity_persons": "334",
        "capacity_match_status": "matched",
        "capacity_match_method": "exact_municipality_name_address",
        "latitude": "32.63585132615984",
        "longitude": "130.7519531247817",
        "coordinate_source": "kumamoto_portal",
        "coordinate_method": "portal_latitude_longitude",
        "coordinate_status": "complete",
        "2026-08-30": "0",
        "2026-08-31": "1",
    }]

    rows, dates = merge_existing_timeseries(
        path,
        generated,
        FIXED_COLUMNS,
        ["2026-08-30", "2026-08-31"],
        "0",
    )

    assert dates == ["2026-08-30", "2026-08-31"]
    assert rows[0]["portal_shelter_id"] == "stable-id"
    assert rows[0]["capacity_match_status"] == "matched"
    assert rows[0]["portal_capacity_persons"] == "334"
    assert rows[0]["coordinate_status"] == "complete"
    assert rows[0]["latitude"] == "32.63585132615984"
    assert rows[0]["2026-08-30"] == "1"
    assert rows[0]["2026-08-31"] == "1"


def test_does_not_degrade_verified_enrichment(tmp_path):
    path = tmp_path / "open_status_by_date.csv"
    write_existing(
        path,
        {
            "shelter_id": "web:test",
            "portal_shelter_id": "stable-id",
            "portal_capacity_persons": "334",
            "capacity_match_status": "matched",
            "capacity_match_method": "portal_shelter_id",
            "latitude": "32.6",
            "longitude": "130.7",
            "coordinate_source": "kumamoto_portal",
            "coordinate_method": "portal_latitude_longitude",
            "coordinate_status": "complete",
            "2026-08-30": "1",
        },
    )
    generated = [{
        "shelter_id": "web:test",
        "portal_shelter_id": "replacement-id",
        "capacity_match_status": "unmatched",
        "capacity_match_method": "no_candidate",
        "coordinate_status": "missing",
        "2026-08-30": "0",
        "2026-08-31": "0",
    }]

    rows, _ = merge_existing_timeseries(
        path,
        generated,
        FIXED_COLUMNS,
        ["2026-08-30", "2026-08-31"],
        "0",
    )

    assert rows[0]["portal_shelter_id"] == "stable-id"
    assert rows[0]["capacity_match_status"] == "matched"
    assert rows[0]["coordinate_status"] == "complete"
    assert rows[0]["latitude"] == "32.6"
    assert rows[0]["2026-08-30"] == "1"

