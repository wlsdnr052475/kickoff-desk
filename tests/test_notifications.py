"""Kickoff boundaries, durable deduplication and tray lifecycle without OS alerts."""

import gc
import json
import weakref
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from kickoff_desk import notifications
from kickoff_desk.models import Match
from kickoff_desk.notifications import NotificationController, ReminderEngine
from kickoff_desk.settings import Preferences

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def fixture_match(minutes=10, **changes):
    match = Match(
        1,
        "PL",
        57,
        "Arsenal FC",
        73,
        "Tottenham Hotspur FC",
        NOW + timedelta(minutes=minutes),
        "TIMED",
    )
    return replace(match, **changes)


def preferences(**changes):
    return replace(Preferences(favorite_team_ids={57}, notifications_enabled=True), **changes)


@pytest.fixture
def engine_factory(tmp_path):
    current = [NOW]
    messages = []
    warnings = []

    def notify(title, body):
        messages.append((title, body))
        return True

    def make(**kwargs):
        return ReminderEngine(
            tmp_path,
            clock=lambda: current[0],
            notify=kwargs.pop("notify", notify),
            warning=warnings.append,
            **kwargs,
        )

    return make, current, messages, warnings


@pytest.mark.parametrize("minutes", [10, 30, 60])
def test_lead_time_boundary_and_second_tick(engine_factory, minutes):
    make, current, messages, _ = engine_factory
    engine = make()
    match = fixture_match(minutes)
    prefs = preferences(notification_minutes=minutes)
    current[0] = NOW - timedelta(seconds=1)
    engine.check([match], prefs, False)
    assert not messages
    current[0] = NOW
    engine.check([match], prefs, False)
    engine.check([match], prefs, False)
    assert len(messages) == 1


@pytest.mark.parametrize("minutes", [0, -1])
def test_exact_kickoff_and_past_games_never_notify(engine_factory, minutes):
    make, _, messages, _ = engine_factory
    make().check([fixture_match(minutes)], preferences(), False)
    assert not messages


def test_sleep_wakeup_delivers_only_if_kickoff_still_future(engine_factory):
    make, current, messages, _ = engine_factory
    engine = make()
    match = fixture_match(60)
    engine.check([match], preferences(), False)
    current[0] += timedelta(minutes=55)
    engine.check([match], preferences(), False)
    assert len(messages) == 1
    current[0] += timedelta(minutes=10)
    engine.check([replace(match, id=2)], preferences(), False)
    assert len(messages) == 1


@pytest.mark.parametrize(
    "status",
    [
        "SCHEDULED",
        "POSTPONED",
        "CANCELED",
        "FINISHED",
        "IN_PLAY",
        "PAUSED",
        "SUSPENDED",
        "AWARDED",
        "UNKNOWN",
    ],
)
def test_unconfirmed_cancelled_or_started_statuses_never_notify(engine_factory, status):
    make, _, messages, _ = engine_factory
    make().check([fixture_match(status=status)], preferences(), False)
    assert not messages


def test_missing_kickoff_is_skipped(engine_factory):
    make, _, messages, _ = engine_factory
    make().check([fixture_match(utc_date=None)], preferences(), False)
    assert not messages


@pytest.mark.parametrize(
    "favorites,expected", [({57}, 1), ({73}, 1), ({57, 73}, 1), ({81}, 0), (set(), 0)]
)
def test_only_home_or_away_favorites_notify_once(engine_factory, favorites, expected):
    make, _, messages, _ = engine_factory
    make().check([fixture_match()], preferences(favorite_team_ids=favorites), False)
    assert len(messages) == expected


def test_disabled_does_not_deliver_or_create_history(engine_factory, tmp_path):
    make, _, messages, _ = engine_factory
    engine = make()
    engine.check([fixture_match()], preferences(notifications_enabled=False), False)
    assert not messages
    assert not engine.path.exists()


def test_history_survives_restart_and_lead_change(engine_factory):
    make, _, messages, _ = engine_factory
    match = fixture_match()
    make().check([match, match], preferences(notification_minutes=10), False)
    make().check([match], preferences(notification_minutes=60), False)
    assert len(messages) == 1


