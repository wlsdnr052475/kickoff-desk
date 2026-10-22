from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from kickoff_desk.cache import CacheStore, Snapshot
from kickoff_desk.models import Match
from kickoff_desk.service import LoadResult, ScheduleService


def fixture(identifier, instant, status="TIMED"):
    return Match(
        identifier,
        "PL",
        57,
        "Arsenal FC",
        61,
        "Chelsea FC",
        datetime.fromisoformat(instant) if instant else None,
        status,
    )


def test_week_crosses_year_uses_both_caches_and_seoul_dates(tmp_path):
    cache = CacheStore(tmp_path)
    stamp = datetime(2026, 12, 20, tzinfo=timezone.utc)
    december = [
        fixture(1, "2026-12-27T14:59:00+00:00"),
        fixture(2, "2026-12-30T15:00:00+00:00"),
        fixture(8, None),
    ]
    january = [
        fixture(3, "2026-12-31T15:00:00+00:00"),
        fixture(4, "2027-01-03T15:00:00+00:00"),
        fixture(8, None),
    ]
    cache.save(Snapshot(2026, 12, stamp, december))
    cache.save(Snapshot(2027, 1, stamp, january))
    provider = Mock()
    result = ScheduleService(provider, cache).load_range(date(2026, 12, 28), date(2027, 1, 3))
    assert [match.id for match in result.matches] == [2, 3, 8]
    assert result.source == "cache" and result.stale
    assert result.fetched_at == stamp
    provider.fetch_matches.assert_not_called()


def test_missing_month_does_not_hide_partial_range_or_oldest_update(tmp_path):
    cache = CacheStore(tmp_path)
    stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    cache.save(Snapshot(2026, 10, stamp, [fixture(1, "2026-10-31T10:00:00+00:00")]))
    result = ScheduleService(None, cache).load_range(date(2026, 10, 26), date(2026, 11, 1))
    assert [match.id for match in result.matches] == [1]
    assert result.stale and result.source == "cache"
    assert "2026년 11월" in result.warning and "캐시가 없습니다" in result.warning
    assert result.fetched_at == stamp
    assert result.match_fetched_at == {1: stamp}
    assert result.month_fetched_at == {(2026, 10): stamp}


def test_newest_rescheduled_fixture_wins_before_range_filtering(tmp_path):
    service = ScheduleService(None, CacheStore(tmp_path))
    old = fixture(1, "2026-10-31T10:00:00+00:00")
    rescheduled = fixture(1, "2026-11-15T10:00:00+00:00")
    service.load_month = Mock(
        side_effect=[
            LoadResult(2026, 10, [old], datetime(2026, 10, 20, tzinfo=timezone.utc), "cache", True),
            LoadResult(
                2026, 11, [rescheduled], datetime(2026, 10, 22, tzinfo=timezone.utc), "live"
            ),
        ]
    )
    result = service.load_range(date(2026, 10, 26), date(2026, 11, 1), refresh=True)
    assert not result.matches
    assert result.match_fetched_at == {}
    assert result.stale and result.source == "cache"
    assert result.fetched_at == datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert service.load_month.call_args_list[0].args == (2026, 10, True)
    assert service.load_month.call_args_list[1].args == (2026, 11, True)


def test_range_merges_current_results_in_time_order(tmp_path):
    provider = Mock()
    provider.fetch_matches.side_effect = [
        [fixture(2, "2026-10-31T09:00:00+00:00")],
        [fixture(3, "2026-11-01T09:00:00+00:00")],
    ]
    result = ScheduleService(provider, CacheStore(tmp_path)).load_range(
        date(2026, 10, 26), date(2026, 11, 1)
    )
    assert [match.id for match in result.matches] == [2, 3]
    assert result.source == "live" and not result.stale and not result.warning
    assert provider.fetch_matches.call_count == 2


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 10, 2), date(2026, 10, 1)),
        (datetime(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 10, 1), "2026-10-02"),
        (date(2026, 1, 1), date(2026, 6, 1)),
    ],
)
def test_invalid_or_unbounded_range_sends_no_requests(tmp_path, start, end):
    provider = Mock()
    with pytest.raises(ValueError):
        ScheduleService(provider, CacheStore(tmp_path)).load_range(start, end)
    provider.fetch_matches.assert_not_called()


