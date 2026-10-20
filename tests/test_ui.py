"""Exercise real Qt widgets and worker lifecycle without network or real credentials."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import QDialog, QLineEdit

from kickoff_desk import settings, ui
from kickoff_desk.cache import CacheStore
from kickoff_desk.mock import MockClient
from kickoff_desk.models import Match
from kickoff_desk.service import LoadResult, ScheduleService
from kickoff_desk.settings import KeyManager, Preferences, SettingsStore
from kickoff_desk.ui import FavoritesDialog, KeyDialog, MainWindow


@pytest.fixture(autouse=True)
def no_native_credentials(monkeypatch):
    monkeypatch.setattr(settings, "_native_backend", lambda: None)


@pytest.fixture
def window_factory(qtbot, tmp_path):
    windows = []

    def create(**kwargs):
        window = MainWindow(SettingsStore(tmp_path), auto_load=False, **kwargs)
        qtbot.addWidget(window)
        window.calendar.blockSignals(True)
        window.calendar.setSelectedDate(QDate(2026, 1, 15))
        window.calendar.blockSignals(False)
        window.show()
        windows.append(window)
        return window

    yield create
    for window in windows:
        qtbot.waitUntil(lambda: not window.is_loading, timeout=5000)


def load(qtbot, window, refresh=False):
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.load_month(refresh=refresh)


def table_values(table):
    return [
        [table.item(row, column).text() for column in range(table.columnCount())]
        for row in range(table.rowCount())
    ]


def test_mock_startup_never_reads_credentials(window_factory, qtbot, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Mock startup must not resolve credentials")

    monkeypatch.setattr(ui, "KeyManager", forbidden)
    window = window_factory(use_mock=True)
    load(qtbot, window)
    assert window.key_manager is None
    assert window.result.source == "mock"
    assert len(window.matches) > 90
    assert window.day_table.rowCount() == 3
    assert window.demo_label.isVisible()
    assert "KST" in window.last_refresh_label.text()
    assert window.calendar.minimumDate() == QDate(2000, 1, 1)
    assert window.calendar.maximumDate() == QDate(2099, 12, 31)


def test_calendar_date_selection_renders_korean_day(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    previous = window.day_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    window.calendar.setSelectedDate(QDate(2026, 1, 16))
    assert "2026년 1월 16일" in window.date_label.text()
    assert window.day_table.rowCount() == 3
    assert window.day_table.item(0, 0).data(Qt.ItemDataRole.UserRole) != previous
    assert not window.is_loading


def test_calendar_month_navigation_loads_and_selects_first_day(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.calendar.showNextMonth()
    assert window.result.month == 2
    assert window.calendar.selectedDate() == QDate(2026, 2, 1)
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.calendar.showPreviousMonth()
    assert window.result.month == 1
    assert window.result.source == "cache"
    assert "최신 여부" in window.source_label.text()


def test_league_and_favorite_filters_include_undated_section(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    window.competition_checkboxes["PD"].setChecked(False)
    window.competition_checkboxes["CL"].setChecked(False)
    assert window.day_table.rowCount() == 1
    assert window.day_table.item(0, 1).text() == "프리미어리그"
    assert window.undated_table.rowCount() == 0
    window.favorite_checkbox.setChecked(True)
    assert window.day_table.rowCount() == 0
    assert window.empty_label.isVisible()
    window.preferences.favorite_team_ids = {57}
    window._render_matches()
    assert window.day_table.rowCount() == 1
    window.competition_checkboxes["PL"].setChecked(False)
    assert window.day_table.rowCount() == 0


def test_unknown_dates_remain_visible_on_other_selected_day(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    assert window.undated_table.rowCount() == 1
    assert window.undated_table.item(0, 0).text() == "일정 미정"
    window.calendar.setSelectedDate(QDate(2026, 1, 25))
    assert window.undated_table.rowCount() == 1
    assert window.undated_table.isVisible()


def test_ui_converts_utc_and_never_exposes_scores(window_factory, qtbot, tmp_path):
    matches = [
        Match(
            1,
            "PL",
            57,
            "Arsenal",
            73,
            "Tottenham",
            datetime(2026, 1, 14, 18, 30, tzinfo=timezone.utc),
            "FINISHED",
        ),
        Match(
            2,
            "PD",
            81,
            "Barcelona",
            86,
            "Real Madrid",
            datetime(2026, 1, 14, 20, 0, tzinfo=timezone.utc),
            "SCHEDULED",
        ),
    ]

    class Provider:
        def fetch_matches(self, first, last):
            return matches

    window = window_factory(
        service_factory=lambda _: ScheduleService(Provider(), CacheStore(tmp_path, "mock"), "mock")
    )
    load(qtbot, window)
    rows = table_values(window.day_table)
    assert rows[0] == ["03:30", "프리미어리그", "Arsenal", "Tottenham", "경기 종료"]
    assert rows[1][0] == "시간 미확정"
    assert [window.day_table.horizontalHeaderItem(i).text() for i in range(5)] == [
        "시간 (KST)",
        "대회",
        "홈",
        "원정",
        "상태",
    ]


def test_refresh_bypasses_cache(window_factory, qtbot, tmp_path):
    class CountingProvider(MockClient):
        calls = 0

        def fetch_matches(self, first, last):
            self.calls += 1
            return super().fetch_matches(first, last)

    provider = CountingProvider()
    window = window_factory(
        service_factory=lambda _: ScheduleService(provider, CacheStore(tmp_path, "mock"), "mock")
    )
    load(qtbot, window)
    load(qtbot, window)
    assert provider.calls == 1
    assert window.result.source == "cache"
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        qtbot.mouseClick(window.refresh_button, Qt.MouseButton.LeftButton)
    assert provider.calls == 2
    assert window.result.source == "mock"


def test_live_without_key_cannot_read_mock_cache(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    assert window.day_table.rowCount() == 3
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(False)
    assert window.result.source == "empty"
    assert window.day_table.rowCount() == 0
    assert "API 키" in window.notice_label.text()
    assert not window.demo_label.isVisible()
    assert not window.settings_store.load().use_mock
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(True)
    assert window.result.source == "cache"
    assert window.day_table.rowCount() == 3


def test_loading_runs_in_background_and_only_once(window_factory, qtbot):
    entered = threading.Event()
    release = threading.Event()
    main_thread = threading.get_ident()

    class SlowService:
        calls = 0
        worker_thread = None

        def load_month(self, year, month, refresh):
            self.calls += 1
            self.worker_thread = threading.get_ident()
            entered.set()
            release.wait(timeout=3)
            return LoadResult(year, month, [], None, "mock")

    service = SlowService()
    window = window_factory(service_factory=lambda _: service)
    window.load_month()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.is_loading
        assert not window.calendar.isEnabled()
        assert not window.refresh_button.isEnabled()
        assert not window.mock_checkbox.isEnabled()
        assert not window.key_button.isEnabled()
        window.load_month(refresh=True)
        assert service.calls == 1
        assert service.worker_thread != main_thread
        responsive = []
        QTimer.singleShot(0, lambda: responsive.append(True))
        qtbot.waitUntil(lambda: bool(responsive))
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.is_loading)
    assert window.calendar.isEnabled()


def test_close_waits_for_worker_and_signal_observes_cleanup(window_factory, qtbot):
    entered = threading.Event()
    release = threading.Event()

    class SlowService:
        def load_month(self, year, month, refresh):
            entered.set()
            release.wait(timeout=3)
            return LoadResult(year, month, [], None, "mock")

    window = window_factory(service_factory=lambda _: SlowService())
    loading_on_completion = []
    window.load_completed.connect(lambda _: loading_on_completion.append(window.is_loading))
    window.load_month()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.close() is False
        assert window.isVisible()
        assert "요청을 마친 뒤 종료" in window.notice_label.text()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.is_loading)
    assert not window.isVisible()
    assert loading_on_completion == [False]


def test_unexpected_worker_error_is_safe(window_factory, qtbot):
    class BrokenService:
        def load_month(self, *args):
            raise RuntimeError("secret-token-example")

    window = window_factory(service_factory=lambda _: BrokenService())
    load(qtbot, window)
    assert window.result.source == "empty"
    assert "불러오지 못했습니다" in window.notice_label.text()
    assert "secret-token-example" not in window.notice_label.text()
    assert window.refresh_button.isEnabled()


def test_api_client_is_reused_across_mode_and_key_changes(window_factory, qtbot, monkeypatch):
    class FakeClient(MockClient):
        def __init__(self, key):
            self.key = key

        def set_api_key(self, key):
            self.key = key

    monkeypatch.setattr(ui, "ApiClient", FakeClient)
    window = window_factory(use_mock=False)
    manager = KeyManager(window.settings_store)
    manager.set_key("first-test-key")
    window.key_manager = manager
    load(qtbot, window)
    original = window._api_client
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(True)
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(False)
    assert window._api_client is original
    manager.set_key("replacement-test-key")
    window._key_changed()
    assert original.key == "replacement-test-key"
    manager.delete_key()
    window._key_changed()
    assert original.key == ""


@pytest.fixture
def key_dialog(qtbot, tmp_path):
    manager = KeyManager(SettingsStore(tmp_path))
    dialog = KeyDialog(manager)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_key_dialog_does_not_prefill_or_reveal_saved_key(key_dialog, qtbot):
    dialog = key_dialog
    assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.key_input.text() == ""
    assert not dialog.persist_checkbox.isEnabled()
    dialog.key_input.setText("test-key-do-not-echo")
    with qtbot.waitSignal(dialog.changed):
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.manager.get_key() == "test-key-do-not-echo"
    assert dialog.key_input.text() == ""
    assert "이번 실행" in dialog.source_label.text()
    assert "test-key-do-not-echo" not in dialog.source_label.text()
    assert "test-key-do-not-echo" not in dialog.message_label.text()
    assert "test-key-do-not-echo" not in dialog.manager.settings_store.path.read_text()


def test_key_dialog_clears_invalid_input_and_reports_error(key_dialog, qtbot):
    dialog = key_dialog
    dialog.key_input.setText("invalid key")
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.key_input.text() == ""
    assert dialog.message_label.isVisible()
    assert "공백 없는" in dialog.message_label.text()
    assert dialog.manager.get_key() is None


def test_key_dialog_environment_switch_and_delete(key_dialog, qtbot, monkeypatch):
    dialog = key_dialog
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "unit-environment")
    dialog.key_input.setText("unit-session")
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.manager.source == "session"
    qtbot.mouseClick(dialog.environment_button, Qt.MouseButton.LeftButton)
    assert dialog.manager.source == "environment"
    assert "환경변수" in dialog.source_label.text()
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    assert dialog.manager.get_key() is None
    assert "등록된 키 없음" in dialog.source_label.text()
    assert dialog.manager.settings_store.load().ignore_environment_api_key


def test_key_dialog_clears_input_on_cancel(key_dialog):
    key_dialog.key_input.setText("not-saved-test-key")
    key_dialog.reject()
    assert key_dialog.key_input.text() == ""


def test_vault_failure_is_safe_and_input_cleared(qtbot, tmp_path):
    class BrokenVault:
        def get_password(self, service, username):
            return None

        def set_password(self, *args):
            raise RuntimeError("sensitive-backend-details")

        def delete_password(self, *args):
            pass

    dialog = KeyDialog(KeyManager(SettingsStore(tmp_path), backend=BrokenVault()))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.persist_checkbox.setChecked(True)
    dialog.key_input.setText("test-persistent-key")
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.key_input.text() == ""
    assert "저장하지 못했습니다" in dialog.message_label.text()
    assert "sensitive" not in dialog.message_label.text()
    assert "test-persistent-key" not in dialog.message_label.text()


def test_favorites_dialog_retains_selection(qtbot):
    dialog = FavoritesDialog({57: "아스널", 65: "맨체스터 시티"}, {57})
    qtbot.addWidget(dialog)
    assert dialog.selected_teams() == {57: "아스널"}


def test_favorites_save_reloads_latest_settings(window_factory, qtbot, monkeypatch):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    manager = KeyManager(window.settings_store)
    manager.delete_key()
    assert not window.preferences.ignore_environment_api_key  # Window holds an older snapshot.

    def choose(dialog):
        for index in range(dialog.team_list.count()):
            item = dialog.team_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == 57:
                item.setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FavoritesDialog, "exec", choose)
    window._open_favorites()
    saved = window.settings_store.load()
    assert saved.ignore_environment_api_key
    assert saved.favorite_team_ids == {57}
    assert saved.favorite_team_names == {57: "아스널"}


def test_favorites_save_and_clear_survive_window_restart(window_factory, qtbot, monkeypatch):
    selected_ids = {57, 81}

    def choose(dialog):
        for index in range(dialog.team_list.count()):
            item = dialog.team_list.item(index)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in selected_ids
                else Qt.CheckState.Unchecked
            )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FavoritesDialog, "exec", choose)
    first = window_factory(use_mock=True)
    load(qtbot, first)
    first._open_favorites()
    first.favorite_checkbox.setChecked(True)
    assert first.day_table.rowCount() == 2
    assert first.close()

    reopened = window_factory(use_mock=True)
    assert reopened.settings_store.path == first.settings_store.path
    assert reopened.preferences.favorite_team_ids == {57, 81}
    assert reopened.preferences.favorite_team_names == {57: "아스널", 81: "바르셀로나"}
    assert "2개" in reopened.favorites_label.text()
    # Team choices persist; the temporary "favorites only" view starts unchecked.
    assert not reopened.favorite_checkbox.isChecked()
    load(qtbot, reopened)
    assert reopened.result.source == "cache"
    assert reopened.day_table.rowCount() == 3
    reopened.favorite_checkbox.setChecked(True)
    assert reopened.day_table.rowCount() == 2

    selected_ids.clear()
    reopened._open_favorites()
    assert reopened.day_table.rowCount() == 0
    assert reopened.close()

    cleared = window_factory(use_mock=True)
    assert cleared.preferences.favorite_team_ids == set()
    assert cleared.preferences.favorite_team_names == {}
    assert "0개" in cleared.favorites_label.text()


def test_favorites_retains_saved_teams_absent_from_month(window_factory, monkeypatch):
    window = window_factory(use_mock=True)
    window.preferences = Preferences(
        favorite_team_ids={999}, favorite_team_names={999: "저장된 팀"}
    )

    def inspect(dialog):
        assert dialog.selected_teams() == {999: "저장된 팀"}
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FavoritesDialog, "exec", inspect)
    window._open_favorites()


def test_mode_save_preserves_environment_disable(window_factory, qtbot):
    window = window_factory(use_mock=True)
    KeyManager(window.settings_store).delete_key()
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(False)
    assert window.settings_store.load().ignore_environment_api_key


def test_bad_settings_start_with_safe_default(qtbot, tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text("broken settings", encoding="utf-8")
    window = MainWindow(store, auto_load=False)
    qtbot.addWidget(window)
    window.show()
    assert window.use_mock
    assert "손상" in window.notice_label.text()
    assert window.key_manager is None
    load(qtbot, window)
    assert window.result.source == "mock"
    assert "손상" in window.notice_label.text()


def test_failed_mode_save_warning_survives_load(window_factory, qtbot, monkeypatch, tmp_path):
    window = window_factory(
        use_mock=True,
        service_factory=lambda _: ScheduleService(
            MockClient(), CacheStore(tmp_path, "mock"), "mock"
        ),
    )
    original_save = window.settings_store.save

    def failed_save(preferences):
        raise settings.SettingsError("설정을 저장하지 못했습니다.")

    monkeypatch.setattr(window.settings_store, "save", failed_save)
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(False)
    assert window.result.source == "mock"
    assert "설정을 저장하지 못했습니다" in window.notice_label.text()
    monkeypatch.setattr(window.settings_store, "save", original_save)
    with qtbot.waitSignal(window.load_completed, timeout=5000):
        window.mock_checkbox.setChecked(True)
    assert "설정을 저장하지 못했습니다" not in window.notice_label.text()
