from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from kickoff_desk.api import NetworkError
from kickoff_desk.cache import CacheError, CacheStore, Snapshot
from kickoff_desk.models import Match
from kickoff_desk.service import ScheduleService, utc_query_dates


def match(identifier=1, utc_date="2026-10-15T15:00:00+00:00"):
    return Match(
        identifier,
        "PL",
        57,
        "Arsenal",
        61,
        "Chelsea",
        datetime.fromisoformat(utc_date) if utc_date else None,
        "TIMED",
    )


def test_cache_round_trip_and_mode_isolation(tmp_path):
    live = CacheStore(tmp_path)
    snapshot = Snapshot(2026, 10, datetime.now(timezone.utc), [match()])
    live.save(snapshot)
    assert live.load(2026, 10) == snapshot
    assert CacheStore(tmp_path, "mock").load(2026, 10) is None
    text = (tmp_path / "cache/live/2026-10.json").read_text()
    assert "score" not in text and "token" not in text


def test_corrupt_cache_has_safe_error(tmp_path):
    cache = CacheStore(tmp_path)
    cache.directory.mkdir(parents=True)
    (cache.directory / "2026-10.json").write_text("{incomplete")
    with pytest.raises(CacheError, match="새로고침"):
        cache.load(2026, 10)


def test_failed_atomic_save_preserves_previous_cache(tmp_path, monkeypatch):
    cache = CacheStore(tmp_path)
    old = Snapshot(2026, 10, datetime.now(timezone.utc), [match()])
    cache.save(old)
    monkeypatch.setattr("kickoff_desk.cache.os.replace", Mock(side_effect=OSError("private")))
    with pytest.raises(CacheError):
        cache.save(Snapshot(2026, 10, datetime.now(timezone.utc), []))
    assert cache.load(2026, 10) == old
    assert not list(cache.directory.glob("*.tmp"))


def test_month_query_includes_korean_midnight_and_leap_day():
    assert utc_query_dates(2026, 10) == (date(2026, 9, 30), date(2026, 10, 31))
    assert utc_query_dates(2024, 2) == (date(2024, 1, 31), date(2024, 2, 29))


def test_fetch_filters_both_month_edges_and_keeps_undated(tmp_path):
    provider = Mock()
    provider.fetch_matches.return_value = [
        match(1, "2026-09-30T14:59:00+00:00"),
        match(2, "2026-09-30T15:00:00+00:00"),
        match(3, "2026-10-31T14:59:00+00:00"),
        match(4, "2026-10-31T15:00:00+00:00"),
        match(5, None),
    ]
    result = ScheduleService(provider, CacheStore(tmp_path)).load_month(2026, 10)
    assert [m.id for m in result.matches] == [2, 3, 5]
    provider.fetch_matches.assert_called_once_with(date(2026, 9, 30), date(2026, 10, 31))
    assert result.source == "live" and not result.stale


def test_offline_refresh_preserves_timestamp_and_cache(tmp_path):
    cache = CacheStore(tmp_path)
    old = Snapshot(2026, 10, datetime(2026, 10, 1, tzinfo=timezone.utc), [match()])
    cache.save(old)
    provider = Mock()
    provider.fetch_matches.side_effect = NetworkError("인터넷 연결을 확인하세요.")
    service = ScheduleService(provider, cache)
    assert service.load_month(2026, 10).matches == old.matches
    provider.fetch_matches.assert_not_called()
    failed = service.load_month(2026, 10, refresh=True)
    assert failed.source == "cache" and failed.stale
    assert failed.fetched_at == old.fetched_at
    assert cache.load(2026, 10) == old


def test_no_key_and_no_cache_is_explicit(tmp_path):
    result = ScheduleService(None, CacheStore(tmp_path)).load_month(2026, 10)
    assert result.source == "empty" and result.fetched_at is None
    assert "API 키" in result.warning and "캐시가 없습니다" in result.warning


def test_storage_failure_still_returns_live_matches(tmp_path):
    provider = Mock()
    provider.fetch_matches.return_value = [match()]
    cache = CacheStore(tmp_path)
    cache.save = Mock(side_effect=CacheError("캐시 저장 실패"))
    result = ScheduleService(provider, cache).load_month(2026, 10)
    assert result.matches and result.source == "live"
    assert result.warning == "캐시 저장 실패"


def test_corrupt_cache_recovers_on_successful_fetch(tmp_path):
    cache = CacheStore(tmp_path)
    cache.directory.mkdir(parents=True)
    (cache.directory / "2026-10.json").write_text("bad json")
    provider = Mock()
    provider.fetch_matches.return_value = []
    result = ScheduleService(provider, cache).load_month(2026, 10)
    assert result.source == "live"
    assert result.warning == ""
    assert cache.load(2026, 10).matches == []


def test_invalid_unicode_cache_is_a_safe_storage_error(tmp_path):
    fixture = Match(1, "PL", 57, "invalid\ud800", 61, "Chelsea", None, "SCHEDULED")
    cache = CacheStore(tmp_path)
    with pytest.raises(CacheError, match="캐시 저장"):
        cache.save(Snapshot(2026, 10, datetime.now(timezone.utc), [fixture]))
    assert not list(cache.directory.glob("*.tmp"))
