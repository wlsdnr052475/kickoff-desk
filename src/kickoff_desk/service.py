"""Coordinate monthly UTC requests, KST selection and offline fallback."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
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


def utc_query_dates(year: int, month: int) -> tuple[date, date]:
    """API date filters are UTC; include both edges of the entire Korean month."""
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    start = datetime.combine(first, time.min, SEOUL).astimezone(timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, SEOUL).astimezone(timezone.utc)
    return start.date(), (end - timedelta(microseconds=1)).date()


class ScheduleService:
    def __init__(self, provider: FixtureProvider | None, cache: CacheStore, mode: str = "live"):
        if mode not in {"live", "mock"}:
            raise ValueError("Invalid schedule mode")
        self.provider = provider
        self.cache = cache
        self.mode = mode

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
