"""Searchable team selection with local display-name overrides."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .logos import CrestProvider
from .models import COMPETITIONS
from .teams import display_team_name

if TYPE_CHECKING:
    from .catalog import Team


def _search_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class FavoritesDialog(QDialog):
    """Keep check states independent of search results and catalog updates."""

    refresh_requested = Signal()

    def __init__(
        self,
        teams: dict[int, str],
        selected_ids: set[int],
        parent: QWidget | None = None,
        crest_provider: CrestProvider | None = None,
        crest_urls: dict[int, str] | None = None,
        allow_network: bool = False,
        *,
        catalog: list[Team] | None = None,
        custom_names: dict[int, str] | None = None,
    ):
        super().__init__(parent)
        self.crest_provider = crest_provider
        self.crest_urls = dict(crest_urls or {})
        self._allow_network = allow_network
        self._teams = dict(teams)
        for team_id in selected_ids:
            self._teams.setdefault(team_id, f"팀 {team_id}")
        self._catalog: dict[int, Team] = {}
        self._custom_names = dict(custom_names or {})
        self._items: dict[int, QListWidgetItem] = {}
        self._requested_crests: dict[int, str | None] = {}
        self._warning = ""
        self._loading = False
        self.setWindowTitle("관심 팀 관리 — Kickoff Desk")
        self.resize(560, 650)

        layout = QVBoxLayout(self)
        description = QLabel("팀을 검색하고 관심 팀을 체크하세요. 숨겨진 팀의 선택도 유지됩니다.")
        description.setWordWrap(True)
        layout.addWidget(description)
        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("한국어·영문 팀 이름 검색")
        self.search_input.setAccessibleName("팀 이름 검색")
        self.search_input.setClearButtonEnabled(True)
        controls.addWidget(self.search_input, 1)
        self.competition_filter = QComboBox()
        self.competition_filter.setAccessibleName("팀 참가 대회 필터")
        self.competition_filter.addItem("모든 대회", "")
        for code, name in COMPETITIONS.items():
            self.competition_filter.addItem(name, code)
        controls.addWidget(self.competition_filter)
        layout.addLayout(controls)

        self.team_list = QListWidget()
        self.team_list.setAccessibleName("관심 팀 선택")
        self.team_list.setIconSize(QSize(24, 24))
        self.team_list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(self.team_list, 1)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("metadata")
        layout.addWidget(self.status_label)
        self.refresh_button = QPushButton("전체 참가 팀 새로고침")
        self.refresh_button.setAutoDefault(False)
        layout.addWidget(self.refresh_button)

        self.original_name_label = QLabel("목록에서 팀을 선택하면 표시 이름을 바꿀 수 있습니다.")
        self.original_name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.original_name_label.setWordWrap(True)
        layout.addWidget(self.original_name_label)
        rename = QHBoxLayout()
        self.nickname_input = QLineEdit()
        self.nickname_input.setAccessibleName("선택한 팀의 표시 이름")
        self.nickname_input.setMaxLength(80)
        self.nickname_input.setPlaceholderText("표시 이름 (최대 80자, 빈칸이면 기본 이름)")
        rename.addWidget(self.nickname_input, 1)
        self.rename_button = QPushButton("이름 적용")
        self.rename_button.setAutoDefault(False)
        rename.addWidget(self.rename_button)
        self.reset_name_button = QPushButton("기본 이름")
        self.reset_name_button.setAutoDefault(False)
        rename.addWidget(self.reset_name_button)
        layout.addLayout(rename)
        self.name_error_label = QLabel()
        self.name_error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_error_label.setWordWrap(True)
        self.name_error_label.hide()
        layout.addWidget(self.name_error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        for button in buttons.buttons():
            button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.search_input.textChanged.connect(self._apply_filter)
        self.competition_filter.currentIndexChanged.connect(self._apply_filter)
        self.team_list.itemChanged.connect(self._update_status)
        self.team_list.currentItemChanged.connect(self._update_editor)
        self.team_list.verticalScrollBar().valueChanged.connect(self._request_visible_icons)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.rename_button.clicked.connect(self._apply_nickname)
        self.reset_name_button.clicked.connect(self._reset_nickname)
        self.nickname_input.returnPressed.connect(self._apply_nickname)
        self.nickname_input.textChanged.connect(self.name_error_label.hide)
        if self.crest_provider is not None:
            self.crest_provider.updated.connect(self._refresh_team_icon)

        self._merge_catalog(catalog or [])
        for team_id in self._teams:
            self._add_item(team_id, team_id in selected_ids)
        self.team_list.sortItems()
        self._apply_filter()
        self._update_editor()

    def _display_name(self, team_id: int) -> str:
        return self._custom_names.get(team_id) or display_team_name(team_id, self._teams[team_id])

    def _add_item(self, team_id: int, checked: bool) -> None:
        item = QListWidgetItem(self._display_name(team_id))
        item.setToolTip(self._teams[team_id])
        item.setData(Qt.ItemDataRole.UserRole, team_id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._items[team_id] = item
        self.team_list.addItem(item)
        self._refresh_team_icon(team_id)

    def _merge_catalog(self, teams: list[Team]) -> None:
        for team in teams:
            self._catalog[team.id] = team
            self._teams[team.id] = team.name
            if team.crest:
                self.crest_urls[team.id] = team.crest

    def update_catalog(self, teams: list[Team], warning: str = "") -> None:
        """Merge new teams without losing saved, hidden, or newly checked teams."""
        self._warning = warning
        self._merge_catalog(teams)
        with QSignalBlocker(self.team_list):
            for team in teams:
                item = self._items.get(team.id)
                if item is None:
                    self._add_item(team.id, False)
                else:
                    item.setText(self._display_name(team.id))
                    item.setToolTip(team.name)
                    self._refresh_team_icon(team.id)
            self.team_list.sortItems()
        self._apply_filter()
        self._update_editor()

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.refresh_button.setEnabled(not loading)
        self._update_status()

    def _apply_filter(self) -> None:
        query = _search_text(self.search_input.text())
        competition = self.competition_filter.currentData()
        for team_id, item in self._items.items():
            team = self._catalog.get(team_id)
            names = (
                self._display_name(team_id),
                display_team_name(team_id, self._teams[team_id]),
                self._teams[team_id],
                team.short_name if team is not None else "",
            )
            matches_name = not query or any(query in _search_text(name) for name in names)
            matches_competition = not competition or (
                team is not None and competition in team.competitions
            )
            item.setHidden(not (matches_name and matches_competition))
        current = self.team_list.currentItem()
        if current is not None and current.isHidden():
            self.team_list.setCurrentItem(None)
        self._update_status()
        QTimer.singleShot(0, self._request_visible_icons)

    def _update_status(self) -> None:
        visible = sum(not item.isHidden() for item in self._items.values())
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in self._items.values())
        text = f"검색 결과 {visible}개 / 전체 {len(self._items)}개 · 관심 팀 {selected}개"
        if self._loading:
            text += "\n전체 참가 팀을 불러오는 중입니다…"
        elif not self._items:
            text += "\n전체 참가 팀 새로고침으로 팀을 불러올 수 있습니다."
        elif not visible:
            text += "\n검색 조건에 맞는 팀이 없습니다."
        if self._warning:
            text += "\n" + self._warning
        self.status_label.setText(text)

    def _update_editor(self) -> None:
        item = self.team_list.currentItem()
        enabled = item is not None and not item.isHidden()
        self.nickname_input.setEnabled(enabled)
        self.rename_button.setEnabled(enabled)
        self.reset_name_button.setEnabled(enabled)
        if not enabled:
            self.nickname_input.clear()
            self.original_name_label.setText("목록에서 팀을 선택하면 표시 이름을 바꿀 수 있습니다.")
            return
        team_id = item.data(Qt.ItemDataRole.UserRole)
        self.original_name_label.setText("원래 이름: " + self._teams[team_id])
        self.nickname_input.setText(self._custom_names.get(team_id, ""))

    def _apply_nickname(self) -> bool:
        item = self.team_list.currentItem()
        if item is None or item.isHidden():
            return True
        team_id = item.data(Qt.ItemDataRole.UserRole)
        name = self.nickname_input.text().strip()
        if name and not name.isprintable():
            self.name_error_label.setText(
                "표시 이름에는 줄바꿈이나 제어 문자를 사용할 수 없습니다."
            )
            self.name_error_label.show()
            return False
        if name:
            self._custom_names[team_id] = name
        else:
            self._custom_names.pop(team_id, None)
        item.setText(self._display_name(team_id))
        self.team_list.sortItems()
        self._apply_filter()
        return True

    def accept(self) -> None:
        if self._apply_nickname():
            super().accept()

    def _reset_nickname(self) -> None:
        self.nickname_input.clear()
        self._apply_nickname()

    def selected_teams(self) -> dict[int, str]:
        """Return stable IDs and original names, never user display overrides."""
        return {
            team_id: self._teams[team_id]
            for team_id, item in self._items.items()
            if item.checkState() == Qt.CheckState.Checked
        }

    def edited_custom_names(self) -> dict[int, str]:
        return dict(self._custom_names)

    def _refresh_team_icon(self, team_id: int) -> None:
        if self.crest_provider is None or team_id not in self._items:
            return
        self._items[team_id].setIcon(
            self.crest_provider.icon(team_id, self.crest_urls.get(team_id), allow_network=False)
        )

    def _request_visible_icons(self) -> None:
        if self.crest_provider is None or not self._allow_network or not self.isVisible():
            return
        viewport = self.team_list.viewport().rect()
        for team_id, item in self._items.items():
            if item.isHidden() or not self.team_list.visualItemRect(item).intersects(viewport):
                continue
            crest = self.crest_urls.get(team_id)
            if team_id in self._requested_crests and self._requested_crests[team_id] == crest:
                continue
            self._requested_crests[team_id] = crest
            item.setIcon(self.crest_provider.icon(team_id, crest, allow_network=True))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._request_visible_icons)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._request_visible_icons)
