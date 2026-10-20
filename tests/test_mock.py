from datetime import date, timedelta

import pytest

from kickoff_desk.mock import MockClient
from kickoff_desk.models import filter_matches


def test_demo_is_deterministic_for_any_requested_range():
    client = MockClient()
    start, end = date(2030, 4, 1), date(2030, 4, 7)
    first = client.fetch_matches(start, end)
    assert first == client.fetch_matches(start, end)
    assert len(first) == 22
    assert len({match.id for match in first}) == len(first)
    assert {match.competition for match in first} == {"PL", "PD", "CL"}
    assert {match.status for match in first} >= {
        "TIMED",
        "SCHEDULED",
        "FINISHED",
        "POSTPONED",
        "IN_PLAY",
    }
    assert all(start <= match.utc_date.date() <= end for match in first if match.utc_date)
    assert sum(match.utc_date is None for match in first) == 1
    assert all("score" not in match.to_dict() for match in first)


def test_demo_exercises_korean_date_rollover():
    day = date(2030, 4, 1)
    matches = MockClient().fetch_matches(day, day)
    assert len(filter_matches(matches, selected_date=day)) == 1
    assert len(filter_matches(matches, selected_date=day + timedelta(days=1))) == 2


def test_overlapping_demo_ranges_retain_match_identifiers_and_states():
    client = MockClient()
    day = date(2030, 4, 2)
    narrow = client.fetch_matches(day, day)
    wide = client.fetch_matches(date(2030, 4, 1), date(2030, 4, 3))
    by_id = {match.id: match for match in wide}
    assert all(match == by_id[match.id] for match in narrow)


def test_demo_rejects_reversed_range():
    with pytest.raises(ValueError):
        MockClient().fetch_matches(date(2030, 4, 2), date(2030, 4, 1))
