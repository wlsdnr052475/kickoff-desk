import json
from datetime import datetime, timezone

import pytest

from kickoff_desk.api import NetworkError
from kickoff_desk.catalog import Team, TeamCatalog, merge_teams, parse_team


class Provider:
    def __init__(self, teams=None, error=None):
        self.teams = teams if teams is not None else [Team(57, "Arsenal FC", competitions=("PL",))]
        self.error = error
        self.calls = 0

    def fetch_teams(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.teams


def test_team_parsing_preserves_search_names_and_only_allowlisted_fields():
    team = parse_team(
        {
            "id": 57,
            "name": "Arsenal FC",
            "shortName": "Arsenal",
            "crest": "https://crests.football-data.org/57.png",
            "score": {"home": 1},
            "api_key": "test-only-not-a-real-key",
            "squad": [{"name": "Unused player"}],
        },
        "PL",
    )
    assert team.name == "Arsenal FC"
    assert team.short_name == "Arsenal"
    assert team.competitions == ("PL",)
    assert set(team.to_dict()) == {"id", "name", "short_name", "crest", "competitions"}
    assert "test-only-not-a-real-key" not in json.dumps(team.to_dict())
    assert Team.from_dict(team.to_dict()) == team


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"id": True, "name": "Arsenal"},
        {"id": 57, "name": ""},
        {"id": 57, "name": 5},
        {"id": 57, "name": "Arsenal", "shortName": []},
        {"id": 57, "name": "Arsenal", "shortName": False},
    ],
)
def test_malformed_team_responses_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_team(raw, "PL")


def test_missing_optional_metadata_and_bad_crest_do_not_discard_team():
    team = parse_team(
        {
            "id": 57,
            "name": "Arsenal FC",
            "shortName": None,
            "crest": "https://crests.football-data.org/57.png?token=unsafe",
        },
        "PL",
    )
    assert team.short_name == ""
    assert team.crest is None
    assert team.id == 57


def test_duplicate_team_ids_merge_membership_and_fill_missing_metadata():
    merged = merge_teams(
        [
            Team(57, "Arsenal FC", competitions=("PL",)),
            Team(
                57,
                "Another provider spelling",
                "Arsenal",
                "https://crests.football-data.org/57.png",
                ("CL", "PL"),
            ),
            Team(81, "FC Barcelona", competitions=("PD", "CL")),
        ]
    )
    assert len(merged) == 2
    assert merged[0].name == "Arsenal FC"
    assert merged[0].short_name == "Arsenal"
    assert merged[0].competitions == ("PL", "CL")
    assert merged[0].crest == "https://crests.football-data.org/57.png"


def test_catalog_uses_cache_until_refresh_and_preserves_fetch_timestamp(tmp_path):
    provider = Provider()
    catalog = TeamCatalog(provider, tmp_path)
    fetched = catalog.load()
    assert fetched.source == "live"
    assert not fetched.stale
    assert fetched.fetched_at.tzinfo is not None
    assert catalog.path == tmp_path / "cache" / "live" / "teams.json"
    cached = catalog.load()
    assert cached.source == "cache"
    assert cached.stale
    assert cached.fetched_at == fetched.fetched_at
    assert cached.teams == fetched.teams
    assert provider.calls == 1
    provider.teams = [Team(81, "FC Barcelona", competitions=("PD",))]
    refreshed = catalog.load(refresh=True)
    assert [team.id for team in refreshed.teams] == [81]
    assert provider.calls == 2


def test_offline_catalog_survives_key_removal_and_failed_refresh(tmp_path):
    fetched = TeamCatalog(Provider(), tmp_path).load()
    path = tmp_path / "cache" / "live" / "teams.json"
    before = path.read_bytes()
    offline = TeamCatalog(None, tmp_path).load(refresh=True)
    assert offline.source == "cache"
    assert offline.teams == fetched.teams
    assert offline.fetched_at == fetched.fetched_at
    failed = TeamCatalog(Provider(error=NetworkError("연결 오류")), tmp_path).load(refresh=True)
    assert failed.teams == fetched.teams
    assert failed.stale
    assert "연결 오류" in failed.warning
    assert path.read_bytes() == before


