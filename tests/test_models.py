from datetime import date, datetime, timezone

import pytest

from kickoff_desk.models import Match, filter_matches, parse_match


def fixture(**changes):
    raw = {
        "id": 42,
        "competition": {"code": "PL"},
        "homeTeam": {"id": 57, "name": "Arsenal"},
        "awayTeam": {"id": 73, "name": "Tottenham"},
        "utcDate": "2026-10-16T15:00:00Z",
        "status": "TIMED",
        "score": {"fullTime": {"home": 5, "away": 0}},
    }
    raw.update(changes)
    return raw


def test_korean_midnight_rollover_and_score_free_round_trip():
    match = parse_match(fixture())
    assert match.local_date == date(2026, 10, 17)
    assert match.kickoff_label == "00:00"
    assert match.is_time_confirmed
    stored = match.to_dict()
    assert "score" not in stored
    assert not hasattr(match, "score")
    assert set(stored) == {
        "id",
        "competition",
        "home_id",
        "home_name",
        "away_id",
        "away_name",
        "utc_date",
        "status",
    }
    assert Match.from_dict(stored) == match


def test_dates_with_explicit_offsets_are_normalized_to_utc():
    match = parse_match(fixture(utcDate="2026-10-17T00:00:00+09:00"))
    assert match.utc_date == datetime(2026, 10, 16, 15, tzinfo=timezone.utc)
    assert match.to_dict()["utc_date"] == "2026-10-16T15:00:00Z"


@pytest.mark.parametrize(
    "value", ["2026-10-16T15:00:00", "invalid", 12, {}, "2026-99-99T10:00:00Z"]
)
def test_malformed_or_naive_api_dates_are_rejected(value):
    with pytest.raises(ValueError):
        parse_match(fixture(utcDate=value))


@pytest.mark.parametrize(
    "status", ["SCHEDULED", "POSTPONED", "SUSPENDED", "CANCELED", "UNRECOGNIZED"]
)
def test_unconfirmed_status_never_presents_a_confirmed_kickoff(status):
    match = parse_match(fixture(status=status))
    assert not match.is_time_confirmed
    assert match.kickoff_label == "시간 미확정"


def test_unknown_teams_and_undated_matches_are_safe():
    match = parse_match(fixture(utcDate=None, homeTeam=None, awayTeam={"id": None, "name": None}))
    assert match.home_id is None
    assert match.away_id is None
    assert match.home_name == "홈 팀 미정"
    assert match.away_name == "원정 팀 미정"
    assert match.local_date is None
    assert match.kickoff_label == "일정 미정"
    assert Match.from_dict(match.to_dict()) == match


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": -1},
        {"id": "42"},
        {"competition": {"code": "BL1"}},
        {"competition": None},
        {"homeTeam": []},
        {"awayTeam": {"id": True}},
    ],
)
def test_invalid_api_match_fields_are_rejected(changes):
    with pytest.raises(ValueError):
        parse_match(fixture(**changes))


def test_finished_status_has_no_score_data_or_score_display():
    match = parse_match(fixture(status="FINISHED"))
    assert match.status_label == "경기 종료"
    assert "5" not in match.status_label
    assert "score" not in repr(match)


def test_filters_use_korean_date_competition_and_either_favorite_team():
    before_midnight = parse_match(fixture(id=1, utcDate="2026-10-16T14:59:00Z"))
    after_midnight = parse_match(fixture(id=2, utcDate="2026-10-16T15:00:00Z"))
    other_league = parse_match(
        fixture(id=3, competition={"code": "PD"}, homeTeam={"id": 81, "name": "Barcelona"})
    )
    undated = parse_match(fixture(id=4, utcDate=None))
    matches = [undated, after_midnight, other_league, before_midnight]
    assert filter_matches(matches, selected_date=date(2026, 10, 16)) == [before_midnight]
    assert filter_matches(matches, selected_date=date(2026, 10, 17), competitions={"PD"}) == [
        other_league
    ]
    assert filter_matches(matches, favorite_team_ids={81}) == [other_league]
    assert filter_matches(matches, favorite_team_ids={73}) == [
        before_midnight,
        after_midnight,
        other_league,
        undated,
    ]
    assert filter_matches(matches, favorite_team_ids=set()) == []
    assert filter_matches(matches, competitions=set()) == []
    assert filter_matches(matches)[-1] == undated


def test_direct_naive_match_is_rejected():
    with pytest.raises(ValueError):
        Match(1, "PL", None, "Home", None, "Away", datetime(2026, 10, 16), "TIMED")


@pytest.mark.parametrize("value", ["9999-12-31T20:00:00Z", "0001-01-01T00:00:00+09:00"])
def test_timezone_conversion_overflow_is_rejected_at_ingestion(value):
    with pytest.raises(ValueError):
        parse_match(fixture(utcDate=value))


def test_direct_match_rejects_date_that_cannot_be_displayed_in_seoul():
    with pytest.raises(ValueError):
        Match(
            1,
            "PL",
            None,
            "Home",
            None,
            "Away",
            datetime(9999, 12, 31, 20, tzinfo=timezone.utc),
            "TIMED",
        )
