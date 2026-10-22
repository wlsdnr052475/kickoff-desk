"""Coordinate monthly UTC requests, KST selection and offline fallback."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from .api import ApiError
from .cache import CacheError, CacheStore, Snapshot
from .models import SEOUL, Match, filter_matches


class FixtureProvider(Protocol):
    def fetch_matches(self, date_from: date, date_to: date) -> list[Match]: ...


@dataclass(frozen=True)
class LoadResult:
    year: int
    month: int
    matches: list[Match]
    fetched_at: datetime | None
    source: str
    stale: bool = False
    warning: str = ""


@dataclass(frozen=True)
class RangeLoadResult:
    start: date
    end: date
    matches: list[Match]
    fetched_at: datetime | None
    source: str
    stale: bool = False
    warning: str = ""
    match_fetched_at: dict[int, datetime] = field(default_factory=dict)
    month_fetched_at: dict[tuple[int, int], datetime] = field(default_factory=dict)


def utc_query_dates(year: int, month: int) -> tuple[date, date]:
    """API date filters are UTC; include both edges of the entire Korean month."""
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    start = datetime.combine(first, time.min, SEOUL).astimezone(timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, SEOUL).astimezone(timezone.utc)
    return start.date(), (end - timedelta(microseconds=1)).date()


def _range_months(start: date, end: date) -> list[date]:
    if type(start) is not date or type(end) is not date or end < start:
        raise ValueError("조회 날짜 범위가 올바르지 않습니다.")
    if (end - start).days >= 120:
        raise ValueError("한 번에 조회할 수 있는 기간은 최대 120일입니다.")
    months = []
    current = start.replace(day=1)
    while current <= end:
        months.append(current)
        if (current.year, current.month) == (end.year, end.month):
            break
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


class ScheduleService:
    def __init__(self, provider: FixtureProvider | None, cache: CacheStore, mode: str = "live"):
        if mode not in {"live", "mock"}:
            raise ValueError("Invalid schedule mode")
        self.provider = provider
        self.cache = cache
        self.mode = mode

    def cached_versions(self, start: date, end: date) -> dict[int, tuple[datetime, Match]]:
        """Read newest known versions across existing monthly caches, with no HTTP.

        Include the full boundary months before applying any date filter. A
        rescheduled match outside the requested days must still supersede an
        older version that falls inside them. Missing or corrupt caches are
        skipped; normal month loading remains responsible for error reporting
        and recovery, so this helper does not establish complete range coverage.
        """
        versions: dict[int, tuple[datetime, Match]] = {}
        for month in _range_months(start, end):
            try:
                snapshot = self.cache.load(month.year, month.month)
            except CacheError:
                continue
            if snapshot is None:
                continue
            for match in snapshot.matches:
                existing = versions.get(match.id)
                if existing is None or snapshot.fetched_at >= existing[0]:
                    versions[match.id] = (snapshot.fetched_at, match)
        return versions

    def load_range(self, start: date, end: date, refresh: bool = False) -> RangeLoadResult:
        """Read an inclusive KST range through the same per-month offline caches.

        A bounded range prevents an accidental year-long GUI operation. Missing
        months remain explicit in the warning; a partial range is never labelled
        as wholly current. If a rescheduled fixture appears in two snapshots,
        the most recently fetched snapshot wins before applying date filters.
        """
        results = [
            self.load_month(month.year, month.month, refresh) for month in _range_months(start, end)
        ]

        earliest = datetime.min.replace(tzinfo=timezone.utc)
        matches: dict[int, Match] = {}
        match_stamps: dict[int, datetime] = {}
        for result in sorted(results, key=lambda item: item.fetched_at or earliest):
            for match in result.matches:
                matches[match.id] = match
                if result.fetched_at is not None:
                    match_stamps[match.id] = result.fetched_at
        visible = filter_matches(
            match
            for match in matches.values()
            if match.local_date is None or start <= match.local_date <= end
        )
        available = [item for item in results if item.source != "empty"]
        stamps = [item.fetched_at for item in available if item.fetched_at is not None]
        stale = any(item.stale or item.source == "empty" for item in results)
        source = (
            "empty"
            if not available
            else "cache"
            if any(item.source == "cache" for item in available)
            else self.mode
        )
        warnings = [
            f"{item.year}년 {item.month}월: {item.warning}" for item in results if item.warning
        ]
        return RangeLoadResult(
            start,
            end,
            visible,
            min(stamps) if stamps else None,
            source,
            stale,
            " ".join(warnings),
            match_fetched_at={
                match.id: match_stamps[match.id] for match in visible if match.id in match_stamps
            },
            month_fetched_at={
                (result.year, result.month): result.fetched_at
                for result in available
                if result.fetched_at is not None
            },
        )

    def load_month(self, year: int, month: int, refresh: bool = False) -> LoadResult:
        first, last = utc_query_dates(year, month)
        warning = ""
        cached = None
        try:
            cached = self.cache.load(year, month)
        except CacheError as exc:
            warning = str(exc)
        if cached is not None and not refresh:
            return LoadResult(
                year,
                month,
                cached.matches,
                cached.fetched_at,
                "cache",
                True,
                "저장된 일정입니다. 최신 변경 사항은 새로고침으로 확인하세요.",
            )
        if self.provider is None:
            return self._fallback(
                year, month, cached, "API 키를 등록하거나 Mock 모드를 사용하세요."
            )
        try:
            fetched = self.provider.fetch_matches(first, last)
        except ApiError as exc:
            return self._fallback(year, month, cached, str(exc))
        matches = filter_matches(
            [
                match
                for match in fetched
                if match.local_date is None
                or (match.local_date.year, match.local_date.month) == (year, month)
            ]
        )
        fetched_at = datetime.now(timezone.utc)
        warning = ""
        try:
            self.cache.save(Snapshot(year, month, fetched_at, matches))
        except CacheError as exc:
            warning = str(exc)
        return LoadResult(year, month, matches, fetched_at, self.mode, warning=warning)

    def _fallback(
        self,
        year: int,
        month: int,
        cached: Snapshot | None,
        warning: str,
    ) -> LoadResult:
        if cached is not None:
            return LoadResult(
                year,
                month,
                cached.matches,
                cached.fetched_at,
                "cache",
                True,
                warning + " 저장된 일정을 표시합니다.",
            )
        return LoadResult(
            year, month, [], None, "empty", True, warning + " 이 달의 캐시가 없습니다."
        )
