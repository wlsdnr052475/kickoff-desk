from datetime import date, datetime, timezone

import pytest

from kickoff_desk.models import Match, filter_matches, normalize_crest_url, parse_match


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
        "home_crest",
        "away_id",
        "away_name",
        "away_crest",
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


def test_api_team_crests_survive_score_free_cache_roundtrip():
    match = parse_match(
        fixture(
            homeTeam={
                "id": 57,
                "name": "Arsenal",
                "crest": "https://crests.football-data.org/57.png",
            },
            awayTeam={
                "id": 73,
                "name": "Tottenham",
                "crest": "https://crests.football-data.org/73.svg",
            },
        )
    )
    assert match.home_crest == "https://crests.football-data.org/57.png"
    assert match.away_crest == "https://crests.football-data.org/73.svg"
    assert Match.from_dict(match.to_dict()) == match
    assert "score" not in match.to_dict()


def test_missing_crest_fields_in_existing_cache_remain_compatible():
    stored = parse_match(fixture()).to_dict()
    del stored["home_crest"]
    del stored["away_crest"]
    restored = Match.from_dict(stored)
    assert restored.home_crest is None
    assert restored.away_crest is None
    assert restored.id == stored["id"]
    assert restored.kickoff_label == "00:00"


def test_existing_eight_positional_match_arguments_remain_compatible():
    match = Match(1, "PL", 57, "Arsenal", 73, "Tottenham", None, "SCHEDULED")
    assert match.home_crest is None
    assert match.away_crest is None


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        57,
        {},
        "http://crests.football-data.org/57.png",
        "https://example.org/57.png",
        "https://crests.football-data.org.example.org/57.png",
        "https://secret@crests.football-data.org/57.png",
        "https://user:secret@crests.football-data.org/57.png",
        "https://crests.football-data.org:8443/57.png",
        "https://crests.football-data.org:/57.png",
        "https://crests.football-data.org/57.png?token=secret",
        "https://crests.football-data.org/57.png?",
        "https://crests.football-data.org/57.png#secret",
        "https://crests.football-data.org/57.png#",
        "https://crests.football-data.org/PL.png",
        "https://crests.football-data.org/57.jpg",
        "https://crests.football-data.org/57.PNG",
        "https://crests.football-data.org/0.png",
        "https://crests.football-data.org/../57.png",
        "https://crests.football-data.org/%35%37.png",
        "https://crests.football-data.org//57.png",
        "https://crests.football-data.org/５７.png",
        "https://crests.football-data.org\\@example.org/57.png",
        " https://crests.football-data.org/57.png",
        "https://crests.football-data.org/57.png\n",
        "https://crests.foot\tball-data.org/57.png",
        "https://[invalid/57.png",
        "file:///57.png",
        "data:image/png;base64,secret",
    ],
)
def test_unsupported_crest_urls_are_ignored_without_losing_the_match(url):
    assert normalize_crest_url(url) is None
    match = parse_match(fixture(homeTeam={"id": 57, "name": "Arsenal", "crest": url}))
    assert match.id == 42
    assert match.home_name == "Arsenal"
    assert match.home_crest is None
    assert match.to_dict()["home_crest"] is None


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://crests.football-data.org/57.png", "https://crests.football-data.org/57.png"),
        ("https://crests.football-data.org/73.svg", "https://crests.football-data.org/73.svg"),
        ("https://crests.football-data.org:443/81.svg", "https://crests.football-data.org/81.svg"),
        ("HTTPS://CRESTS.FOOTBALL-DATA.ORG/86.png", "https://crests.football-data.org/86.png"),
    ],
)
def test_official_numeric_png_and_svg_urls_are_normalized(url, expected):
    assert normalize_crest_url(url) == expected


def test_direct_match_and_existing_cache_discard_unsafe_crest_values():
    match = Match(
        1,
        "PL",
        57,
        "Arsenal",
        73,
        "Tottenham",
        None,
        "SCHEDULED",
        home_crest="https://crests.football-data.org/57.png?token=secret",
        away_crest="https://example.org/73.svg",
    )
    assert match.home_crest is None
    assert match.away_crest is None
    stored = match.to_dict()
    stored["home_crest"] = "https://user:secret@crests.football-data.org/57.png"
    assert Match.from_dict(stored).home_crest is None
