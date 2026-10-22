"""Local, opt-in kickoff reminders; an OS notification request is not a receipt.

Only schedules already loaded by the UI are examined. No API requests or
background service are started. History contains identifiers and timestamps,
never credentials, names or scores. A corrupt history suspends reminders so a
restart cannot silently turn lost history into a burst of duplicate alerts.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from weakref import WeakMethod

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from .models import COMPETITIONS, SEOUL, Match
from .settings import Preferences
from .teams import display_team_name

_MAX_HISTORY = 1000
_MAX_HISTORY_BYTES = 512 * 1024
_RETENTION = timedelta(days=7)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


class ReminderEngine:
    """Testable reminder selection and durable deduplication, independent of Qt.

    ``notify`` returns whether a notification was submitted to the OS. It must
    return False when unavailable; the platform cannot confirm actual display.
    The history is written after submission. A crash or disk failure between
    those steps can repeat an alert after restart; exactly-once OS delivery is
    not available through QSystemTrayIcon.
    """

    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        notify: Callable[[str, str], bool] | None = None,
        warning: Callable[[str], None] | None = None,
    ):
        self.path = Path(directory) / "notification-history.json"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._notify = notify or (lambda _title, _body: False)
        self._warning = warning or (lambda _message: None)
        self._history: dict[tuple[str, int, datetime], datetime] = {}
        self._loaded = False
        self._blocked = False
        self._warnings: set[str] = set()

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.add(message)
            self._warning(message)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with self.path.open("rb") as handle:
                content = handle.read(_MAX_HISTORY_BYTES + 1)
            if len(content) > _MAX_HISTORY_BYTES:
                raise ValueError
            payload = json.loads(content)
            if (
                not isinstance(payload, dict)
                or type(payload.get("version")) is not int
                or payload["version"] != 1
                or not isinstance(payload.get("entries"), list)
                or len(payload["entries"]) > _MAX_HISTORY
            ):
                raise ValueError
            parsed: dict[tuple[str, int, datetime], datetime] = {}
            for entry in payload["entries"]:
                if not isinstance(entry, dict):
                    raise ValueError
                mode, match_id = entry.get("mode"), entry.get("match_id")
                if mode not in ("mock", "live") or type(match_id) is not int or match_id <= 0:
                    raise ValueError
                key = mode, match_id, _timestamp(entry.get("kickoff"))
                if key in parsed:
                    raise ValueError
                parsed[key] = _timestamp(entry.get("notified_at"))
            self._history = parsed
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
            self._blocked = True
            self._warn("알림 기록을 읽지 못해 중복 방지를 위해 알림을 중지했습니다.")

    def _save(self) -> bool:
        payload = {
            "version": 1,
            "entries": [
                {
                    "mode": mode,
                    "match_id": match_id,
                    "kickoff": kickoff.isoformat(),
                    "notified_at": notified.isoformat(),
                }
                for (mode, match_id, kickoff), notified in self._history.items()
            ],
        }
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".notification-history-", suffix=".tmp", dir=self.path.parent
            )
            temporary_path = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            return True
        except (OSError, UnicodeError):
            self._blocked = True
            self._warn(
                "알림 기록을 저장하지 못해 추가 알림을 중지했습니다. "
                "다시 실행하면 같은 알림이 반복될 수 있습니다."
            )
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def check(self, matches: list[Match], preferences: Preferences, use_mock: bool) -> None:
        if not preferences.notifications_enabled or self._blocked:
            return
        minutes = preferences.notification_minutes
        if type(minutes) is not int or minutes not in (10, 30, 60):
            self._warn("알림 시간을 10분, 30분, 60분 중에서 선택해 주세요.")
            return
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
            now = now.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            self._warn("현재 시간을 확인하지 못해 이번 알림 확인을 건너뛰었습니다.")
            return
        self._load()
        if self._blocked:
            return
        retained = {
            key: value for key, value in self._history.items() if key[2] >= now - _RETENTION
        }
        if len(retained) != len(self._history):
            self._history = retained
            if not self._save():
                return
        mode = "mock" if use_mock else "live"
        for match in matches:
            kickoff = match.utc_date
            if (
                match.status != "TIMED"
                or kickoff is None
                or not (0 < (kickoff - now).total_seconds() <= minutes * 60)
                or not ({match.home_id, match.away_id} & preferences.favorite_team_ids)
            ):
                continue
            key = mode, match.id, kickoff
            if key in self._history:
                continue
            if len(self._history) >= _MAX_HISTORY:
                expired = [item for item in self._history if item[2] <= now]
                if not expired:
                    self._warn("알림 기록이 가득 차 추가 알림을 보류했습니다.")
                    continue
                del self._history[min(expired, key=lambda item: item[2])]
            home = preferences.custom_team_names.get(match.home_id) or display_team_name(
                match.home_id, match.home_name
            )
            away = preferences.custom_team_names.get(match.away_id) or display_team_name(
                match.away_id, match.away_name
            )
            title = ("[데모] " if use_mock else "") + "Kickoff Desk · 경기 시작 알림"
            remaining = math.ceil((kickoff - now).total_seconds() / 60)
            body = (
                f"{home} vs {away}\n{COMPETITIONS[match.competition]} · "
                f"{kickoff.astimezone(SEOUL):%m월 %d일 %H:%M} (KST)\n"
                f"경기 시작까지 약 {remaining}분"
            )
            try:
                submitted = self._notify(title, body)
            except Exception:
                submitted = False
            if not submitted:
                self._warn("알림을 요청하지 못했습니다. 운영체제의 알림 설정을 확인해 주세요.")
                continue
            self._history[key] = now
            if not self._save():
                return


class NotificationController(QObject):
    """Own the opt-in local timer and the tray's open/quit actions."""

    restore_requested = Signal()
    quit_requested = Signal()
    warnings = Signal(str)

    def __init__(self, directory: Path, parent: QObject | None = None, clock=None):
        super().__init__(parent)
        self._matches: list[Match] = []
        self._preferences = Preferences()
        self._use_mock = True
        self._closed = False
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._hidden = False
        self._previous_quit_setting: bool | None = None
        self._warning_messages: set[str] = set()

        # The engine must not keep this QObject alive through bound callbacks.
        # Otherwise cyclic collection can destroy Qt objects on a worker thread.
        notify_method = WeakMethod(self._submit)
        warning_method = WeakMethod(self._warn)

        def notify(title: str, body: str) -> bool:
            method = notify_method()
            return method(title, body) if method is not None else False

        def warning(message: str) -> None:
            method = warning_method()
            if method is not None:
                method(message)

        self._engine = ReminderEngine(directory, clock=clock, notify=notify, warning=warning)
        self._timer = QTimer(self)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(self.check)

    @property
    def tray_available(self) -> bool:
        return not self._closed and QSystemTrayIcon.isSystemTrayAvailable()

    def _warn(self, message: str) -> None:
        if message not in self._warning_messages:
            self._warning_messages.add(message)
            self.warnings.emit(message)

    def _ensure_tray(self) -> bool:
        if not self.tray_available:
            return False
        if self._tray is None:
            application = QApplication.instance()
            if application is None:
                return False
            self._tray = QSystemTrayIcon(self)
            self._tray.setIcon(
                application.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            )
            self._tray.setToolTip("Kickoff Desk")
            self._menu = QMenu()
            self._menu.addAction("열기", self._restore)
            self._menu.addAction("종료", self.quit_requested.emit)
            self._tray.setContextMenu(self._menu)
            self._tray.activated.connect(self._activated)
            self._tray.messageClicked.connect(self._restore)
        self._tray.show()
        return True

    def _activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore()

    def _restore_quit_setting(self) -> None:
        if self._previous_quit_setting is not None:
            application = QApplication.instance()
            if application is not None:
                application.setQuitOnLastWindowClosed(self._previous_quit_setting)
            self._previous_quit_setting = None

    def _restore(self) -> None:
        if self._closed:
            return
        self._hidden = False
        self.restore_requested.emit()
        self._restore_quit_setting()
        if self._tray is not None and not self._preferences.notifications_enabled:
            self._tray.hide()

    def _submit(self, title: str, body: str) -> bool:
        if not QSystemTrayIcon.supportsMessages() or not self._ensure_tray():
            return False
        self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 10_000)
        return True

    def update(self, matches: list[Match], preferences: Preferences, use_mock: bool) -> None:
        if self._closed:
            return
        self._matches = list(matches)
        self._preferences = copy.deepcopy(preferences)
        self._use_mock = use_mock
        if preferences.notifications_enabled:
            self._timer.start()
            self.check()
        else:
            self._timer.stop()
            if self._tray is not None and not self._hidden:
                self._tray.hide()

    def check(self) -> None:
        if self._closed or not self._preferences.notifications_enabled:
            return
        if not self.tray_available or not QSystemTrayIcon.supportsMessages():
            self._warn("이 환경에서는 시스템 트레이 알림을 사용할 수 없습니다.")
            return
        self._engine.check(self._matches, self._preferences, self._use_mock)

    def hide_to_tray(self) -> bool:
        if self._closed or not self._ensure_tray():
            self._warn("시스템 트레이를 사용할 수 없어 창을 숨기지 않았습니다.")
            return False
        application = QApplication.instance()
        if self._previous_quit_setting is None:
            self._previous_quit_setting = application.quitOnLastWindowClosed()
        application.setQuitOnLastWindowClosed(False)
        self._hidden = True
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
        if self._menu is not None:
            self._menu.close()
            self._menu.deleteLater()
            self._menu = None
        self._restore_quit_setting()
