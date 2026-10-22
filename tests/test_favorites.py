"""Offline widget behavior for searching and personalizing favorite teams."""

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap

from kickoff_desk.catalog import Team
from kickoff_desk.favorites import FavoritesDialog


def items(dialog):
    return {
        item.data(Qt.ItemDataRole.UserRole): item
        for index in range(dialog.team_list.count())
        if (item := dialog.team_list.item(index)) is not None
    }


def visible_ids(dialog):
    return {team_id for team_id, item in items(dialog).items() if not item.isHidden()}


@pytest.fixture
def catalog():
    return [
        Team(57, "Arsenal FC", "Arsenal", competitions=("PL", "CL")),
        Team(73, "Tottenham Hotspur FC", "Spurs", competitions=("PL",)),
        Team(81, "FC Barcelona", "Barça", competitions=("PD", "CL")),
        Team(9001, "Example United FC", "EUFC", competitions=("CL",)),
    ]


@pytest.fixture
def dialog(qtbot, catalog):
    widget = FavoritesDialog({}, {57}, catalog=catalog, custom_names={57: "우리 아스널"})
    qtbot.addWidget(widget)
    return widget


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("아스널", {57}),
        (" ARSENAL fc ", {57}),
        ("우리", {57}),
        ("토트넘", {73}),
        ("SPURS", {73}),
        ("바르셀로나", {81}),
        ("Barça", {81}),
        ("eufc", {9001}),
        ("unknown", set()),
    ],
)
def test_search_matches_korean_original_short_and_custom_names(dialog, query, expected):
    dialog.search_input.setText(query)
    assert visible_ids(dialog) == expected
    assert dialog.selected_teams() == {57: "Arsenal FC"}


def test_competition_and_search_filters_keep_hidden_selections(dialog):
    items(dialog)[81].setCheckState(Qt.CheckState.Checked)
    dialog.competition_filter.setCurrentIndex(dialog.competition_filter.findData("PD"))
    assert visible_ids(dialog) == {81}
    dialog.search_input.setText("arsenal")
    assert visible_ids(dialog) == set()
    assert dialog.selected_teams() == {57: "Arsenal FC", 81: "FC Barcelona"}
    assert "관심 팀 2개" in dialog.status_label.text()
    assert "검색 조건에 맞는 팀이 없습니다" in dialog.status_label.text()
    dialog.search_input.clear()
    dialog.competition_filter.setCurrentIndex(dialog.competition_filter.findData("CL"))
    assert visible_ids(dialog) == {57, 81, 9001}
    dialog.competition_filter.setCurrentIndex(0)
    assert items(dialog)[57].checkState() == Qt.CheckState.Checked
    assert items(dialog)[81].checkState() == Qt.CheckState.Checked


def test_catalog_update_preserves_search_checks_and_absent_saved_teams(qtbot, catalog):
    dialog = FavoritesDialog({999: "Saved Club"}, {999}, catalog=catalog)
    qtbot.addWidget(dialog)
    original_item = items(dialog)[57]
    original_item.setCheckState(Qt.CheckState.Checked)
    dialog.team_list.setCurrentItem(original_item)
    dialog.search_input.setText("Arsenal")
    dialog.update_catalog(
        [
            Team(57, "Arsenal FC", "Gunners", competitions=("PL", "CL")),
            Team(64, "Liverpool FC", "Liverpool", competitions=("PL",)),
        ],
        warning="저장된 목록을 표시합니다.",
    )
    assert dialog.search_input.text() == "Arsenal"
    assert visible_ids(dialog) == {57}
    assert dialog.team_list.currentItem() is original_item
    assert dialog.selected_teams() == {999: "Saved Club", 57: "Arsenal FC"}
    assert len(items(dialog)) == 6
    assert "저장된 목록을 표시합니다." in dialog.status_label.text()
    dialog.search_input.setText("Gunners")
    assert visible_ids(dialog) == {57}
    dialog.search_input.clear()
    assert items(dialog)[999].checkState() == Qt.CheckState.Checked
    assert items(dialog)[64].checkState() == Qt.CheckState.Unchecked


def test_selected_team_without_catalog_or_saved_name_is_retained(qtbot):
    dialog = FavoritesDialog({9001: "Unknown Town FC"}, {9001, 999})
    qtbot.addWidget(dialog)
    assert dialog.selected_teams() == {9001: "Unknown Town FC", 999: "팀 999"}
    assert items(dialog)[9001].text() == "Unknown Town FC"
    dialog.search_input.setText("unknown")
    assert visible_ids(dialog) == {9001}
    assert dialog.selected_teams()[999] == "팀 999"


