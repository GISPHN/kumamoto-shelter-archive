from scripts.collect_capacity import capacity_master_stats


def test_capacity_master_stats_include_preserved_positive_capacity():
    rows = [
        {
            "municipality_code": "43201",
            "portal_capacity_persons": "100",
            "capacity_parse_status": "parsed",
        },
        {
            "municipality_code": "43201",
            "portal_capacity_persons": "200",
            "capacity_parse_status": "preserved_previous_parsed",
        },
        {
            "municipality_code": "43202",
            "portal_capacity_persons": "",
            "capacity_parse_status": "missing_zero",
        },
        {
            "municipality_code": "43202",
            "portal_capacity_persons": "",
            "capacity_parse_status": "invalid",
        },
    ]

    assert capacity_master_stats(rows) == {
        "record_count": 4,
        "parsed_capacity_count": 2,
        "preserved_previous_parsed_count": 1,
        "missing_zero_capacity_count": 1,
        "missing_capacity_count": 0,
        "invalid_capacity_count": 1,
        "municipality_count": 2,
    }
