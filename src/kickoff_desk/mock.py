"""Deterministic demo fixtures that remain useful on any selected date."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .catalog import Team
from .models import Match


class MockClient:
    """Generate a local demonstration; team pairs and fixtures are fictional."""

    def fetch_teams(self) -> list[Team]:
        """A fictional demo roster, independent of the displayed month's fixtures.

        IDs have been verified against the API so favorites survive mode changes.
        The chosen membership is fictional, not a live-season participant list.
        """
        return [
            Team(57, "Arsenal FC", "Arsenal", competitions=("PL", "CL")),
            Team(73, "Tottenham Hotspur FC", "Tottenham", competitions=("PL",)),
            Team(81, "FC Barcelona", "Barcelona", competitions=("PD", "CL")),
            Team(86, "Real Madrid CF", "Real Madrid", competitions=("PD", "CL")),
            Team(5, "FC Bayern München", "Bayern Munich", competitions=("CL",)),
            Team(65, "Manchester City FC", "Man City", competitions=("PL", "CL")),
            Team(64, "Liverpool FC", "Liverpool", competitions=("PL",)),
            Team(66, "Manchester United FC", "Man Utd", competitions=("PL",)),
            Team(61, "Chelsea FC", "Chelsea", competitions=("PL",)),
            Team(78, "Club Atlético de Madrid", "Atletico Madrid", competitions=("PD",)),
            Team(90, "Real Betis Balompié", "Real Betis", competitions=("PD",)),
            Team(77, "Athletic Club", "Athletic Bilbao", competitions=("PD",)),
            Team(524, "Paris Saint-Germain FC", "PSG", competitions=("CL",)),
            Team(108, "FC Internazionale Milano", "Inter", competitions=("CL",)),
            Team(4, "Borussia Dortmund", "Dortmund", competitions=("CL",)),
        ]

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