def test_single_day_and_empty_offline_range(tmp_path):
    result = ScheduleService(None, CacheStore(tmp_path)).load_range(
        date(2026, 10, 22), date(2026, 10, 22)
    )
    assert result.source == "empty" and result.stale and result.fetched_at is None
    assert result.start == result.end == date(2026, 10, 22)
    assert "API 키" in result.warning


def test_cached_versions_resolves_reschedule_before_filtering_without_network(tmp_path):
    cache = CacheStore(tmp_path)
    old_stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    new_stamp = datetime(2026, 10, 22, tzinfo=timezone.utc)
    old = fixture(1, "2026-10-31T10:00:00+00:00")
    rescheduled = fixture(1, "2026-11-15T10:00:00+00:00")
    cache.save(Snapshot(2026, 10, old_stamp, [old]))
    cache.save(Snapshot(2026, 11, new_stamp, [rescheduled]))
    provider = Mock()
    service = ScheduleService(provider, cache)
    versions = service.cached_versions(date(2026, 10, 29), date(2026, 11, 1))
    assert versions == {1: (new_stamp, rescheduled)}
    provider.fetch_matches.assert_not_called()


def test_cached_versions_uses_fetch_recency_even_when_earlier_month_is_newer(tmp_path):
    cache = CacheStore(tmp_path)
    old_stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    new_stamp = datetime(2026, 10, 22, tzinfo=timezone.utc)
    earlier = fixture(1, "2026-10-31T10:00:00+00:00")
    later = fixture(1, "2026-11-15T10:00:00+00:00")
    cache.save(Snapshot(2026, 10, new_stamp, [earlier]))
    cache.save(Snapshot(2026, 11, old_stamp, [later]))
    versions = ScheduleService(None, cache).cached_versions(date(2026, 10, 29), date(2026, 11, 20))
    assert versions == {1: (new_stamp, earlier)}


def test_cached_versions_skips_corrupt_and_missing_months_without_repair_requests(tmp_path):
    cache = CacheStore(tmp_path)
    stamp = datetime(2026, 12, 20, tzinfo=timezone.utc)
    current = fixture(1, "2026-12-31T15:00:00+00:00")
    cache.save(Snapshot(2027, 1, stamp, [current]))
    corrupt = cache.directory / "2026-12.json"
    corrupt.write_text("broken json")
    provider = Mock()
    service = ScheduleService(provider, cache)
    versions = service.cached_versions(date(2026, 12, 28), date(2027, 2, 1))
    assert versions == {1: (stamp, current)}
    assert corrupt.read_text() == "broken json"
    assert not (cache.directory / "2027-02.json").exists()
    provider.fetch_matches.assert_not_called()


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 10, 2), date(2026, 10, 1)),
        (datetime(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 10, 1), "2026-10-02"),
        (date(2026, 1, 1), date(2026, 6, 1)),
    ],
)
def test_invalid_cached_version_range_performs_no_io(tmp_path, start, end):
    cache = Mock()
    provider = Mock()
    with pytest.raises(ValueError):
        ScheduleService(provider, cache).cached_versions(start, end)
    cache.load.assert_not_called()
    provider.fetch_matches.assert_not_called()


def test_cached_versions_accepts_120_inclusive_days_but_not_121(tmp_path):
    service = ScheduleService(None, CacheStore(tmp_path))
    start = date(2026, 10, 1)
    assert service.cached_versions(start, start + timedelta(days=119)) == {}
    with pytest.raises(ValueError):
        service.cached_versions(start, start + timedelta(days=120))


def test_range_preserves_winning_match_and_month_timestamps_separately_from_oldest(tmp_path):
    cache = CacheStore(tmp_path)
    old_stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    new_stamp = datetime(2026, 10, 22, tzinfo=timezone.utc)
    original = fixture(1, "2026-10-31T10:00:00+00:00")
    rescheduled = fixture(1, "2026-11-15T10:00:00+00:00")
    other = fixture(2, "2026-10-28T10:00:00+00:00")
    cache.save(Snapshot(2026, 10, old_stamp, [original, other]))
    cache.save(Snapshot(2026, 11, new_stamp, [rescheduled]))
    result = ScheduleService(None, cache).load_range(date(2026, 10, 26), date(2026, 11, 30))
    assert result.matches == [other, rescheduled]
    assert result.fetched_at == old_stamp
    assert result.match_fetched_at == {2: old_stamp, 1: new_stamp}
    assert result.month_fetched_at == {(2026, 10): old_stamp, (2026, 11): new_stamp}
