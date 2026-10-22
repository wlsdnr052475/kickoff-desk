"""Native, score-free Qt calendar and safe user-controlled API key settings."""

from __future__ import annotations

import calendar as calendar_dates
from collections.abc import Callable
from datetime import date, datetime, timedelta

from PySide6.QtCore import QByteArray, QDate, QLocale, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient, ApiError
from .cache import CacheStore
from .catalog import CatalogResult, Team, TeamCatalog
from .favorites import FavoritesDialog
from .logos import CrestProvider
from .mock import MockClient
from .models import COMPETITIONS, SEOUL, Match, filter_matches
from .notifications import NotificationController
from .personalization import PersonalizationDialog, theme_stylesheet
from .service import LoadResult, RangeLoadResult, ScheduleService
from .settings import CredentialError, KeyManager, Preferences, SettingsError, SettingsStore
from .teams import display_team_name

_TEAM_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_CREST_URL_ROLE = Qt.ItemDataRole.UserRole + 2


class MonthWorker(QThread):
    """Keep all HTTP and cache work off the Qt event loop."""

    def __init__(self, service: ScheduleService, year: int, month: int, refresh: bool):
        super().__init__()
        self.service = service
        self.year = year
        self.month = month
        self.refresh = refresh
        self.result: LoadResult | None = None

    def run(self) -> None:
        try:
            self.result = self.service.load_month(self.year, self.month, self.refresh)
        except Exception:
            # Never expose exception text: third-party failures can contain secrets.
            self.result = LoadResult(
                self.year,
                self.month,
                [],
                None,
                "empty",
                True,
                "일정을 불러오지 못했습니다. 설정과 연결 상태를 확인하고 다시 시도해 주세요.",
            )


