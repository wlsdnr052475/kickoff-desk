"""Deterministic demo fixtures that remain useful on any selected date."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .models import Match


class MockClient:
    """Generate a local demonstration; team pairs and fixtures are fictional."""

    def fetch_matches(self, date_from: date, date_to: date) -> list[Match]:
        if not isinstance(date_from, date) or not isinstance(date_to, date) or date_from > date_to:
            raise ValueError("조회 날짜 범위가 올바르지 않습니다.")
        if isinstance(date_from, datetime) or isinstance(date_to, datetime):
            raise ValueError("조회에는 날짜만 지정해 주세요.")
        fixtures = []
        current = date_from
        statuses = ("TIMED", "SCHEDULED", "FINISHED", "IN_PLAY", "POSTPONED", "CANCELED", "PAUSED")
        while current <= date_to:
            ordinal = current.toordinal()
            for offset, (code, home_id, home, away_id, away, hour) in enumerate(
                (
                    ("PL", 57, "아스널", 73, "토트넘", 14),
                    ("PD", 81, "바르셀로나", 86, "레알 마드리드", 15),
                    ("CL", 5, "바이에른 뮌헨", 65, "맨체스터 시티", 20),
                )
            ):
                fixtures.append(
                    Match(
                        id=ordinal * 10 + offset + 1,
                        competition=code,
                        home_id=home_id,
                        home_name=home,
                        away_id=away_id,
                        away_name=away,
                        utc_date=datetime.combine(current, time(hour), timezone.utc),
                        status=statuses[(ordinal + offset) % len(statuses)],
                    )
                )
            current += timedelta(days=1)
        fixtures.append(
            Match(
                id=9_000_001,
                competition="CL",
                home_id=None,
                home_name="홈 팀 미정",
                away_id=None,
                away_name="원정 팀 미정",
                utc_date=None,
                status="SCHEDULED",
            )
        )
        return fixtures
