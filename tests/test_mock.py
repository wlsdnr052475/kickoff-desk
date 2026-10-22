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


def test_demo_does_not_supply_remote_crest_urls():
    matches = MockClient().fetch_matches(date(2030, 4, 1), date(2030, 4, 1))
    assert all(match.home_crest is None and match.away_crest is None for match in matches)


def test_demo_catalog_has_searchable_teams_without_any_selected_month_fixture():
    client = MockClient()
    teams = client.fetch_teams()
    matches = client.fetch_matches(date(2030, 4, 1), date(2030, 4, 1))
    fixture_ids = {identifier for match in matches for identifier in (match.home_id, match.away_id)}
    assert len(teams) == 15
    assert len({team.id for team in teams}) == len(teams)
    assert any(team.id not in fixture_ids for team in teams)
    assert any(team.name == "Liverpool FC" for team in teams)
    assert all(team.crest is None for team in teams)
    assert teams == client.fetch_teams()
    assert {code for team in teams for code in team.competitions} == {"PL", "PD", "CL"}


def test_demo_catalog_uses_verified_ids_so_favorites_survive_mode_changes():
    by_name = {team.name: team.id for team in MockClient().fetch_teams()}
    assert by_name["Chelsea FC"] == 61
    assert by_name["Liverpool FC"] == 64
    assert by_name["Manchester United FC"] == 66
    assert by_name["Club Atlético de Madrid"] == 78
    assert by_name["Real Betis Balompié"] == 90
    assert by_name["Athletic Club"] == 77
    assert by_name["Paris Saint-Germain FC"] == 524
    assert by_name["FC Internazionale Milano"] == 108
    assert by_name["Borussia Dortmund"] == 4
