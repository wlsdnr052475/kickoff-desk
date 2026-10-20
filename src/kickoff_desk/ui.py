"""Native, score-free Qt calendar and safe user-controlled API key settings."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QDate, QLocale, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient, ApiError
from .cache import CacheStore
from .logos import CrestProvider
from .mock import MockClient
from .models import COMPETITIONS, SEOUL, Match, filter_matches
from .service import LoadResult, ScheduleService
from .settings import CredentialError, KeyManager, Preferences, SettingsError, SettingsStore

_TEAM_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_CREST_URL_ROLE = Qt.ItemDataRole.UserRole + 2

_STYLE = """
QMainWindow, QDialog { background: #f2f5f8; color: #162b41; }
QWidget { font-family: "Malgun Gothic", "Noto Sans CJK KR", sans-serif; font-size: 13px; }
QLabel { color: #162b41; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#subtitle, QLabel#metadata { color: #566b7e; }
QLabel#section { font-size: 17px; font-weight: 700; }
QLabel#notice { background: #fff2d5; color: #775000; padding: 11px; border-radius: 7px; }
QLabel#demo { background: #e0f4ef; color: #126554; padding: 10px; border-radius: 7px; }
QFrame#panel { background: white; border-radius: 10px; }
QPushButton { background: white; color: #233c54; border: 1px solid #cdd7e0;
              border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { background: #edf4f8; border-color: #7b9cab; }
QPushButton:disabled { color: #93a3b0; background: #eef1f4; }
QPushButton#primary { background: #137969; color: white; border-color: #137969; }
QPushButton#primary:hover { background: #096251; }
QPushButton#primary:disabled { background: #9bbeb8; border-color: #9bbeb8; }
QCheckBox { spacing: 7px; padding: 5px 0; color: #263f55; }
QLineEdit, QListWidget { background: white; border: 1px solid #cdd7e0;
                          border-radius: 5px; padding: 8px; color: #162b41; }
QTableWidget { background: white; alternate-background-color: #f5f8fa;
               color: #162b41; border: none; selection-background-color: #deefeb;
               selection-color: #162b41; gridline-color: #ecf0f4; }
QHeaderView::section { background: #eaf0f5; color: #425970;
                       border: none; padding: 10px 6px; font-weight: 600; }
QCalendarWidget QWidget { alternate-background-color: #eaf0f5; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eaf0f5; }
QCalendarWidget QAbstractItemView { background: white; color: #243b53;
                                   selection-background-color: #137969;
                                   selection-color: white; border: none; }
QCalendarWidget QToolButton { color: #233c54; background: #eaf0f5;
                             border: none; padding: 6px; }
QCalendarWidget QSpinBox { background: white; color: #233c54; }
QSplitter::handle { background: #f2f5f8; }
"""


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


class FavoritesDialog(QDialog):
    def __init__(
        self,
        teams: dict[int, str],
        selected_ids: set[int],
        parent: QWidget | None = None,
        crest_provider: CrestProvider | None = None,
        crest_urls: dict[int, str] | None = None,
        allow_network: bool = False,
    ):
        super().__init__(parent)
        self.crest_provider = crest_provider
        self.crest_urls = crest_urls or {}
        self.setWindowTitle("관심 팀 관리 — Kickoff Desk")
        self.resize(400, 440)
        layout = QVBoxLayout(self)
        description = QLabel("이번 달 일정의 팀과 저장된 관심 팀을 선택하세요.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.team_list = QListWidget()
        self.team_list.setAccessibleName("관심 팀 선택")
        self.team_list.setIconSize(QSize(24, 24))
        self.team_list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        for team_id, name in sorted(teams.items(), key=lambda entry: entry[1].casefold()):
            item = QListWidgetItem(name)
            item.setToolTip(name)
            item.setData(Qt.ItemDataRole.UserRole, team_id)
            if self.crest_provider is not None:
                item.setIcon(
                    self.crest_provider.icon(
                        team_id, self.crest_urls.get(team_id), allow_network=allow_network
                    )
                )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if team_id in selected_ids else Qt.CheckState.Unchecked
            )
            self.team_list.addItem(item)
        if self.crest_provider is not None:
            self.crest_provider.updated.connect(self._refresh_team_icon)
        layout.addWidget(self.team_list)
        if not teams:
            layout.addWidget(QLabel("일정을 불러온 뒤 관심 팀을 선택할 수 있습니다."))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_team_icon(self, team_id: int) -> None:
        if self.crest_provider is None:
            return
        for index in range(self.team_list.count()):
            item = self.team_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == team_id:
                item.setIcon(
                    self.crest_provider.icon(
                        team_id, self.crest_urls.get(team_id), allow_network=False
                    )
                )

    def selected_teams(self) -> dict[int, str]:
        return {
            item.data(Qt.ItemDataRole.UserRole): item.text()
            for index in range(self.team_list.count())
            if (item := self.team_list.item(index)).checkState() == Qt.CheckState.Checked
        }


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
    ):
        super().__init__()
        self.settings_store = settings_store
        self.key_manager = key_manager
        self.service_factory = service_factory
        self.crest_provider = (
            crest_provider
            if crest_provider is not None
            else CrestProvider(settings_store.directory, self)
        )
        self._api_client: ApiClient | None = None
        self._mock_client = MockClient()
        self._worker: MonthWorker | None = None
        self._close_requested = False
        self._load_warning = ""
        self.matches: list[Match] = []
        self.result: LoadResult | None = None
        self._marked_dates: set[QDate] = set()
        preference_warning = ""
        try:
            self.preferences = settings_store.load()
        except SettingsError as exc:
            self.preferences = Preferences()
            preference_warning = str(exc)
        self._settings_warning = preference_warning
        self.use_mock = self.preferences.use_mock if use_mock is None else use_mock
        self.setWindowTitle("Kickoff Desk — 축구 일정")
        self.resize(1180, 790)
        self.setMinimumSize(920, 650)
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self.crest_provider.updated.connect(self._refresh_team_icons)
        self._render_matches()
        self._update_mode_label()
        if preference_warning:
            self._show_notice(preference_warning)
        if auto_load:
            QTimer.singleShot(0, self.load_month)

    @property
    def is_loading(self) -> bool:
        return self._worker is not None

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
        subtitle.setObjectName("subtitle")
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        self.key_button = QPushButton("API 키 설정")
        self.key_button.clicked.connect(self._open_key_settings)
        header.addWidget(self.key_button)
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
        self.refresh_button.clicked.connect(lambda: self.load_month(refresh=True))
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
        today = datetime.now(SEOUL).date()
        self.calendar.setSelectedDate(QDate(today.year, today.month, today.day))
        self.calendar.selectionChanged.connect(self._render_matches)
        self.calendar.currentPageChanged.connect(self._page_changed)
        side_layout.addWidget(self.calendar)
        self.today_button = QPushButton("오늘로 이동")
        self.today_button.clicked.connect(self._select_today)
        side_layout.addWidget(self.today_button)
        competition_title = QLabel("대회 필터")
        competition_title.setObjectName("section")
        side_layout.addWidget(competition_title)
        self.competition_checkboxes: dict[str, QCheckBox] = {}
        for code, label in COMPETITIONS.items():
            checkbox = QCheckBox(f"{label} ({code})")
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._render_matches)
            self.competition_checkboxes[code] = checkbox
            side_layout.addWidget(checkbox)
        self.favorite_checkbox = QCheckBox("관심 팀 경기만 보기")
        self.favorite_checkbox.toggled.connect(self._render_matches)
        side_layout.addWidget(self.favorite_checkbox)
        self.favorites_button = QPushButton("관심 팀 관리")
        self.favorites_button.clicked.connect(self._open_favorites)
        side_layout.addWidget(self.favorites_button)
        self.favorites_label = QLabel()
        self.favorites_label.setObjectName("metadata")
        self.favorites_label.setWordWrap(True)
        side_layout.addWidget(self.favorites_label)
        side_layout.addStretch()
        split.addWidget(side)

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
        split.addWidget(content)
        split.setSizes([340, 770])
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.last_refresh_label = QLabel("마지막 갱신: —")
        self.last_refresh_label.setObjectName("metadata")
        layout.addWidget(self.last_refresh_label)

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
        self._worker.finished.connect(self._load_finished)
        self._set_loading(True)
        self._show_notice(self._settings_warning)
        self.source_label.setText("일정을 불러오는 중…")
        self._worker.start()

    def _set_loading(self, loading: bool) -> None:
        for control in (
            self.calendar,
            self.today_button,
            self.refresh_button,
            self.mock_checkbox,
            self.key_button,
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
            }[result.source]
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
            self._render_matches()
            self.load_completed.emit(result)
        if self._close_requested:
            self.close()

    def _filtered(self) -> list[Match]:
        return filter_matches(
            self.matches,
            competitions={
                code
                for code, checkbox in self.competition_checkboxes.items()
                if checkbox.isChecked()
            },
            favorite_team_ids=(
                self.preferences.favorite_team_ids if self.favorite_checkbox.isChecked() else None
            ),
        )

    def _render_matches(self) -> None:
        chosen = self.calendar.selectedDate()
        selected_date = chosen.toPython()
        filtered = self._filtered()
        daily = [match for match in filtered if match.local_date == selected_date]
        undated = [match for match in filtered if match.local_date is None]
        self.date_label.setText(chosen.toString("yyyy년 M월 d일") + f" · {len(daily)}경기")
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
                match.kickoff_label,
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
        selected = self.calendar.selectedDate()
        if (selected.year(), selected.month()) != (year, month):
            self.calendar.setSelectedDate(QDate(year, month, 1))
        self.load_month()

    def _select_today(self) -> None:
        today = datetime.now(SEOUL).date()
        self.calendar.setSelectedDate(QDate(today.year, today.month, today.day))

    def _update_mode_label(self) -> None:
        self.demo_label.setVisible(self.use_mock)

    def _mode_changed(self, checked: bool) -> None:
        if self.is_loading:
            return
        self.use_mock = checked
        self.matches = []
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
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.selected_teams()
            try:
                preferences = self.settings_store.load()
                preferences.favorite_team_ids = set(chosen)
                preferences.favorite_team_names = chosen
                self.settings_store.save(preferences)
                self.preferences = preferences
                self._settings_warning = ""
                self._render_matches()
            except SettingsError as exc:
                self._settings_warning = str(exc)
                self._show_notice(str(exc))
        dialog.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.is_loading:
            self._close_requested = True
            self._show_notice("현재 요청을 마친 뒤 종료합니다. 잠시 기다려 주세요.")
            event.ignore()
            return
        self.crest_provider.close()
        super().closeEvent(event)