class RangeWorker(QThread):
    def __init__(self, service: ScheduleService, start: date, end: date, refresh: bool = False):
        super().__init__()
        self.service = service
        self.start_date = start
        self.end_date = end
        self.refresh = refresh
        self.result: RangeLoadResult | None = None

    def run(self) -> None:
        try:
            self.result = self.service.load_range(self.start_date, self.end_date, self.refresh)
        except Exception:
            self.result = RangeLoadResult(
                self.start_date,
                self.end_date,
                [],
                None,
                "empty",
                True,
                "선택한 기간의 일정을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )


class NextFavoriteWorker(RangeWorker):
    """Search cache-first in calendar order and stop as soon as a future game is found."""

    def __init__(
        self,
        service: ScheduleService,
        now: datetime,
        favorite_ids: set[int],
        competitions: set[str],
    ):
        super().__init__(
            service, now.astimezone(SEOUL).date(), now.astimezone(SEOUL).date() + timedelta(days=90)
        )
        self.now = now
        self.favorite_ids = set(favorite_ids)
        self.competitions = set(competitions)
        self.next_match: Match | None = None

    def run(self) -> None:
        results: list[LoadResult] = []
        current = self.start_date.replace(day=1)
        scanned_end = self.start_date
        try:
            versions = (
                self.service.cached_versions(self.start_date, self.end_date)
                if hasattr(self.service, "cached_versions")
                else {}
            )
            while current <= self.end_date:
                loaded = self.service.load_month(current.year, current.month)
                results.append(loaded)
                for match in loaded.matches:
                    previous = versions.get(match.id)
                    if previous is None or (loaded.fetched_at and loaded.fetched_at >= previous[0]):
                        versions[match.id] = (loaded.fetched_at or self.now, match)
                month_end = current.replace(
                    day=calendar_dates.monthrange(current.year, current.month)[1]
                )
                scanned_end = min(month_end, self.end_date)
                if loaded.source == "empty":
                    # An unseen earlier month prevents a trustworthy "next" result.
                    break
                candidates = filter_matches(
                    [
                        match
                        for _, match in versions.values()
                        if match.status == "TIMED"
                        and match.utc_date is not None
                        and match.utc_date > self.now
                        and match.local_date is not None
                        and match.local_date <= scanned_end
                    ],
                    competitions=self.competitions,
                    favorite_team_ids=self.favorite_ids,
                )
                if candidates:
                    self.next_match = candidates[0]
                    break
                if loaded.warning and loaded.stale:
                    # A quota/network failure must stay visible instead of triggering more requests.
                    if "요청 한도" in loaded.warning or "API 인증" in loaded.warning:
                        break
                current = month_end + timedelta(days=1)
            matches = {
                match.id: match
                for _, match in versions.values()
                if match.local_date is None or self.start_date <= match.local_date <= scanned_end
            }
            dates = [result.fetched_at for result in results if result.fetched_at]
            source = "empty"
            if any(result.source != "empty" for result in results):
                source = "cache" if any(result.stale for result in results) else results[-1].source
            self.result = RangeLoadResult(
                self.start_date,
                scanned_end,
                filter_matches(matches.values()),
                min(dates) if dates else None,
                source,
                any(result.stale for result in results),
                " ".join(dict.fromkeys(result.warning for result in results if result.warning)),
                match_fetched_at={identifier: versions[identifier][0] for identifier in matches},
                month_fetched_at={
                    (result.year, result.month): result.fetched_at
                    for result in results
                    if result.fetched_at is not None
                },
            )
        except Exception:
            self.result = RangeLoadResult(
                self.start_date,
                scanned_end,
                [],
                None,
                "empty",
                True,
                "다음 관심 팀 경기를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )


class CatalogWorker(QThread):
    def __init__(self, catalog: TeamCatalog, refresh: bool = False):
        super().__init__()
        self.catalog = catalog
        self.refresh = refresh
        self.result: CatalogResult | None = None

    def run(self) -> None:
        try:
            self.result = self.catalog.load(self.refresh)
        except Exception:
            self.result = CatalogResult([], None, "empty", True, "팀 목록을 불러오지 못했습니다.")


class KeyDialog(QDialog):
    """Keys are write-only in the UI and are never copied into labels."""

    changed = Signal()

    def __init__(self, manager: KeyManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("API 키 설정 — Kickoff Desk")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel("football-data.org API 키를 등록하거나 변경할 수 있습니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source_label = QLabel()
        self.source_label.setObjectName("metadata")
        layout.addWidget(self.source_label)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("새 API 키 입력 (기존 키는 표시하지 않습니다)")
        self.key_input.setAccessibleName("새 API 키")
        layout.addWidget(self.key_input)
        self.persist_checkbox = QCheckBox("Windows 자격 증명 관리자에 안전하게 저장")
        self.persist_checkbox.setEnabled(manager.can_persist)
        layout.addWidget(self.persist_checkbox)
        storage = QLabel(
            "체크하지 않으면 이번 실행 중에만 사용합니다."
            if manager.can_persist
            else "이 환경에서는 이번 실행 중에만 키를 사용할 수 있습니다."
        )
        storage.setObjectName("metadata")
        storage.setWordWrap(True)
        layout.addWidget(storage)
        self.message_label = QLabel()
        self.message_label.setObjectName("notice")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        layout.addWidget(self.message_label)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("키 등록 / 변경")
        self.save_button.setObjectName("primary")
        self.delete_button = QPushButton("키 삭제")
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)
        self.environment_button = QPushButton("FOOTBALL_DATA_API_KEY 환경변수 사용")
        layout.addWidget(self.environment_button)
        explanation = QLabel(
            "키 삭제는 저장된 키와 현재 실행의 키를 제거하고 환경변수 사용을 해제합니다. "
            "환경변수 사용을 선택하면 등록한 키를 지우고 환경변수로 전환합니다."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("metadata")
        layout.addWidget(explanation)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.save_button.clicked.connect(self._save)
        self.delete_button.clicked.connect(lambda: self._change(self.manager.delete_key))
        self.environment_button.clicked.connect(
            lambda: self._change(self.manager.use_environment_key)
        )
        self._update_source()

    def _update_source(self) -> None:
        labels = {
            "session": "이번 실행에 등록한 키",
            "credential_store": "Windows 자격 증명 관리자",
            "environment": "FOOTBALL_DATA_API_KEY 환경변수",
            "none": "등록된 키 없음",
        }
        try:
            self.source_label.setText("현재 키: " + labels[self.manager.source])
        except (CredentialError, SettingsError) as exc:
            self.source_label.setText("현재 키: 확인할 수 없음")
            self._message(str(exc))

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.show()

    def _save(self) -> None:
        self._change(
            lambda: self.manager.set_key(
                self.key_input.text(), persist=self.persist_checkbox.isChecked()
            )
        )

    def _change(self, action: Callable[[], None]) -> None:
        self.message_label.hide()
        try:
            action()
        except (CredentialError, SettingsError) as exc:
            self._message(str(exc))
        else:
            self._message("API 키 설정을 변경했습니다.")
            self.changed.emit()
        finally:
            self.key_input.clear()
        self._update_source()

    def done(self, result: int) -> None:
        self.key_input.clear()
        super().done(result)


class MainWindow(QMainWindow):
    load_completed = Signal(object)

    def __init__(
        self,
        settings_store: SettingsStore,
        key_manager: KeyManager | None = None,
        use_mock: bool | None = None,
        auto_load: bool = True,
        service_factory: Callable[[bool], ScheduleService] | None = None,
        crest_provider: CrestProvider | None = None,
        catalog_factory: Callable[[bool], TeamCatalog] | None = None,
        notification_controller: NotificationController | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        super().__init__()
        self.settings_store = settings_store
        self.key_manager = key_manager
        self.service_factory = service_factory
        self.catalog_factory = catalog_factory
        self._clock = clock or (lambda: datetime.now(SEOUL))
        self.crest_provider = (
            crest_provider
            if crest_provider is not None
            else CrestProvider(settings_store.directory, self)
        )
        self._api_client: ApiClient | None = None
        self._mock_client = MockClient()
        self._worker: MonthWorker | RangeWorker | None = None
        self._catalog_worker: CatalogWorker | None = None
        self._catalog_teams: list[Team] = []
        self._favorites_dialog: FavoritesDialog | None = None
        self._pending_catalog_refresh: bool | None = None
        self._notification_matches: dict[int, Match] = {}
        self._notification_versions: dict[int, datetime | None] = {}
        self._view_mode = "day"
        self._view_start: date | None = None
        self._view_end: date | None = None
        self._close_requested = False
        self._force_quit = False
        self._resources_closed = False
        self._load_warning = ""
        self.matches: list[Match] = []
        self.result: LoadResult | RangeLoadResult | None = None
        self._marked_dates: set[QDate] = set()
        preference_warning = ""
        try:
            self.preferences = settings_store.load()
        except SettingsError as exc:
            self.preferences = Preferences()
            preference_warning = str(exc)
        self._settings_warning = preference_warning
        self._notification_warning = ""
        self.use_mock = self.preferences.use_mock if use_mock is None else use_mock
        self.notification_controller = (
            notification_controller
            if notification_controller is not None
            else NotificationController(settings_store.directory, self, clock=self._clock)
        )
        self.setWindowTitle("Kickoff Desk — 축구 일정")
        self.resize(1180, 790)
        self.setMinimumSize(920, 650)
        self._build_ui()
        self._apply_appearance()
        self._restore_geometry()
        self.crest_provider.updated.connect(self._refresh_team_icons)
        self.notification_controller.restore_requested.connect(self._restore_from_tray)
        self.notification_controller.quit_requested.connect(self._quit)
        self.notification_controller.warnings.connect(self._show_notification_warning)
        self._update_notifications()
        self._render_matches()
        self._update_mode_label()
        if preference_warning:
            self._show_notice(preference_warning)
        if auto_load:
            QTimer.singleShot(0, self.load_month)

    @property
    def is_loading(self) -> bool:
        return self._worker is not None or self._catalog_worker is not None

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(13)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Kickoff Desk")
        title.setObjectName("title")
        heading.addWidget(title)
        subtitle = QLabel("세 대회의 일정을 한곳에 · 모든 날짜와 시간은 한국시간(KST)")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("subtitle")
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        self.key_button = QPushButton("API 키 설정")
        self.key_button.clicked.connect(self._open_key_settings)
        header.addWidget(self.key_button)
        self.settings_button = QPushButton("화면 · 알림 설정")
        self.settings_button.clicked.connect(self._open_personalization)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        self.mock_checkbox = QCheckBox("Mock 데모 모드")
        self.mock_checkbox.setChecked(self.use_mock)
        self.mock_checkbox.toggled.connect(self._mode_changed)
        toolbar.addWidget(self.mock_checkbox)
        toolbar.addStretch()
        self.source_label = QLabel("아직 불러오지 않음")
        self.source_label.setObjectName("metadata")
        toolbar.addWidget(self.source_label)
        self.refresh_button = QPushButton("일정 새로고침")
        self.refresh_button.setObjectName("primary")
        self.refresh_button.clicked.connect(self._refresh_current)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)
        self.demo_label = QLabel("데모 데이터 · 실제 경기 일정이 아닌 가상의 예시입니다.")
        self.demo_label.setObjectName("demo")
        layout.addWidget(self.demo_label)
        self.notice_label = QLabel()
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName("notice")
        self.notice_label.hide()
        layout.addWidget(self.notice_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.splitter = split
        split.setChildrenCollapsible(False)
        side = QFrame()
        side.setObjectName("panel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 14, 14, 14)
        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Language.Korean, QLocale.Country.SouthKorea))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setMinimumDate(QDate(2000, 1, 1))
        self.calendar.setMaximumDate(QDate(2099, 12, 31))
        today = self._clock().astimezone(SEOUL).date()
        self.calendar.setSelectedDate(QDate(today.year, today.month, today.day))
        self.calendar.selectionChanged.connect(self._date_selected)
        self.calendar.currentPageChanged.connect(self._page_changed)
        side_layout.addWidget(self.calendar)
        shortcuts = QHBoxLayout()
        self.today_button = QPushButton("오늘")
        self.today_button.clicked.connect(self._select_today)
        self.tomorrow_button = QPushButton("내일")
        self.tomorrow_button.clicked.connect(self._select_tomorrow)
        self.week_button = QPushButton("이번 주")
        self.week_button.clicked.connect(self._select_week)
        for button in (self.today_button, self.tomorrow_button, self.week_button):
            shortcuts.addWidget(button)
        side_layout.addLayout(shortcuts)
        self.date_button = QPushButton("날짜 선택")
        self.date_button.clicked.connect(self._choose_date)
        side_layout.addWidget(self.date_button)
        competition_title = QLabel("대회 필터")
        competition_title.setObjectName("section")
        side_layout.addWidget(competition_title)
        self.competition_checkboxes: dict[str, QCheckBox] = {}
        for code, label in COMPETITIONS.items():
            checkbox = QCheckBox(f"{label} ({code})")
            checkbox.setChecked(code in self.preferences.selected_competitions)
            checkbox.toggled.connect(self._filters_changed)
            self.competition_checkboxes[code] = checkbox
            side_layout.addWidget(checkbox)
        self.favorite_checkbox = QCheckBox("관심 팀 경기만 보기")
        self.favorite_checkbox.setChecked(self.preferences.favorites_only)
        self.favorite_checkbox.toggled.connect(self._filters_changed)
        side_layout.addWidget(self.favorite_checkbox)
        self.favorites_button = QPushButton("관심 팀 관리")
        self.favorites_button.clicked.connect(self._open_favorites)
        side_layout.addWidget(self.favorites_button)
        self.next_favorite_button = QPushButton("다음 관심 팀 경기")
        self.next_favorite_button.clicked.connect(self._find_next_favorite)
        side_layout.addWidget(self.next_favorite_button)
        self.favorites_label = QLabel()
        self.favorites_label.setObjectName("metadata")
        self.favorites_label.setWordWrap(True)
        side_layout.addWidget(self.favorites_label)
        side_layout.addStretch()
        side_scroll = QScrollArea()
        self.side_scroll = side_scroll
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setMinimumWidth(300)
        side_scroll.setWidget(side)
        split.addWidget(side_scroll)

        content = QFrame()
        content.setObjectName("panel")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        self.date_label = QLabel()
        self.date_label.setObjectName("section")
        content_layout.addWidget(self.date_label)
        self.day_table = self._new_table()
        content_layout.addWidget(self.day_table, 3)
        self.empty_label = QLabel("선택한 날짜에 조건에 맞는 경기가 없습니다.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("metadata")
        content_layout.addWidget(self.empty_label)
        self.undated_label = QLabel("날짜 미정 경기")
        self.undated_label.setObjectName("section")
        content_layout.addWidget(self.undated_label)
        note = QLabel("날짜가 없는 경기는 선택한 날짜와 관계없이 이곳에 표시됩니다.")
        note.setWordWrap(True)
        note.setObjectName("metadata")
        content_layout.addWidget(note)
        self.undated_table = self._new_table()
        self.undated_table.setMinimumHeight(95)
        content_layout.addWidget(self.undated_table, 1)
        self.undated_empty_label = QLabel("현재 필터에 맞는 날짜 미정 경기가 없습니다.")
        self.undated_empty_label.setObjectName("metadata")
        self.undated_empty_label.setWordWrap(True)
        content_layout.addWidget(self.undated_empty_label)
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setWidget(content)
        split.addWidget(content_scroll)
        split.setSizes([340, 770])
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.last_refresh_label = QLabel("마지막 갱신: —")
        self.last_refresh_label.setObjectName("metadata")
        layout.addWidget(self.last_refresh_label)
        self.notification_status_label = QLabel()
        self.notification_status_label.setObjectName("metadata")
        self.notification_status_label.setWordWrap(True)
        self.notification_status_label.hide()
        layout.addWidget(self.notification_status_label)

    @staticmethod
    def _new_table() -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setIconSize(QSize(24, 24))
        table.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        table.setHorizontalHeaderLabels(["시간 (KST)", "대회", "홈", "원정", "상태"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(46)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _show_notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.setVisible(bool(text))

    def _persist_fields(self, **fields) -> bool:
        try:
            latest = self.settings_store.load()
            for name, value in fields.items():
                setattr(latest, name, value)
            self.settings_store.save(latest)
        except SettingsError as exc:
            self._settings_warning = str(exc)
            self._show_notice(str(exc))
            return False
        for name, value in fields.items():
            setattr(self.preferences, name, value)
        self._settings_warning = ""
        return True

    def _filters_changed(self) -> None:
        self._persist_fields(
            selected_competitions=self._selected_competitions(),
            favorites_only=self.favorite_checkbox.isChecked(),
        )
        self._render_matches()

    def _selected_competitions(self) -> set[str]:
        return {
            code for code, control in self.competition_checkboxes.items() if control.isChecked()
        }

    def _display_team_name(self, team_id: int | None, name: str) -> str:
        return self.preferences.custom_team_names.get(team_id) or display_team_name(team_id, name)

    def _apply_appearance(self) -> None:
        self.setStyleSheet(theme_stylesheet(self.preferences.theme, self.preferences.font_size))
        self.calendar.ensurePolished()
        side_width = max(320, self.calendar.minimumSizeHint().width() + 44)
        self.side_scroll.setMinimumWidth(side_width)
        self.splitter.setSizes([side_width, max(400, self.width() - side_width - 56)])
        for name, text in (("qt_calendar_prevmonth", "‹"), ("qt_calendar_nextmonth", "›")):
            button = self.calendar.findChild(QToolButton, name)
            if button is not None:
                button.setIcon(QIcon())
                button.setArrowType(Qt.ArrowType.NoArrow)
                button.setText(text)
        for table in (self.day_table, self.undated_table):
            table.ensurePolished()
            table.horizontalHeader().setMinimumSectionSize(self.preferences.font_size * 5)
        self._resize_table_rows()

    def _resize_table_rows(self) -> None:
        line_height = self.day_table.fontMetrics().lineSpacing()
        row_height = max(46, line_height * (2 if self._view_mode == "week" else 1) + 18)
        self.day_table.verticalHeader().setDefaultSectionSize(row_height)
        self.day_table.setMinimumHeight(max(180, row_height * 3 + line_height + 26))
        self.undated_table.verticalHeader().setDefaultSectionSize(max(46, line_height + 18))
        self.undated_table.setMinimumHeight(max(110, line_height * 2 + 50))

    def _restore_geometry(self) -> None:
        if self.preferences.window_geometry:
            encoded = self.preferences.window_geometry.encode("ascii")
            self.restoreGeometry(QByteArray.fromBase64(encoded))
        screen = (
            QGuiApplication.screenAt(self.frameGeometry().center())
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame_width = max(4, frame.width() - self.width())
        frame_height = max(4, frame.height() - self.height())
        max_width = max(1, available.width() - frame_width)
        max_height = max(1, available.height() - frame_height)
        self.setMinimumSize(min(920, max_width), min(650, max_height))
        self.resize(min(self.width(), max_width), min(self.height(), max_height))
        self.move(
            max(available.left(), min(self.x(), available.right() - self.width() + 1)),
            max(available.top(), min(self.y(), available.bottom() - self.height() + 1)),
        )

    def _save_window_state(self) -> None:
        self._persist_fields(
            window_geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"),
            selected_competitions=self._selected_competitions(),
            favorites_only=self.favorite_checkbox.isChecked(),
        )

    def _open_personalization(self) -> None:
        dialog = PersonalizationDialog(self.preferences, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                latest = dialog.apply_to(self.settings_store.load())
                self.settings_store.save(latest)
                self.preferences = latest
                self._settings_warning = ""
                self._apply_appearance()
                self._render_matches()
                self._update_notifications()
            except SettingsError as exc:
                self._settings_warning = str(exc)
                self._show_notice(str(exc))
        dialog.deleteLater()

    def _update_notifications(self) -> None:
        self.notification_status_label.setVisible(
            bool(self._notification_warning)
            and (self.preferences.notifications_enabled or self.preferences.minimize_to_tray)
        )
        self.notification_controller.update(
            list(self._notification_matches.values()), self.preferences, self.use_mock
        )

    def _show_notification_warning(self, message: str) -> None:
        self._notification_warning = message
        self.notification_status_label.setText(message)
        self.notification_status_label.setVisible(bool(message))

    def _remember_matches(self, result: LoadResult | RangeLoadResult) -> None:
        if result.source == "empty":
            return
        if isinstance(result, LoadResult):
            first = date(result.year, result.month, 1)
            last = first.replace(day=calendar_dates.monthrange(result.year, result.month)[1])
            month_versions = {(result.year, result.month): result.fetched_at}
            match_versions = {match.id: result.fetched_at for match in result.matches}
        else:
            first, last = result.start, result.end
            month_versions = result.month_fetched_at
            match_versions = result.match_fetched_at
        for identifier, match in list(self._notification_matches.items()):
            local = match.local_date
            if local is None or not first <= local <= last:
                continue
            month_key = (local.year, local.month)
            if month_key not in month_versions:
                continue
            incoming = month_versions[month_key]
            previous = self._notification_versions.get(identifier)
            if previous is None or incoming is not None and incoming >= previous:
                self._notification_matches.pop(identifier)
                self._notification_versions.pop(identifier, None)
        for match in result.matches:
            incoming = match_versions.get(match.id, result.fetched_at)
            previous = self._notification_versions.get(match.id)
            if previous is None or incoming is not None and incoming >= previous:
                self._notification_matches[match.id] = match
                self._notification_versions[match.id] = incoming
        self._update_notifications()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self._force_quit = True
        self.close()

    def _key_manager(self) -> KeyManager:
        if self.key_manager is None:
            self.key_manager = KeyManager(self.settings_store)
        return self.key_manager

    def _service(self) -> ScheduleService:
        if self.service_factory is not None:
            return self.service_factory(self.use_mock)
        mode = "mock" if self.use_mock else "live"
        provider = self._mock_client if self.use_mock else None
        if not self.use_mock:
            try:
                key = self._key_manager().get_key()
                if self._api_client is not None:
                    self._api_client.set_api_key(key or "")
                elif key:
                    self._api_client = ApiClient(key)
                if key:
                    provider = self._api_client
            except (CredentialError, SettingsError, ApiError) as exc:
                self._load_warning = str(exc)
        return ScheduleService(provider, CacheStore(self.settings_store.directory, mode), mode)

    def load_month(self, refresh: bool = False) -> None:
        if self.is_loading or self._close_requested:
            return
        self._load_warning = ""
        try:
            service = self._service()
        except Exception:
            self._show_notice("일정 서비스를 시작하지 못했습니다. 설정을 확인해 주세요.")
            return
        self._worker = MonthWorker(
            service, self.calendar.yearShown(), self.calendar.monthShown(), refresh
        )
        self._start_schedule_worker()

    def _start_schedule_worker(self) -> None:
        self._worker.finished.connect(self._load_finished)
        self._set_loading(True)
        self._show_notice(self._settings_warning)
        self.source_label.setText("일정을 불러오는 중…")
        self._worker.start()

    def _set_loading(self, loading: bool) -> None:
        loading = loading or self.is_loading
        for control in (
            self.calendar,
            self.today_button,
            self.tomorrow_button,
            self.week_button,
            self.date_button,
            self.next_favorite_button,
            self.refresh_button,
            self.mock_checkbox,
            self.key_button,
            self.settings_button,
        ):
            control.setEnabled(not loading)
        self.refresh_button.setText("불러오는 중…" if loading else "일정 새로고침")

    def _load_finished(self) -> None:
        worker = self._worker
        if worker is None:
            return
        result = worker.result
        self._worker = None
        worker.deleteLater()
        self._set_loading(False)
        if result is not None:
            self.result = result
            self.matches = result.matches
            source = {
                "mock": "데모 데이터",
                "live": "API 최신 일정",
                "cache": "저장된 일정",
                "empty": "일정 없음",
            }.get(result.source, "일정")
            self.source_label.setText(source + (" · 최신 여부 확인 필요" if result.stale else ""))
            updated = (
                result.fetched_at.astimezone(SEOUL).strftime("%Y.%m.%d %H:%M:%S KST")
                if result.fetched_at
                else "—"
            )
            self.last_refresh_label.setText("마지막 갱신: " + updated)
            self._show_notice(
                " ".join(filter(None, (self._settings_warning, self._load_warning, result.warning)))
            )
            self._remember_matches(result)
            if isinstance(worker, NextFavoriteWorker):
                self._view_mode = "day"
                if worker.next_match is not None:
                    chosen = worker.next_match.local_date
                    self.calendar.blockSignals(True)
                    self.calendar.setSelectedDate(QDate(chosen.year, chosen.month, chosen.day))
                    self.calendar.blockSignals(False)
                    message = "다음 관심 팀 경기를 찾았습니다. 검색 한도: 앞으로 90일."
                else:
                    message = (
                        "조회하지 못한 기간이 있어 다음 관심 팀 경기를 확인할 수 없습니다. "
                        "검색 한도: 앞으로 90일."
                        if result.source == "empty"
                        else "앞으로 90일 범위에서 확인된 다음 관심 팀 경기가 없습니다."
                    )
                self._show_notice(
                    " ".join(filter(None, (self._settings_warning, result.warning, message)))
                )
            self._render_matches()
            if isinstance(worker, NextFavoriteWorker) and worker.next_match is not None:
                for row in range(self.day_table.rowCount()):
                    if (
                        self.day_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                        == worker.next_match.id
                    ):
                        self.day_table.selectRow(row)
                        break
            self.load_completed.emit(result)
        if self._pending_catalog_refresh is not None and not self._close_requested:
            refresh = self._pending_catalog_refresh
            self._pending_catalog_refresh = None
            self._start_catalog(refresh)
        if self._close_requested:
            self.close()

    def _filtered(self) -> list[Match]:
        return filter_matches(
            self.matches,
            competitions=self._selected_competitions(),
            favorite_team_ids=(
                self.preferences.favorite_team_ids if self.favorite_checkbox.isChecked() else None
            ),
        )

    def _render_matches(self) -> None:
        self._resize_table_rows()
        chosen = self.calendar.selectedDate()
        selected_date = chosen.toPython()
        filtered = self._filtered()
        if self._view_mode == "week":
            daily = [
                match
                for match in filtered
                if match.local_date is not None
                and self._view_start <= match.local_date <= self._view_end
            ]
            label = f"{self._view_start:%Y.%m.%d} ~ {self._view_end:%m.%d} · 이번 주"
        else:
            daily = [match for match in filtered if match.local_date == selected_date]
            label = chosen.toString("yyyy년 M월 d일")
        undated = [match for match in filtered if match.local_date is None]
        self.date_label.setText(label + f" · {len(daily)}경기")
        self.day_table.setHorizontalHeaderItem(
            0, QTableWidgetItem("날짜 · 시간 (KST)" if self._view_mode == "week" else "시간 (KST)")
        )
        self._fill_table(self.day_table, daily)
        self._fill_table(self.undated_table, undated)
        self.empty_label.setVisible(not daily)
        self.undated_empty_label.setVisible(not undated)
        self.undated_label.setText(f"날짜 미정 경기 · {len(undated)}경기")
        self.favorites_label.setText(f"관심 팀 {len(self.preferences.favorite_team_ids)}개 선택됨")
        for marked in self._marked_dates:
            self.calendar.setDateTextFormat(marked, QTextCharFormat())
        self._marked_dates = {
            QDate(match.local_date.year, match.local_date.month, match.local_date.day)
            for match in filtered
            if match.local_date is not None
        }
        marker = QTextCharFormat()
        marker.setFontWeight(700)
        for marked in self._marked_dates:
            self.calendar.setDateTextFormat(marked, marker)

    def _fill_table(self, table: QTableWidget, matches: list[Match]) -> None:
        table.setRowCount(len(matches))
        for row, match in enumerate(matches):
            values = (
                (
                    f"{match.local_date:%m/%d}\n{match.kickoff_label}"
                    if self._view_mode == "week" and match.local_date is not None
                    else match.kickoff_label
                ),
                COMPETITIONS[match.competition],
                match.home_name,
                match.away_name,
                match.status_label,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, match.id)
                if column in (0, 1, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    team_id, crest_url = (
                        (match.home_id, match.home_crest)
                        if column == 2
                        else (match.away_id, match.away_crest)
                    )
                    item.setText(self._display_team_name(team_id, value))
                    item.setData(_TEAM_ID_ROLE, team_id)
                    item.setData(_CREST_URL_ROLE, crest_url)
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setIcon(
                        self.crest_provider.icon(
                            team_id, crest_url, allow_network=not self.use_mock
                        )
                    )
                table.setItem(row, column, item)

    def _refresh_team_icons(self, team_id: int) -> None:
        for table in (self.day_table, self.undated_table):
            for row in range(table.rowCount()):
                for column in (2, 3):
                    item = table.item(row, column)
                    if item is not None and item.data(_TEAM_ID_ROLE) == team_id:
                        item.setIcon(
                            self.crest_provider.icon(
                                team_id, item.data(_CREST_URL_ROLE), allow_network=False
                            )
                        )

    def _page_changed(self, year: int, month: int) -> None:
        self._view_mode = "day"
        selected = self.calendar.selectedDate()
        if (selected.year(), selected.month()) != (year, month):
            self.calendar.setSelectedDate(QDate(year, month, 1))
        self.load_month()

    def _date_selected(self) -> None:
        was_week = self._view_mode == "week"
        self._view_mode = "day"
        self._render_matches()
        if was_week:
            self.load_month()

    def _select_day(self, day: date) -> None:
        if self.is_loading:
            return
        self._view_mode = "day"
        self.calendar.blockSignals(True)
        self.calendar.setSelectedDate(QDate(day.year, day.month, day.day))
        self.calendar.blockSignals(False)
        self._render_matches()
        self.load_month()

    def _select_today(self) -> None:
        self._select_day(self._clock().astimezone(SEOUL).date())

    def _select_tomorrow(self) -> None:
        self._select_day(self._clock().astimezone(SEOUL).date() + timedelta(days=1))

    def _choose_date(self) -> None:
        was_week = self._view_mode == "week"
        self._view_mode = "day"
        self.calendar.setFocus()
        self._render_matches()
        if was_week:
            self.load_month()

    def _select_week(self, refresh: bool = False) -> None:
        if self.is_loading or self._close_requested:
            return
        today = self._clock().astimezone(SEOUL).date()
        self._view_start = today - timedelta(days=today.weekday())
        self._view_end = self._view_start + timedelta(days=6)
        self._view_mode = "week"
        self._load_warning = ""
        try:
            service = self._service()
        except Exception:
            self._show_notice("이번 주 일정을 시작하지 못했습니다. 설정을 확인해 주세요.")
            return
        self._worker = RangeWorker(service, self._view_start, self._view_end, refresh)
        self._start_schedule_worker()

    def _refresh_current(self) -> None:
        if self._view_mode == "week":
            self._select_week(refresh=True)
        else:
            self.load_month(refresh=True)

    def _find_next_favorite(self) -> None:
        if self.is_loading or self._close_requested:
            return
        if not self.preferences.favorite_team_ids:
            self._show_notice("관심 팀 관리에서 팀을 먼저 선택해 주세요.")
            return
        competitions = self._selected_competitions()
        if not competitions:
            self._show_notice("검색할 대회를 하나 이상 선택해 주세요.")
            return
        self._load_warning = ""
        try:
            service = self._service()
        except Exception:
            self._show_notice("다음 경기 조회를 시작하지 못했습니다. 설정을 확인해 주세요.")
            return
        self._worker = NextFavoriteWorker(
            service, self._clock(), self.preferences.favorite_team_ids, competitions
        )
        self._start_schedule_worker()

    def _update_mode_label(self) -> None:
        self.demo_label.setVisible(self.use_mock)

    def _mode_changed(self, checked: bool) -> None:
        if self.is_loading:
            return
        self.use_mock = checked
        self.matches = []
        self._catalog_teams = []
        self._notification_matches = {}
        self._notification_versions = {}
        self.result = None
        self._render_matches()
        self._update_mode_label()
        try:
            preferences = self.settings_store.load()
            preferences.use_mock = checked
            self.settings_store.save(preferences)
            self.preferences = preferences
            self._settings_warning = ""
        except SettingsError as exc:
            self._settings_warning = str(exc)
            self._show_notice(str(exc))
        self._update_notifications()
        if self._view_mode == "week":
            self._select_week()
        else:
            self.load_month()

    def _open_key_settings(self) -> None:
        dialog = KeyDialog(self._key_manager(), self)
        dialog.changed.connect(self._key_changed)
        dialog.exec()
        dialog.deleteLater()

    def _key_changed(self) -> None:
        if self._api_client is not None:
            try:
                self._api_client.set_api_key(self._key_manager().get_key() or "")
            except (CredentialError, SettingsError, ApiError) as exc:
                self._api_client.set_api_key("")
                self._show_notice(str(exc))

    def _open_favorites(self) -> None:
        teams = {
            team_id: self.preferences.favorite_team_names.get(team_id, f"팀 #{team_id}")
            for team_id in self.preferences.favorite_team_ids
        }
        crest_urls: dict[int, str] = {}
        for match in self.matches:
            for team_id, name, crest_url in (
                (match.home_id, match.home_name, match.home_crest),
                (match.away_id, match.away_name, match.away_crest),
            ):
                if team_id is not None:
                    teams[team_id] = name
                    if crest_url:
                        crest_urls[team_id] = crest_url
        dialog = FavoritesDialog(
            teams,
            self.preferences.favorite_team_ids,
            self,
            crest_provider=self.crest_provider,
            crest_urls=crest_urls,
            allow_network=not self.use_mock,
            catalog=self._catalog_teams,
            custom_names=self.preferences.custom_team_names,
        )
        self._favorites_dialog = dialog
        dialog.refresh_requested.connect(lambda: self._start_catalog(refresh=True))
        # Queue until exec enters the event loop; opening the dialog is always immediate.
        QTimer.singleShot(0, self._start_catalog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.selected_teams()
            try:
                preferences = self.settings_store.load()
                preferences.favorite_team_ids = set(chosen)
                preferences.favorite_team_names = chosen
                preferences.custom_team_names = dialog.edited_custom_names()
                self.settings_store.save(preferences)
                self.preferences = preferences
                self._settings_warning = ""
                self._render_matches()
                self._update_notifications()
            except SettingsError as exc:
                self._settings_warning = str(exc)
                self._show_notice(str(exc))
        self._favorites_dialog = None
        self._pending_catalog_refresh = None
        dialog.deleteLater()

    def _start_catalog(self, refresh: bool = False) -> None:
        dialog = self._favorites_dialog
        if dialog is None or self._close_requested:
            return
        dialog.set_loading(True)
        if self._worker is not None:
            self._pending_catalog_refresh = refresh
            return
        if self._catalog_worker is not None:
            return
        try:
            if self.catalog_factory is not None:
                catalog = self.catalog_factory(self.use_mock)
            else:
                provider = self._mock_client if self.use_mock else self._service().provider
                catalog = TeamCatalog(
                    provider, self.settings_store.directory, "mock" if self.use_mock else "live"
                )
        except Exception:
            dialog.set_loading(False)
            dialog.update_catalog([], "팀 목록 서비스를 시작하지 못했습니다.")
            return
        self._catalog_worker = CatalogWorker(catalog, refresh)
        self._catalog_worker.finished.connect(self._catalog_finished)
        self._set_loading(True)
        self._catalog_worker.start()

    def _catalog_finished(self) -> None:
        worker = self._catalog_worker
        if worker is None:
            return
        result = worker.result
        self._catalog_worker = None
        worker.deleteLater()
        self._set_loading(False)
        if result is not None:
            self._catalog_teams = result.teams
            if self._favorites_dialog is not None:
                self._favorites_dialog.set_loading(False)
                self._favorites_dialog.update_catalog(result.teams, result.warning)
        if self._close_requested:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._resources_closed:
            super().closeEvent(event)
            return
        if (
            not self._force_quit
            and not self._close_requested
            and self.preferences.minimize_to_tray
            and self.notification_controller.hide_to_tray()
        ):
            self._save_window_state()
            self.hide()
            event.ignore()
            return
        if self.is_loading:
            self._close_requested = True
            self._show_notice("현재 요청을 마친 뒤 종료합니다. 잠시 기다려 주세요.")
            event.ignore()
            return
        if not self._resources_closed:
            self._save_window_state()
            self.crest_provider.close()
            self.notification_controller.close()
            self._resources_closed = True
        super().closeEvent(event)
        if self._force_quit:
            QTimer.singleShot(0, QApplication.instance().quit)
