"""Bounded, explicit requests to the football-data.org v4 schedule API."""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import requests

from .models import COMPETITIONS, Match, parse_match


class ApiError(Exception):
    """A user-safe API failure. Messages never include server or transport text."""


class AuthenticationError(ApiError):
    pass


class RateLimitError(ApiError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(
            f"API 요청 한도에 도달했습니다. {self.retry_after}초 후 다시 시도해 주세요."
        )


class NetworkError(ApiError):
    pass


class InvalidResponseError(ApiError):
    pass


class ApiClient:
    BASE_URL = "https://api.football-data.org/v4"
    TIMEOUT = (5, 15)

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._session = session if session is not None else requests.Session()
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._next_allowed = 0.0
        self._api_key = ""
        self.set_api_key(api_key)

    def set_api_key(self, api_key: str) -> None:
        if not isinstance(api_key, str) or any(
            ord(char) < 32 or ord(char) > 126 for char in api_key
        ):
            raise AuthenticationError("API 키 형식이 올바르지 않습니다.")
        self._api_key = api_key.strip()

    def _check_cooldown(self) -> None:
        remaining = self._next_allowed - self._monotonic()
        if remaining > 0:
            raise RateLimitError(remaining)

    @staticmethod
    def _seconds(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, seconds) if math.isfinite(seconds) else None

    @classmethod
    def _retry_after(cls, value: str | None) -> float | None:
        seconds = cls._seconds(value)
        if seconds is not None or value is None:
            return seconds
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            return max(0.0, instant.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None

    def _update_cooldown(self, response: requests.Response) -> float:
        headers = {str(key).lower(): value for key, value in response.headers.items()}
        available = self._seconds(headers.get("x-requests-available-minute"))
        reset = self._seconds(headers.get("x-requestcounter-reset"))
        retry = self._retry_after(headers.get("retry-after"))
        if response.status_code == 429 or available == 0:
            delay = max(reset or 0, retry or 0) or 60.0
        elif retry is not None and retry > 0:
            # A server can ask callers to back off on 503 while quota remains.
            delay = retry
        else:
            return 0.0
        if delay:
            self._next_allowed = max(self._next_allowed, self._monotonic() + delay)
        return delay

    def fetch_matches(self, date_from: date, date_to: date) -> list[Match]:
        """Fetch all three competitions for inclusive UTC dates, or fail as a unit."""
        if not isinstance(date_from, date) or not isinstance(date_to, date) or date_from > date_to:
            raise ValueError("조회 날짜 범위가 올바르지 않습니다.")
        if isinstance(date_from, datetime) or isinstance(date_to, datetime):
            raise ValueError("조회에는 날짜만 지정해 주세요.")
        if not self._api_key:
            raise AuthenticationError("API 키를 등록하거나 데모 모드를 사용해 주세요.")
        results: dict[int, Match] = {}
        for competition in COMPETITIONS:
            self._check_cooldown()
            try:
                response = self._session.get(
                    f"{self.BASE_URL}/competitions/{competition}/matches",
                    headers={"X-Auth-Token": self._api_key, "Accept": "application/json"},
                    params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
                    timeout=self.TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise NetworkError(
                    "인터넷 연결을 확인해 주세요. 저장된 일정은 오프라인으로 확인할 수 있습니다."
                ) from None
            delay = self._update_cooldown(response)
            if response.status_code in {401, 403}:
                raise AuthenticationError("API 인증 또는 대회 접근 권한을 확인해 주세요.")
            if response.status_code == 429:
                raise RateLimitError(delay)
            if response.status_code >= 500:
                raise ApiError("일정 서버에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.")
            if response.status_code != 200:
                raise InvalidResponseError(
                    "일정 서버가 요청을 처리하지 못했습니다. 설정과 조회 날짜를 확인해 주세요."
                )
            try:
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
                    raise ValueError
                for raw in payload["matches"]:
                    match = parse_match(raw)
                    if match.competition != competition:
                        raise ValueError
                    results.setdefault(match.id, match)
            except (ValueError, TypeError, KeyError, OverflowError):
                raise InvalidResponseError(
                    "일정 서버의 응답 형식을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요."
                ) from None
        latest = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(
            results.values(),
            key=lambda match: (match.utc_date is None, match.utc_date or latest, match.id),
        )