def test_mock_and_live_catalogues_never_share_cache(tmp_path):
    TeamCatalog(Provider([Team(57, "Demo name", competitions=("PL",))]), tmp_path, "mock").load()
    live = TeamCatalog(None, tmp_path, "live").load()
    assert live.source == "empty"
    assert live.teams == []
    mock = TeamCatalog(None, tmp_path, "mock").load()
    assert mock.source == "cache"
    assert mock.teams[0].name == "Demo name"


def test_no_provider_or_cache_is_an_explicit_empty_result(tmp_path):
    result = TeamCatalog(None, tmp_path).load()
    assert result.source == "empty"
    assert result.stale
    assert result.teams == []
    assert result.fetched_at is None
    assert result.warning
    assert not (tmp_path / "cache").exists()


def test_corrupted_cache_recovers_only_after_successful_fetch(tmp_path):
    catalog = TeamCatalog(None, tmp_path)
    catalog.path.parent.mkdir(parents=True)
    catalog.path.write_text("broken json", encoding="utf-8")
    result = catalog.load()
    assert result.source == "empty"
    assert "읽지 못했습니다" in result.warning
    assert catalog.path.read_text() == "broken json"
    recovered = TeamCatalog(Provider(), tmp_path).load()
    assert recovered.source == "live"
    assert json.loads(catalog.path.read_text())["teams"][0]["id"] == 57


def test_failed_atomic_save_leaves_prior_catalogue_untouched(tmp_path, monkeypatch):
    catalog = TeamCatalog(Provider(), tmp_path)
    catalog.load()
    previous = catalog.path.read_bytes()
    catalog.provider = Provider([Team(81, "FC Barcelona", competitions=("PD",))])

    def fail_replace(*args):
        raise OSError("unsafe system detail")

    monkeypatch.setattr("kickoff_desk.catalog.os.replace", fail_replace)
    result = catalog.load(refresh=True)
    assert result.source == "live"
    assert result.teams[0].id == 81
    assert "저장하지 못했습니다" in result.warning
    assert "unsafe" not in result.warning
    assert catalog.path.read_bytes() == previous
    assert list(catalog.path.parent.glob("*.tmp")) == []


def test_cache_has_only_public_catalog_fields_and_rejects_wrong_mode(tmp_path):
    catalog = TeamCatalog(Provider(), tmp_path)
    catalog.load()
    raw = json.loads(catalog.path.read_text())
    assert set(raw) == {"version", "mode", "fetched_at", "teams"}
    assert set(raw["teams"][0]) == {"id", "name", "short_name", "crest", "competitions"}
    raw["mode"] = "mock"
    catalog.path.write_text(json.dumps(raw))
    assert TeamCatalog(None, tmp_path).load().source == "empty"


@pytest.mark.parametrize("timestamp", ["2026-10-22T10:00:00", "9999-12-31T23:00:00+00:00"])
def test_invalid_cached_timestamps_are_handled_safely(tmp_path, timestamp):
    catalog = TeamCatalog(Provider(), tmp_path)
    catalog.load()
    raw = json.loads(catalog.path.read_text())
    raw["fetched_at"] = timestamp
    catalog.path.write_text(json.dumps(raw))
    assert TeamCatalog(None, tmp_path).load().source == "empty"


def test_empty_successful_catalogue_is_distinct_from_no_cached_data(tmp_path):
    result = TeamCatalog(Provider([]), tmp_path).load()
    assert result.source == "live"
    assert result.teams == []
    assert isinstance(result.fetched_at, datetime)
    assert result.fetched_at.tzinfo == timezone.utc
    assert TeamCatalog(None, tmp_path).load().source == "cache"


def test_invalid_provider_result_does_not_overwrite_last_success(tmp_path):
    catalog = TeamCatalog(Provider(), tmp_path)
    original = catalog.load()
    catalog.provider = Provider([{"id": 57, "name": "Invalid provider object"}])
    result = catalog.load(refresh=True)
    assert result.source == "cache"
    assert result.teams == original.teams


def test_catalog_rejects_unrecognized_mode(tmp_path):
    with pytest.raises(ValueError):
        TeamCatalog(None, tmp_path, "../live")