def test_rename_and_reset_preserve_original_names_ids_and_input_preferences(qtbot, catalog):
    preferences = {57: "이전 별명", 999: "저장된 별명"}
    dialog = FavoritesDialog({}, {57}, catalog=catalog, custom_names=preferences)
    qtbot.addWidget(dialog)
    item = items(dialog)[57]
    dialog.team_list.setCurrentItem(item)
    dialog.nickname_input.setText("  런던의 아스널  ")
    dialog.rename_button.click()
    assert item.text() == "런던의 아스널"
    assert item.toolTip() == "Arsenal FC"
    assert dialog.selected_teams() == {57: "Arsenal FC"}
    assert dialog.edited_custom_names() == {57: "런던의 아스널", 999: "저장된 별명"}
    assert preferences == {57: "이전 별명", 999: "저장된 별명"}
    dialog.search_input.setText("런던")
    assert visible_ids(dialog) == {57}
    dialog.search_input.clear()
    dialog.reset_name_button.click()
    assert item.text() == "아스널"
    assert item.toolTip() == "Arsenal FC"
    assert dialog.edited_custom_names() == {999: "저장된 별명"}
    assert dialog.selected_teams() == {57: "Arsenal FC"}


def test_empty_nickname_resets_and_unchecked_team_override_is_retained(dialog):
    item = items(dialog)[57]
    dialog.team_list.setCurrentItem(item)
    dialog.nickname_input.setText("   ")
    dialog.rename_button.click()
    assert dialog.edited_custom_names() == {}
    assert item.text() == "아스널"
    dialog.nickname_input.setText("별명")
    dialog.rename_button.click()
    item.setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_teams() == {}
    assert dialog.edited_custom_names() == {57: "별명"}
    returned = dialog.edited_custom_names()
    returned.clear()
    assert dialog.edited_custom_names() == {57: "별명"}


def test_nickname_length_limit_and_control_character_rejection(dialog):
    item = items(dialog)[57]
    dialog.team_list.setCurrentItem(item)
    dialog.nickname_input.setText("가" * 90)
    dialog.rename_button.click()
    assert dialog.edited_custom_names()[57] == "가" * 80
    dialog.nickname_input.setText("invalid\x01name")
    dialog.rename_button.click()
    assert dialog.edited_custom_names()[57] == "가" * 80
    assert "제어 문자" in dialog.name_error_label.text()


def test_save_applies_pending_nickname_and_blocks_invalid_input(dialog, qtbot):
    dialog.team_list.setCurrentItem(items(dialog)[57])
    dialog.nickname_input.setText("invalid\x01name")
    dialog.accept()
    assert dialog.result() != dialog.DialogCode.Accepted
    dialog.nickname_input.setText("저장할 이름")
    with qtbot.waitSignal(dialog.accepted):
        dialog.accept()
    assert dialog.edited_custom_names()[57] == "저장할 이름"


def test_loading_refresh_and_empty_results_are_explicit(qtbot):
    dialog = FavoritesDialog({}, set())
    qtbot.addWidget(dialog)
    assert "전체 참가 팀 새로고침" in dialog.status_label.text()
    with qtbot.waitSignal(dialog.refresh_requested):
        dialog.refresh_button.click()
    dialog.set_loading(True)
    assert not dialog.refresh_button.isEnabled()
    assert "불러오는 중" in dialog.status_label.text()
    dialog.update_catalog([], "오프라인 상태입니다.")
    dialog.set_loading(False)
    assert dialog.refresh_button.isEnabled()
    assert "오프라인 상태입니다." in dialog.status_label.text()


def test_only_visible_rows_request_network_crests(qtbot):
    class Provider(QObject):
        updated = Signal(int)

        def __init__(self):
            super().__init__()
            self.calls = []
            pixmap = QPixmap(24, 24)
            pixmap.fill(Qt.GlobalColor.blue)
            self.image = QIcon(pixmap)

        def icon(self, team_id, crest_url=None, *, allow_network=False):
            self.calls.append((team_id, allow_network))
            return self.image

    provider = Provider()
    dialog = FavoritesDialog(
        {team_id: f"Club {team_id}" for team_id in range(1000, 1100)},
        {1000},
        crest_provider=provider,
        allow_network=True,
    )
    qtbot.addWidget(dialog)
    assert all(not network for _, network in provider.calls)
    dialog.search_input.setText("Club 1099")
    dialog.show()
    qtbot.waitUntil(lambda: (1099, True) in provider.calls)
    assert {team_id for team_id, network in provider.calls if network} == {1099}
    dialog.search_input.clear()
    dialog.team_list.scrollToTop()
    qtbot.waitUntil(lambda: (1000, True) in provider.calls)
    assert len({team_id for team_id, network in provider.calls if network}) < 100
    dialog.team_list.scrollToItem(items(dialog)[1050])
    qtbot.waitUntil(lambda: (1050, True) in provider.calls)
    assert dialog.selected_teams() == {1000: "Club 1000"}