def test_changed_kickoff_can_remind_again_but_cancellation_cannot(engine_factory):
    make, _, messages, _ = engine_factory
    engine = make()
    engine.check([fixture_match()], preferences(), False)
    engine.check([fixture_match(15, status="CANCELED")], preferences(), False)
    assert len(messages) == 1
    engine.check([fixture_match(15)], preferences(), False)
    assert len(messages) == 2


def test_modes_are_separate_and_mock_is_labelled(engine_factory):
    make, _, messages, _ = engine_factory
    engine = make()
    engine.check([fixture_match()], preferences(), True)
    engine.check([fixture_match()], preferences(), False)
    assert len(messages) == 2
    assert messages[0][0].startswith("[데모]")
    assert "데모" not in messages[1][0]


def test_names_use_custom_then_korean_alias_and_korean_time(engine_factory):
    make, _, messages, _ = engine_factory
    make().check([fixture_match()], preferences(custom_team_names={57: "우리 아스널"}), False)
    assert "우리 아스널 vs 토트넘" in messages[0][1]
    assert "10월 22일 21:10 (KST)" in messages[0][1]
    assert "10분" in messages[0][1]


@pytest.mark.parametrize("raises", [False, True])
def test_failed_submission_is_not_recorded(engine_factory, raises):
    make, _, messages, warnings = engine_factory

    def fail(title, body):
        if raises:
            raise RuntimeError("sensitive-notifier-details")
        return False

    engine = make(notify=fail)
    engine.check([fixture_match()], preferences(), False)
    assert not engine.path.exists()
    assert len(warnings) == 1
    assert "sensitive" not in warnings[0]
    make().check([fixture_match()], preferences(), False)
    assert len(messages) == 1


@pytest.mark.parametrize(
    "payload",
    ["broken", "[]", '{"version":1,"entries":[{}]}', '{"version":true,"entries":[]}'],
    ids=["invalid-json", "wrong-root", "invalid-entry", "invalid-version"],
)
def test_corrupt_history_suspends_and_preserves_file(engine_factory, payload):
    make, _, messages, warnings = engine_factory
    engine = make()
    engine.path.write_text(payload)
    engine.check([fixture_match()], preferences(), False)
    engine.check([fixture_match()], preferences(), False)
    assert not messages
    assert len(warnings) == 1
    assert engine.path.read_text() == payload


def test_failed_atomic_write_stops_further_alerts_and_removes_temp_file(
    engine_factory, monkeypatch, tmp_path
):
    make, _, messages, warnings = engine_factory

    def fail(*args):
        raise PermissionError("private-file-details")

    monkeypatch.setattr(notifications.os, "replace", fail)
    engine = make()
    engine.check([fixture_match(), fixture_match(id=2)], preferences(), False)
    engine.check([fixture_match()], preferences(), False)
    assert len(messages) == 1
    assert len(warnings) == 1
    assert "다시 실행" in warnings[0]
    assert not list(tmp_path.iterdir())


def test_history_contains_only_identifiers_and_timestamps(engine_factory):
    make, _, _, _ = engine_factory
    engine = make()
    engine.check([fixture_match()], preferences(custom_team_names={57: "private-nickname"}), False)
    payload = json.loads(engine.path.read_text())
    assert payload["version"] == 1
    assert set(payload["entries"][0]) == {"mode", "match_id", "kickoff", "notified_at"}
    assert "private-nickname" not in engine.path.read_text()


def test_old_history_is_cleaned_and_capacity_does_not_forget_upcoming_alerts(
    engine_factory, monkeypatch
):
    make, current, messages, warnings = engine_factory
    monkeypatch.setattr(notifications, "_MAX_HISTORY", 2)
    engine = make()
    matches = [fixture_match(id=value) for value in (1, 2, 3)]
    engine.check(matches, preferences(), False)
    engine.check(matches, preferences(), False)
    assert len(messages) == 2
    assert len(json.loads(engine.path.read_text())["entries"]) == 2
    assert len(warnings) == 1
    current[0] += timedelta(days=8)
    engine.check([], preferences(), False)
    assert json.loads(engine.path.read_text())["entries"] == []


