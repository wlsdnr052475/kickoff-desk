"""Versioned, score-free monthly snapshots with atomic replacement."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Match


class CacheError(Exception):
    """A safe message suitable for the UI."""


@dataclass(frozen=True)
class Snapshot:
    year: int
    month: int
    fetched_at: datetime
    matches: list[Match]


class CacheStore:
    def __init__(self, directory: Path, namespace: str = "live"):
        if namespace not in {"live", "mock"}:
            raise ValueError("Invalid cache namespace")
        self.directory = Path(directory) / "cache" / namespace

    def _path(self, year: int, month: int) -> Path:
        datetime(year, month, 1)
        return self.directory / f"{year:04d}-{month:02d}.json"

    def load(self, year: int, month: int) -> Snapshot | None:
        path = self._path(year, month)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("version") != 1
                or raw.get("year") != year
                or raw.get("month") != month
                or not isinstance(raw.get("matches"), list)
            ):
                raise ValueError
            fetched_at = datetime.fromisoformat(raw["fetched_at"])
            if fetched_at.tzinfo is None:
                raise ValueError
            matches = [Match.from_dict(item) for item in raw["matches"]]
            if any(
                m.local_date is not None
                and (m.local_date.year, m.local_date.month) != (year, month)
                for m in matches
            ):
                raise ValueError
            return Snapshot(year, month, fetched_at, matches)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise CacheError(
                "저장된 일정을 읽지 못했습니다. 새로고침으로 복구할 수 있습니다."
            ) from None

    def save(self, snapshot: Snapshot) -> None:
        target = self._path(snapshot.year, snapshot.month)
        if snapshot.fetched_at.tzinfo is None:
            raise ValueError("Snapshot timestamp must be timezone-aware")
        # A strict allowlist prevents API metadata, scores or credentials from being persisted.
        payload = {
            "version": 1,
            "year": snapshot.year,
            "month": snapshot.month,
            "fetched_at": snapshot.fetched_at.astimezone(timezone.utc).isoformat(),
            "matches": [match.to_dict() for match in snapshot.matches],
        }
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except (OSError, UnicodeError):
            raise CacheError(
                "일정을 표시했지만 캐시 저장에 실패했습니다. 저장 공간을 확인하세요."
            ) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
