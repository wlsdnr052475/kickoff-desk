"""Exercise real Qt widgets and worker lifecycle without network or real credentials."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDate, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QLineEdit

from kickoff_desk import settings, ui
from kickoff_desk.cache import CacheStore
from kickoff_desk.catalog import CatalogResult, Team
from kickoff_desk.mock import MockClient
from kickoff_desk.models import SEOUL, Match
from kickoff_desk.personalization import PersonalizationDialog
from kickoff_desk.service import LoadResult, RangeLoadResult, ScheduleService
from kickoff_desk.settings import KeyManager, Preferences, SettingsStore
from kickoff_desk.ui import FavoritesDialog, KeyDialog, MainWindow


@pytest.fixture(autouse=True)
def no_native_credentials(monkeypatch):
    monkeypatch.setattr(settings, "_native_backend", lambda: None)


@pytest.fixture
def crest_provider(qapp):
    class FakeCrestProvider(QObject):
        updated = Signal(int)

        def __init__(self):
            super().__init__()
            self.calls = []
            self.icons = {}
            self.close_calls = 0
            placeholder = QPixmap(24, 24)
            placeholder.fill(QColor("#718096"))
            self.placeholder = QIcon(placeholder)

        def icon(self, team_id, crest_url=None, *, allow_network=False):
            self.calls.append((team_id, crest_url, allow_network))
            return self.icons.get(team_id, self.placeholder)

        def update_icon(self, team_id):
            image = QPixmap(24, 24)
            image.fill(QColor("#cc3344"))
            self.icons[team_id] = QIcon(image)
            self.updated.emit(team_id)

        def close(self):
            self.close_calls += 1

    return FakeCrestProvider()


@pytest.fixture
def notification_controller(qapp):
    class FakeNotificationController(QObject):
        restore_requested = Signal()
        quit_requested = Signal()
        warnings = Signal(str)

        def __init__(self):
            super().__init__()
            self.updates = []
            self.close_calls = 0
            self.hide_calls = 0
            self.can_hide = False

        def update(self, matches, preferences, use_mock):
            self.updates.append((list(matches), set(preferences.favorite_team_ids), use_mock))

        def hide_to_tray(self):
            self.hide_calls += 1
            return self.can_hide

        def close(self):
            self.close_calls += 1

    return FakeNotificationController()


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
    assert rows[0] == ["03:30", "프리미어리그", "아스널", "토트넘", "경기 종료"]
    assert rows[1][0] == "시간 미확정"
    assert rows[1][2:4] == ["바르셀로나", "레알 마드리드"]
    assert window.day_table.item(0, 2).toolTip() == "Arsenal"
    assert window.day_table.item(0, 3).toolTip() == "Tottenham"
    assert matches[0].home_name == "Arsenal"
    assert matches[0].away_name == "Tottenham"
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


def test_close_waits_for_worker_and_signal_observes_cleanup(window_factory, qtbot, crest_provider):
    entered = threading.Event()
    release = threading.Event()

    class SlowService:
        def load_month(self, year, month, refresh):
            entered.set()
            release.wait(timeout=3)
            return LoadResult(year, month, [], None, "mock")

    window = window_factory(service_factory=lambda _: SlowService(), crest_provider=crest_provider)
    loading_on_completion = []
    window.load_completed.connect(lambda _: loading_on_completion.append(window.is_loading))
    window.load_month()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.close() is False
        assert window.isVisible()
        assert crest_provider.close_calls == 0
        assert "요청을 마친 뒤 종료" in window.notice_label.text()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.is_loading)
    assert not window.isVisible()
    assert loading_on_completion == [False]
    assert crest_provider.close_calls == 1


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


def test_api_client_is_reused_across_mode_and_key_changes(
    window_factory, qtbot, monkeypatch, crest_provider
):
    class FakeClient(MockClient):
        def __init__(self, key):
            self.key = key

        def set_api_key(self, key):
            self.key = key

    monkeypatch.setattr(ui, "ApiClient", FakeClient)
    window = window_factory(use_mock=False, crest_provider=crest_provider)
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
    # Both saved teams and the favorites-only view now survive a restart.
    assert reopened.favorite_checkbox.isChecked()
    load(qtbot, reopened)
    assert reopened.result.source == "cache"
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


def test_mock_fixture_tables_show_team_icons_and_preserve_full_names(window_factory, qtbot):
    window = window_factory(use_mock=True)
    load(qtbot, window)
    matches_by_id = {match.id: match for match in window.matches}
    for table in (window.day_table, window.undated_table):
        assert table.iconSize() == QSize(24, 24)
        assert table.layoutDirection() == Qt.LayoutDirection.LeftToRight
        for row in range(table.rowCount()):
            match = matches_by_id[table.item(row, 0).data(Qt.ItemDataRole.UserRole)]
            for column, name in ((2, match.home_name), (3, match.away_name)):
                item = table.item(row, column)
                assert not item.icon().isNull()
                assert item.text() == name
                assert item.toolTip() == name
                assert item.data(Qt.ItemDataRole.UserRole) == match.id
                assert item.textAlignment() & Qt.AlignmentFlag.AlignLeft


def test_mock_requests_icons_only_for_displayed_teams_without_network(
    window_factory, qtbot, crest_provider
):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    load(qtbot, window)
    assert crest_provider.calls
    assert all(not network for _, _, network in crest_provider.calls)
    window.competition_checkboxes["PD"].setChecked(False)
    window.competition_checkboxes["CL"].setChecked(False)
    crest_provider.calls.clear()
    window._render_matches()
    assert [team_id for team_id, _, _ in crest_provider.calls] == [57, 73]
    assert all(not network for _, _, network in crest_provider.calls)


@pytest.mark.parametrize("use_mock", [True, False])
def test_team_crest_urls_only_allow_network_in_live_mode(window_factory, crest_provider, use_mock):
    window = window_factory(use_mock=use_mock, crest_provider=crest_provider)
    window.matches = [
        Match(
            1,
            "PL",
            57,
            "아스널",
            73,
            "토트넘",
            None,
            "SCHEDULED",
            home_crest="https://crests.football-data.org/57.png",
            away_crest="https://crests.football-data.org/73.png",
        )
    ]
    window._render_matches()
    assert crest_provider.calls == [
        (57, "https://crests.football-data.org/57.png", not use_mock),
        (73, "https://crests.football-data.org/73.png", not use_mock),
    ]


def test_async_crests_update_both_tables_without_resetting_view(window_factory, crest_provider):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    window.matches = [
        Match(
            1,
            "PL",
            57,
            "아스널",
            73,
            "토트넘",
            datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            "TIMED",
        ),
        Match(2, "CL", 57, "아스널", None, "원정 팀 미정", None, "SCHEDULED"),
    ]
    window.preferences.favorite_team_ids = {57}
    window.favorite_checkbox.setChecked(True)
    window.competition_checkboxes["PD"].setChecked(False)
    window.day_table.selectRow(0)
    day_home = window.day_table.item(0, 2)
    undated_home = window.undated_table.item(0, 2)
    away_icon = window.day_table.item(0, 3).icon().cacheKey()
    previous = day_home.icon().cacheKey()
    crest_provider.calls.clear()
    crest_provider.update_icon(57)
    assert window.day_table.item(0, 2) is day_home
    assert window.undated_table.item(0, 2) is undated_home
    assert day_home.icon().cacheKey() != previous
    assert undated_home.icon().cacheKey() == day_home.icon().cacheKey()
    assert window.day_table.item(0, 3).icon().cacheKey() == away_icon
    assert window.day_table.selectionModel().selectedRows()[0].row() == 0
    assert window.favorite_checkbox.isChecked()
    assert not window.competition_checkboxes["PD"].isChecked()
    assert window.calendar.selectedDate() == QDate(2026, 1, 15)
    assert crest_provider.calls == [(57, None, False), (57, None, False)]


def test_favorites_icons_refresh_without_changing_checks(qtbot, crest_provider):
    dialog = FavoritesDialog(
        {57: "아스널", 999: "저장된 팀"},
        {57},
        crest_provider=crest_provider,
        crest_urls={57: "https://crests.football-data.org/57.png"},
    )
    qtbot.addWidget(dialog)
    assert dialog.team_list.iconSize() == QSize(24, 24)
    assert dialog.team_list.layoutDirection() == Qt.LayoutDirection.LeftToRight
    items = {
        dialog.team_list.item(index).data(Qt.ItemDataRole.UserRole): dialog.team_list.item(index)
        for index in range(dialog.team_list.count())
    }
    assert not items[57].icon().isNull()
    assert not items[999].icon().isNull()
    assert items[57].text() == "아스널"
    assert items[57].toolTip() == "아스널"
    before = items[57].icon().cacheKey()
    other = items[999].icon().cacheKey()
    dialog.team_list.setCurrentItem(items[57])
    crest_provider.update_icon(57)
    assert items[57].icon().cacheKey() != before
    assert items[999].icon().cacheKey() == other
    assert dialog.selected_teams() == {57: "아스널"}
    assert dialog.team_list.currentItem() is items[57]
    assert all(not network for _, _, network in crest_provider.calls)


def test_favorites_pass_loaded_crest_urls_and_restore_absent_team_icons(
    window_factory, monkeypatch, crest_provider
):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    window.preferences = Preferences(
        favorite_team_ids={999}, favorite_team_names={999: "저장된 팀"}
    )
    window.matches = [
        Match(
            1,
            "PL",
            57,
            "아스널",
            73,
            "토트넘",
            None,
            "SCHEDULED",
            home_crest="https://crests.football-data.org/57.png",
            away_crest="https://crests.football-data.org/73.png",
        )
    ]

    def inspect(dialog):
        assert dialog.crest_provider is crest_provider
        assert dialog.crest_urls == {
            57: "https://crests.football-data.org/57.png",
            73: "https://crests.football-data.org/73.png",
        }
        assert dialog.selected_teams() == {999: "저장된 팀"}
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FavoritesDialog, "exec", inspect)
    window._open_favorites()
    assert (999, None, False) in crest_provider.calls
    assert all(not network for _, _, network in crest_provider.calls)


def test_main_window_closes_crest_provider(window_factory, crest_provider):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    assert window.close()
    assert crest_provider.close_calls == 1


@pytest.mark.parametrize("undated", [False, True])
def test_localized_team_names_keep_original_tooltips_and_unknown_fallback(
    window_factory, crest_provider, undated
):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    kickoff = None if undated else datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    window.matches = [
        Match(1, "CL", 5, "FC Bayern München", 65, "Manchester City FC", kickoff, "TIMED"),
        Match(2, "PL", 9001, "Example United FC", None, "원정 팀 미정", kickoff, "SCHEDULED"),
    ]
    window._render_matches()
    table = window.undated_table if undated else window.day_table
    assert table.item(0, 2).text() == "바이에른 뮌헨"
    assert table.item(0, 3).text() == "맨체스터 시티"
    assert table.item(0, 2).toolTip() == "FC Bayern München"
    assert table.item(0, 3).toolTip() == "Manchester City FC"
    assert table.item(1, 2).text() == "Example United FC"
    assert table.item(1, 3).text() == "원정 팀 미정"
    assert not table.item(0, 2).icon().isNull()
    assert window.matches[0].home_name == "FC Bayern München"


def test_saved_english_favorites_display_in_korean_after_restart(
    window_factory, tmp_path, monkeypatch, crest_provider
):
    SettingsStore(tmp_path).save(
        Preferences(
            favorite_team_ids={57, 9001},
            favorite_team_names={57: "Arsenal FC", 9001: "Example United FC"},
        )
    )
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    assert window.preferences.favorite_team_names[57] == "Arsenal FC"

    def inspect(dialog):
        items = {
            dialog.team_list.item(index).data(Qt.ItemDataRole.UserRole): dialog.team_list.item(
                index
            )
            for index in range(dialog.team_list.count())
        }
        assert items[57].text() == "아스널"
        assert items[57].toolTip() == "Arsenal FC"
        assert items[9001].text() == "Example United FC"
        assert dialog.selected_teams() == {57: "Arsenal FC", 9001: "Example United FC"}
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FavoritesDialog, "exec", inspect)
    window._open_favorites()
    assert window.close()
    reopened = window_factory(use_mock=True, crest_provider=crest_provider)
    assert reopened.preferences.favorite_team_ids == {57, 9001}
    assert reopened.preferences.favorite_team_names == {57: "Arsenal FC", 9001: "Example United FC"}


def test_korean_display_names_do_not_change_favorite_matching(window_factory, crest_provider):
    window = window_factory(use_mock=True, crest_provider=crest_provider)
    kickoff = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    window.matches = [
        Match(1, "PL", 57, "Arsenal FC", 73, "Tottenham Hotspur FC", kickoff, "TIMED"),
        Match(2, "PD", 81, "FC Barcelona", 86, "Real Madrid CF", kickoff, "TIMED"),
    ]
    window.preferences.favorite_team_ids = {57}
    window.favorite_checkbox.setChecked(True)
    assert window.day_table.rowCount() == 1
    assert window.day_table.item(0, 2).text() == "아스널"
    assert window.day_table.item(0, 3).text() == "토트넘"
    assert window.day_table.item(0, 2).data(Qt.ItemDataRole.UserRole) == 1


def test_today_and_tomorrow_use_korean_calendar_dates(window_factory, qtbot):
    def clock():
        return datetime(2026, 1, 31, 18, 0, tzinfo=timezone.utc)

    window = window_factory(use_mock=True, clock=clock)
    with qtbot.waitSignal(window.load_completed):
        window.today_button.click()
    assert window.calendar.selectedDate() == QDate(2026, 2, 1)
    with qtbot.waitSignal(window.load_completed):
        window.tomorrow_button.click()
    assert window.calendar.selectedDate() == QDate(2026, 2, 2)
    assert window.result.month == 2


def test_week_view_crosses_month_boundary_and_refreshes_same_range(window_factory, qtbot):
    window = window_factory(use_mock=True, clock=lambda: datetime(2026, 2, 1, 10, 0, tzinfo=SEOUL))
    with qtbot.waitSignal(window.load_completed):
        window.week_button.click()
    assert isinstance(window.result, RangeLoadResult)
    assert (window.result.start, window.result.end) == (date(2026, 1, 26), date(2026, 2, 1))
    assert window.day_table.rowCount() == 21
    assert "이번 주" in window.date_label.text()
    assert window.day_table.item(0, 0).text().startswith("01/26\n")
    assert window.day_table.item(20, 0).text().startswith("02/01\n")
    with qtbot.waitSignal(window.load_completed):
        window.refresh_button.click()
    assert window._view_mode == "week"
    assert window.result.start == date(2026, 1, 26)
    with qtbot.waitSignal(window.load_completed):
        window.date_button.click()
    assert window._view_mode == "day"
    assert isinstance(window.result, LoadResult)
    assert window.day_table.horizontalHeaderItem(0).text() == "시간 (KST)"


def test_next_favorite_searches_in_order_ignores_past_and_uncertain_games(
    window_factory, qtbot, notification_controller
):
    now = datetime(2026, 1, 31, 20, 0, tzinfo=SEOUL)

    class Service:
        calls = []

        def load_month(self, year, month, refresh=False):
            self.calls.append((year, month))
            matches = (
                [
                    Match(
                        1, "PL", 57, "Arsenal", 73, "Tottenham", now - timedelta(hours=1), "TIMED"
                    ),
                    Match(
                        2,
                        "PL",
                        57,
                        "Arsenal",
                        73,
                        "Tottenham",
                        now + timedelta(hours=1),
                        "SCHEDULED",
                    ),
                ]
                if month == 1
                else [
                    Match(3, "PL", 57, "Arsenal", 73, "Tottenham", now + timedelta(days=2), "TIMED")
                ]
            )
            return LoadResult(year, month, matches, now, "mock")

    service = Service()
    window = window_factory(
        use_mock=True,
        clock=lambda: now,
        service_factory=lambda _: service,
        notification_controller=notification_controller,
    )
    window.preferences.favorite_team_ids = {57}
    with qtbot.waitSignal(window.load_completed):
        window.next_favorite_button.click()
    assert service.calls == [(2026, 1), (2026, 2)]
    assert window.calendar.selectedDate() == QDate(2026, 2, 2)
    assert window.day_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == 3
    assert window.day_table.selectionModel().selectedRows()[0].row() == 0
    assert "90일" in window.notice_label.text()


def test_next_favorite_uses_latest_cached_reschedule(window_factory, qtbot):
    now = datetime(2026, 10, 29, 12, 0, tzinfo=SEOUL)
    old = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 10, 31, tzinfo=SEOUL), "TIMED"
    )
    moved = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 11, 15, tzinfo=SEOUL), "TIMED"
    )

    class Service:
        calls = []

        def cached_versions(self, start, end):
            return {1: (now - timedelta(days=1), moved)}

        def load_month(self, year, month, refresh=False):
            self.calls.append(month)
            return LoadResult(
                year,
                month,
                [old if month == 10 else moved],
                now - timedelta(days=5 if month == 10 else 1),
                "cache",
                True,
            )

    service = Service()
    window = window_factory(use_mock=True, clock=lambda: now, service_factory=lambda _: service)
    window.preferences.favorite_team_ids = {57}
    with qtbot.waitSignal(window.load_completed):
        window._find_next_favorite()
    assert service.calls == [10, 11]
    assert window.calendar.selectedDate() == QDate(2026, 11, 15)


def test_next_favorite_limit_and_league_filter(window_factory, qtbot):
    now = datetime(2026, 1, 31, 10, 0, tzinfo=SEOUL)

    class Service:
        calls = []

        def load_month(self, year, month, refresh=False):
            self.calls.append((year, month))
            matches = [
                Match(1, "CL", 57, "Arsenal", 73, "Tottenham", now + timedelta(days=1), "TIMED"),
                Match(2, "PL", 57, "Arsenal", 73, "Tottenham", now + timedelta(days=91), "TIMED"),
            ]
            return LoadResult(year, month, matches, now, "mock")

    service = Service()
    window = window_factory(use_mock=True, clock=lambda: now, service_factory=lambda _: service)
    window.preferences.favorite_team_ids = {57}
    window.competition_checkboxes["CL"].setChecked(False)
    with qtbot.waitSignal(window.load_completed):
        window._find_next_favorite()
    assert service.calls == [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]
    assert "90일" in window.notice_label.text()
    assert "없습니다" in window.notice_label.text()


def test_next_favorite_without_team_does_not_start_requests(window_factory):
    window = window_factory(use_mock=True)
    window.next_favorite_button.click()
    assert not window.is_loading
    assert "팀을 먼저 선택" in window.notice_label.text()


def test_catalog_load_is_async_and_preserves_favorite_selection(window_factory, qtbot):
    entered = threading.Event()
    release = threading.Event()

    class Catalog:
        def load(self, refresh=False):
            entered.set()
            release.wait(timeout=3)
            return CatalogResult([Team(64, "Liverpool FC", competitions=("PL",))], None, "mock")

    window = window_factory(use_mock=True, catalog_factory=lambda _: Catalog())
    dialog = FavoritesDialog({57: "아스널"}, {57}, window)
    qtbot.addWidget(dialog)
    window._favorites_dialog = dialog
    window._start_catalog()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.is_loading
        assert not window.key_button.isEnabled()
        assert not window.mock_checkbox.isEnabled()
        assert not window.refresh_button.isEnabled()
        assert dialog.selected_teams() == {57: "아스널"}
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.is_loading)
    assert {
        dialog.team_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.team_list.count())
    } == {57, 64}
    assert dialog.selected_teams() == {57: "아스널"}
    assert window.key_button.isEnabled()
    window._favorites_dialog = None


def test_close_waits_for_catalog_worker(window_factory, qtbot, notification_controller):
    entered = threading.Event()
    release = threading.Event()

    class Catalog:
        def load(self, refresh=False):
            entered.set()
            release.wait(timeout=3)
            return CatalogResult([], None, "mock")

    window = window_factory(
        use_mock=True,
        catalog_factory=lambda _: Catalog(),
        notification_controller=notification_controller,
    )
    dialog = FavoritesDialog({}, set(), window)
    qtbot.addWidget(dialog)
    window._favorites_dialog = dialog
    window._start_catalog()
    try:
        qtbot.waitUntil(entered.is_set)
        assert not window.close()
        assert notification_controller.close_calls == 0
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.is_loading)
    assert not window.isVisible()
    assert notification_controller.close_calls == 1


def test_filters_and_geometry_restore_without_overwriting_key_preferences(window_factory):
    window = window_factory(use_mock=True)
    KeyManager(window.settings_store).delete_key()
    window.competition_checkboxes["PD"].setChecked(False)
    window.favorite_checkbox.setChecked(True)
    assert window.close()
    saved = window.settings_store.load()
    assert saved.ignore_environment_api_key
    assert saved.window_geometry
    reopened = window_factory(use_mock=True)
    assert not reopened.competition_checkboxes["PD"].isChecked()
    assert reopened.favorite_checkbox.isChecked()
    assert reopened.preferences.selected_competitions == {"PL", "CL"}


def test_personalization_updates_theme_font_and_notification_preferences(
    window_factory, monkeypatch, notification_controller
):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    KeyManager(window.settings_store).delete_key()

    def choose(dialog):
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        dialog.font_size_spin.setValue(20)
        dialog.notifications_checkbox.setChecked(True)
        dialog.notification_minutes_combo.setCurrentIndex(
            dialog.notification_minutes_combo.findData(60)
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PersonalizationDialog, "exec", choose)
    window._open_personalization()
    saved = window.settings_store.load()
    assert saved.theme == "dark" and saved.font_size == 20
    assert saved.notifications_enabled and saved.notification_minutes == 60
    assert saved.ignore_environment_api_key
    assert "20px" in window.styleSheet()
    assert "#18232e" in window.styleSheet()
    assert notification_controller.updates[-1][2]


def test_custom_team_name_applies_to_fixtures_and_survives_restart(window_factory, monkeypatch):
    window = window_factory(use_mock=True)
    window.preferences.favorite_team_ids = {57}
    window.preferences.favorite_team_names = {57: "Arsenal FC"}

    def choose(dialog):
        dialog.team_list.setCurrentRow(0)
        dialog.nickname_input.setText("우리 팀")
        dialog.rename_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FavoritesDialog, "exec", choose)
    window._open_favorites()
    assert window._display_team_name(57, "Arsenal FC") == "우리 팀"
    assert window.close()
    reopened = window_factory(use_mock=True)
    assert reopened._display_team_name(57, "Arsenal FC") == "우리 팀"
    assert reopened.settings_store.load().favorite_team_ids == {57}


def test_notifications_retain_upcoming_data_when_browsing_past(
    window_factory, notification_controller
):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    future = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 3, 2, tzinfo=SEOUL), "TIMED"
    )
    window._remember_matches(LoadResult(2026, 3, [future], None, "mock"))
    window._remember_matches(LoadResult(2025, 12, [], None, "mock"))
    assert notification_controller.updates[-1][0] == [future]
    canceled = Match(1, "PL", 57, "Arsenal", 73, "Tottenham", future.utc_date, "CANCELED")
    window._remember_matches(LoadResult(2026, 3, [canceled], None, "mock"))
    assert notification_controller.updates[-1][0] == [canceled]


def test_older_month_cache_cannot_roll_back_newer_notification_schedule(
    window_factory, notification_controller
):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    newer = datetime(2026, 10, 22, tzinfo=SEOUL)
    older = datetime(2026, 10, 20, tzinfo=SEOUL)
    moved = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 11, 15, tzinfo=SEOUL), "TIMED"
    )
    old = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 10, 31, tzinfo=SEOUL), "TIMED"
    )
    window._remember_matches(LoadResult(2026, 11, [moved], newer, "cache", True))
    window._remember_matches(LoadResult(2026, 10, [old], older, "cache", True))
    assert notification_controller.updates[-1][0] == [moved]
    window._remember_matches(LoadResult(2026, 11, [], older, "cache", True))
    assert notification_controller.updates[-1][0] == [moved]
    window._remember_matches(LoadResult(2026, 11, [], newer + timedelta(days=1), "mock"))
    assert notification_controller.updates[-1][0] == []


def test_notification_range_uses_per_match_time_instead_of_oldest_month(
    window_factory, notification_controller
):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    old_stamp = datetime(2026, 1, 10, tzinfo=SEOUL)
    fresh_stamp = datetime(2026, 1, 20, tzinfo=SEOUL)
    original = Match(
        1, "PL", 57, "Arsenal", 73, "Tottenham", datetime(2026, 2, 1, tzinfo=SEOUL), "TIMED"
    )
    canceled = Match(1, "PL", 57, "Arsenal", 73, "Tottenham", original.utc_date, "CANCELED")
    window._remember_matches(
        LoadResult(2026, 2, [original], fresh_stamp - timedelta(days=1), "cache", True)
    )
    result = RangeLoadResult(
        date(2026, 1, 26),
        date(2026, 2, 1),
        [canceled],
        old_stamp,
        "cache",
        True,
        match_fetched_at={1: fresh_stamp},
        month_fetched_at={(2026, 1): old_stamp, (2026, 2): fresh_stamp},
    )
    window._remember_matches(result)
    assert notification_controller.updates[-1][0] == [canceled]


def test_notification_warning_survives_schedule_loading(
    window_factory, notification_controller, qtbot
):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    window.preferences.notifications_enabled = True
    notification_controller.warnings.emit("이 환경에서는 알림을 표시할 수 없습니다.")
    load(qtbot, window)
    assert window.notification_status_label.isVisible()
    assert "알림을 표시할 수 없습니다" in window.notification_status_label.text()


def test_close_to_tray_restore_and_real_quit(window_factory, notification_controller):
    window = window_factory(use_mock=True, notification_controller=notification_controller)
    window.preferences.minimize_to_tray = True
    notification_controller.can_hide = True
    assert not window.close()
    assert not window.isVisible()
    assert notification_controller.close_calls == 0
    notification_controller.restore_requested.emit()
    assert window.isVisible()
    window._quit()
    assert notification_controller.close_calls == 1
    window.close()
    assert notification_controller.hide_calls == 1