@pytest.fixture
def tray_environment(qtbot, monkeypatch, tmp_path):
    class FakeTray(QObject):
        activated = Signal(object)
        messageClicked = Signal()
        ActivationReason = QSystemTrayIcon.ActivationReason
        MessageIcon = QSystemTrayIcon.MessageIcon
        available = True
        messages_supported = True
        instances = []

        def __init__(self, parent):
            super().__init__(parent)
            self.visible = False
            self.messages = []
            self.instances.append(self)

        @classmethod
        def isSystemTrayAvailable(cls):
            return cls.available

        @classmethod
        def supportsMessages(cls):
            return cls.messages_supported

        def setIcon(self, icon):
            assert not icon.isNull()

        def setToolTip(self, text):
            self.tooltip = text

        def setContextMenu(self, menu):
            self.menu = menu

        def show(self):
            self.visible = True

        def hide(self):
            self.visible = False

        def showMessage(self, title, body, icon, timeout):
            self.messages.append((title, body))

    monkeypatch.setattr(notifications, "QSystemTrayIcon", FakeTray)
    controller = NotificationController(tmp_path, clock=lambda: NOW)
    yield controller, FakeTray
    controller.close()


def test_controller_submits_and_disabling_stops_timer(tray_environment):
    controller, tray_type = tray_environment
    controller.update([fixture_match()], preferences(), False)
    assert controller._timer.interval() == 15000
    assert controller._timer.isActive()
    assert len(tray_type.instances[0].messages) == 1
    controller.check()
    assert len(tray_type.instances[0].messages) == 1
    controller.update([fixture_match(id=2)], preferences(notifications_enabled=False), False)
    assert not controller._timer.isActive()
    assert not tray_type.instances[0].visible


@pytest.mark.parametrize("availability,messages_supported", [(False, True), (True, False)])
def test_unsupported_notifications_warn_without_writing_history(
    tray_environment, availability, messages_supported
):
    controller, tray_type = tray_environment
    tray_type.available = availability
    tray_type.messages_supported = messages_supported
    warnings = []
    controller.warnings.connect(warnings.append)
    controller.update([fixture_match()], preferences(), False)
    controller.check()
    assert len(warnings) == 1
    assert not controller._engine.path.exists()
    assert not tray_type.instances


def test_hide_restore_and_quit_actions_preserve_application_lifetime(tray_environment):
    controller, tray_type = tray_environment
    application = QApplication.instance()
    original = application.quitOnLastWindowClosed()
    restored, quit_requested = [], []
    controller.restore_requested.connect(lambda: restored.append(True))
    controller.quit_requested.connect(lambda: quit_requested.append(True))
    assert controller.hide_to_tray()
    assert not application.quitOnLastWindowClosed()
    tray = tray_type.instances[0]
    assert tray.visible
    assert [action.text() for action in tray.menu.actions()] == ["열기", "종료"]
    tray.menu.actions()[0].trigger()
    assert restored == [True]
    assert application.quitOnLastWindowClosed() == original
    assert controller.hide_to_tray()
    tray.menu.actions()[1].trigger()
    assert quit_requested == [True]
    controller.close()
    assert application.quitOnLastWindowClosed() == original
    assert not tray.visible
    assert not controller.hide_to_tray()


def test_unavailable_tray_does_not_change_quit_behavior(tray_environment):
    controller, tray_type = tray_environment
    original = QApplication.instance().quitOnLastWindowClosed()
    tray_type.available = False
    assert not controller.hide_to_tray()
    assert QApplication.instance().quitOnLastWindowClosed() == original


def test_closed_controller_cannot_restart_notifications(tray_environment):
    controller, tray_type = tray_environment
    controller.close()
    controller.update([fixture_match()], preferences(), False)
    controller.check()
    assert not controller._timer.isActive()
    assert not tray_type.instances


def test_engine_callbacks_do_not_retain_closed_qobject_until_cyclic_gc(qtbot, tmp_path):
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        controller = NotificationController(tmp_path)
        reference = weakref.ref(controller)
        engine = controller._engine
        controller.close()
        del controller
        assert reference() is None
        assert engine._notify("unused", "unused") is False
        engine._warning("unused")
    finally:
        if was_enabled:
            gc.enable()
