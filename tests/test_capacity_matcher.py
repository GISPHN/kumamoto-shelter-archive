import csv

from scripts.capacity_matcher import CAPACITY_INPUT_COLUMNS, CapacityMatcher


def write_capacity_master(path):
    rows = [
        {
            "portal_shelter_id": "00000571",
            "municipality_code": "434230",
            "municipality": "南小国町",
            "shelter_name": "元気プラザ中原",
            "address": "熊本県南小国町大字中原2489-3",
            "portal_latitude": "33.0",
            "portal_longitude": "131.0",
            "portal_capacity_persons": "188",
        },
        {
            "portal_shelter_id": "00000572",
            "municipality_code": "434230",
            "municipality": "南小国町",
            "shelter_name": "湯夢プラザ",
            "address": "熊本県南小国町大字中原4172-1",
            "portal_latitude": "33.001",
            "portal_longitude": "131.001",
            "portal_capacity_persons": "50",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CAPACITY_INPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_exact_name_wins_over_stale_portal_id_and_shared_address(tmp_path):
    master_path = tmp_path / "portal_shelter_capacity.csv"
    write_capacity_master(master_path)
    matcher = CapacityMatcher(master_path)

    result = matcher.match(
        {
            "portal_shelter_id": "00000571",
            "municipality": "南小国町",
            "shelter_name": "湯夢プラザ",
            "address": "熊本県南小国町大字中原2489-3",
        }
    )

    assert result.status == "matched"
    assert result.row["portal_shelter_id"] == "00000572"
    assert result.row["portal_capacity_persons"] == "50"


def test_unique_address_remains_fallback_for_name_variation(tmp_path):
    master_path = tmp_path / "portal_shelter_capacity.csv"
    write_capacity_master(master_path)
    matcher = CapacityMatcher(master_path)

    result = matcher.match(
        {
            "municipality": "南小国町",
            "shelter_name": "湯夢プラザ会議室",
            "address": "熊本県南小国町大字中原4172-1",
        }
    )

    assert result.status == "matched"
    assert result.method == "exact_municipality_address"
    assert result.row["portal_shelter_id"] == "00000572"
    assert result.row["portal_capacity_persons"] == "50"
