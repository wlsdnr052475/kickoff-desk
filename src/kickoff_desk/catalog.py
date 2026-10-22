"""Competition team catalogues and separate, atomic live/demo caches."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from .models import COMPETITIONS, SEOUL, normalize_crest_url


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    short_name: str = ""
    crest: str | None = None
    competitions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0:
            raise ValueError("팀 식별자가 올바르지 않습니다.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("팀 이름이 올바르지 않습니다.")
        if not isinstance(self.short_name, str):
            raise ValueError("팀 약칭이 올바르지 않습니다.")
        if not isinstance(self.competitions, tuple) or any(
            not isinstance(code, str) or code not in COMPETITIONS for code in self.competitions
        ):
            raise ValueError("팀의 대회 정보가 올바르지 않습니다.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "short_name", self.short_name.strip())
        object.__setattr__(self, "crest", normalize_crest_url(self.crest))
        object.__setattr__(
            self, "competitions", tuple(code for code in COMPETITIONS if code in self.competitions)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "crest": self.crest,
            "competitions": list(self.competitions),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Team:
        if not isinstance(raw, dict) or not isinstance(raw.get("competitions"), list):
            raise ValueError("저장된 팀 정보가 올바르지 않습니다.")
        return cls(
            id=raw.get("id"),
            name=raw.get("name"),
            short_name=raw.get("short_name", ""),
            crest=raw.get("crest"),
            competitions=tuple(raw["competitions"]),
        )


def parse_team(raw: dict[str, Any], competition: str) -> Team:
    if not isinstance(raw, dict) or competition not in COMPETITIONS:
        raise ValueError("팀 정보가 올바르지 않습니다.")
    return Team(
        id=raw.get("id"),
        name=raw.get("name"),
        short_name="" if raw.get("shortName") is None else raw["shortName"],
        crest=raw.get("crest"),
        competitions=(competition,),
    )


def merge_teams(teams: Iterable[Team]) -> list[Team]:
    """Merge competition membership by ID, retaining the first available metadata."""
    merged: dict[int, Team] = {}
    for team in teams:
        if not isinstance(team, Team):
            raise ValueError("팀 목록이 올바르지 않습니다.")
        existing = merged.get(team.id)
        if existing is None:
            merged[team.id] = team
        else:
            merged[team.id] = replace(
                existing,
                short_name=existing.short_name or team.short_name,
                crest=existing.crest or team.crest,
                competitions=existing.competitions + team.competitions,
            )
    return sorted(merged.values(), key=lambda team: (team.name.casefold(), team.id))


class TeamProvider(Protocol):
    def fetch_teams(self) -> list[Team]: ...


@dataclass(frozen=True)
class CatalogResult:
    teams: list[Team]
    fetched_at: datetime | None
    source: str
    stale: bool = False
    warning: str = ""


class _CatalogCacheError(Exception):
    pass


class TeamCatalog:
    def __init__(self, provider: TeamProvider | None, directory: Path, mode: str = "live"):
        if mode not in {"live", "mock"}:
            raise ValueError("Invalid team catalogue mode")
        self.provider = provider
        self.mode = mode
        self.path = Path(directory) / "cache" / mode / "teams.json"

    def load(self, refresh: bool = False) -> CatalogResult:
        """Use an existing cache; fetch on a cache miss or explicit refresh."""
        # ApiClient imports Team; importing the exception here avoids a module cycle.
        from .api import ApiError

        cached = None
        cache_warning = ""
        try:
            cached = self._read_cache()
        except _CatalogCacheError as exc:
            cache_warning = str(exc)
        if cached is not None and not refresh:
            return cached
        if self.provider is None:
            return self._fallback(
                cached, cache_warning or "API 키를 등록하거나 데모 모드를 사용해 주세요."
            )
        try:
            fetched = self.provider.fetch_teams()
            if not isinstance(fetched, list):
                raise ValueError
            teams = merge_teams(fetched)
        except ApiError as exc:
            return self._fallback(cached, str(exc))
        except (ValueError, TypeError):
            return self._fallback(cached, "팀 목록의 응답 형식을 확인할 수 없습니다.")
        result = CatalogResult(teams, datetime.now(timezone.utc), self.mode)
        try:
            self._write_cache(result)
        except _CatalogCacheError as exc:
            result = replace(result, warning=str(exc))
        return result

    @staticmethod
    def _fallback(cached: CatalogResult | None, warning: str) -> CatalogResult:
        if cached is not None:
            return replace(cached, warning=warning + " 저장된 팀 목록을 표시합니다.")
        return CatalogResult([], None, "empty", True, warning + " 저장된 팀 목록이 없습니다.")

    def _read_cache(self) -> CatalogResult | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or type(raw.get("version")) is not int
                or raw["version"] != 1
                or raw.get("mode") != self.mode
                or not isinstance(raw.get("teams"), list)
            ):
                raise ValueError
            fetched_at = datetime.fromisoformat(raw["fetched_at"])
            if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
                raise ValueError
            fetched_at = fetched_at.astimezone(timezone.utc)
            fetched_at.astimezone(SEOUL)
            teams = merge_teams(Team.from_dict(team) for team in raw["teams"])
            return CatalogResult(
                teams,
                fetched_at,
                "cache",
                True,
                "저장된 팀 목록입니다. 최신 대회 참가 팀은 목록 새로고침으로 확인하세요.",
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            raise _CatalogCacheError(
                "저장된 팀 목록을 읽지 못했습니다. 목록 새로고침으로 복구할 수 있습니다."
            ) from None

    def _write_cache(self, result: CatalogResult) -> None:
        payload = {
            "version": 1,
            "mode": self.mode,
            "fetched_at": result.fetched_at.isoformat(),
            "teams": [team.to_dict() for team in result.teams],
        }
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            raise _CatalogCacheError(
                "팀 목록을 표시했지만 저장하지 못했습니다. 저장 공간을 확인해 주세요."
            ) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
