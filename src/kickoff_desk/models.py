"""The small, score-free data model shared by API, cache, and desktop UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
COMPETITIONS = {"PL": "프리미어리그", "PD": "라리가", "CL": "UEFA 챔피언스리그"}
STATUS_LABELS = {
    "SCHEDULED": "시간 미확정",
    "TIMED": "경기 예정",
    "LIVE": "진행 중",
    "IN_PLAY": "진행 중",
    "PAUSED": "하프타임",
    "FINISHED": "경기 종료",
    "POSTPONED": "연기 · 일정 미확정",
    "SUSPENDED": "중단 · 일정 미확정",
    "CANCELED": "취소",
    "AWARDED": "경기 종료 (행정 결정)",
    "UNKNOWN": "상태 미확인",
}
_CONFIRMED_STATUSES = {"TIMED", "LIVE", "IN_PLAY", "PAUSED", "FINISHED", "AWARDED"}
_CREST_HOST = "crests.football-data.org"


def normalize_crest_url(value: Any) -> str | None:
    """Allow only public, credential-free team images on the API's crest host."""
    if not isinstance(value, str) or any(ord(char) < 33 or ord(char) > 126 for char in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or parts.netloc.lower() not in {_CREST_HOST, f"{_CREST_HOST}:443"}
        or "?" in value
        or "#" in value
        or re.fullmatch(r"/[1-9][0-9]*\.(?:png|svg)", parts.path) is None
    ):
        return None
    return f"https://{_CREST_HOST}{parts.path}"


def _identifier(value: Any, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("경기 또는 팀 식별자가 올바르지 않습니다.")
    return value


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("경기 일시가 올바르지 않습니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("경기 일시가 올바르지 않습니다.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("경기 일시에 시간대 정보가 없습니다.")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("경기 일시가 표시 가능한 범위를 벗어났습니다.") from None


def _team(value: Any, fallback: str) -> tuple[int | None, str, str | None]:
    if value is None:
        return None, fallback, None
    if not isinstance(value, dict):
        raise ValueError("팀 정보가 올바르지 않습니다.")
    identifier = _identifier(value.get("id"), optional=True)
    name = value.get("name")
    return (
        identifier,
        name.strip() if isinstance(name, str) and name.strip() else fallback,
        normalize_crest_url(value.get("crest")),
    )


@dataclass(frozen=True)
class Match:
    id: int
    competition: str
    home_id: int | None
    home_name: str
    away_id: int | None
    away_name: str
    utc_date: datetime | None
    status: str
    home_crest: str | None = None
    away_crest: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.id)
        _identifier(self.home_id, optional=True)
        _identifier(self.away_id, optional=True)
        if self.competition not in COMPETITIONS:
            raise ValueError("지원하지 않는 대회입니다.")
        if not isinstance(self.home_name, str) or not isinstance(self.away_name, str):
            raise ValueError("팀 이름이 올바르지 않습니다.")
        if self.utc_date is not None:
            if not isinstance(self.utc_date, datetime) or self.utc_date.tzinfo is None:
                raise ValueError("경기 일시에 시간대 정보가 없습니다.")
            if self.utc_date.utcoffset() is None:
                raise ValueError("경기 일시에 시간대 정보가 없습니다.")
            try:
                normalized = self.utc_date.astimezone(timezone.utc)
                # Validate display conversion here so cache/API errors are caught at ingestion.
                normalized.astimezone(SEOUL)
            except OverflowError:
                raise ValueError("경기 일시가 표시 가능한 범위를 벗어났습니다.") from None
            object.__setattr__(self, "utc_date", normalized)
        if not isinstance(self.status, str) or self.status not in STATUS_LABELS:
            object.__setattr__(self, "status", "UNKNOWN")
        object.__setattr__(self, "home_crest", normalize_crest_url(self.home_crest))
        object.__setattr__(self, "away_crest", normalize_crest_url(self.away_crest))

    @property
    def local_date(self) -> date | None:
        return self.utc_date.astimezone(SEOUL).date() if self.utc_date else None

    @property
    def is_time_confirmed(self) -> bool:
        return self.utc_date is not None and self.status in _CONFIRMED_STATUSES

    @property
    def kickoff_label(self) -> str:
        if self.utc_date is None:
            return "일정 미정"
        if not self.is_time_confirmed:
            return "시간 미확정"
        return self.utc_date.astimezone(SEOUL).strftime("%H:%M")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    def to_dict(self) -> dict[str, Any]:
        """Persist only the allowlisted fields used by the application."""
        return {
            "id": self.id,
            "competition": self.competition,
            "home_id": self.home_id,
            "home_name": self.home_name,
            "home_crest": self.home_crest,
            "away_id": self.away_id,
            "away_name": self.away_name,
            "away_crest": self.away_crest,
            "utc_date": self.utc_date.isoformat().replace("+00:00", "Z") if self.utc_date else None,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Match:
        if not isinstance(value, dict):
            raise ValueError("저장된 경기 정보가 올바르지 않습니다.")
        return parse_match(
            {
                "id": value.get("id"),
                "competition": {"code": value.get("competition")},
                "homeTeam": {
                    "id": value.get("home_id"),
                    "name": value.get("home_name"),
                    "crest": value.get("home_crest"),
                },
                "awayTeam": {
                    "id": value.get("away_id"),
                    "name": value.get("away_name"),
                    "crest": value.get("away_crest"),
                },
                "utcDate": value.get("utc_date"),
                "status": value.get("status"),
            }
        )


def parse_match(raw: dict[str, Any]) -> Match:
    """Normalize one football-data.org v4 match, discarding unused API fields."""
    if not isinstance(raw, dict) or not isinstance(raw.get("competition"), dict):
        raise ValueError("경기 정보가 올바르지 않습니다.")
    identifier = _identifier(raw.get("id"))
    competition = raw["competition"].get("code")
    if not isinstance(competition, str) or competition not in COMPETITIONS:
        raise ValueError("지원하지 않는 대회입니다.")
    home_id, home_name, home_crest = _team(raw.get("homeTeam"), "홈 팀 미정")
    away_id, away_name, away_crest = _team(raw.get("awayTeam"), "원정 팀 미정")
    status = raw.get("status")
    if not isinstance(status, str) or status not in STATUS_LABELS:
        status = "UNKNOWN"
    return Match(
        id=identifier,
        competition=competition,
        home_id=home_id,
        home_name=home_name,
        away_id=away_id,
        away_name=away_name,
        utc_date=_utc_datetime(raw.get("utcDate")),
        status=status,
        home_crest=home_crest,
        away_crest=away_crest,
    )


def filter_matches(
    matches: Iterable[Match],
    selected_date: date | None = None,
    competitions: set[str] | None = None,
    favorite_team_ids: set[int] | None = None,
) -> list[Match]:
    """Filter using Korean calendar dates; None means no filter."""
    filtered = (
        match
        for match in matches
        if (selected_date is None or match.local_date == selected_date)
        and (competitions is None or match.competition in competitions)
        and (
            favorite_team_ids is None
            or match.home_id in favorite_team_ids
            or match.away_id in favorite_team_ids
        )
    )
    latest = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(
        filtered, key=lambda match: (match.utc_date is None, match.utc_date or latest, match.id)
    )
