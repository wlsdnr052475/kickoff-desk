from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTableView,
    QTableWidget,
    QVBoxLayout,
)

from kickoff_desk.personalization import PersonalizationDialog, theme_stylesheet
from kickoff_desk.settings import Preferences


def test_dialog_applies_only_edited_fields_to_latest_preferences(qtbot):
    original = Preferences({64}, False, False, {64: "Liverpool FC"})
    dialog = PersonalizationDialog(original)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
    dialog.font_size_spin.setValue(18)
    dialog.notifications_checkbox.setChecked(True)
    dialog.notification_minutes_combo.setCurrentIndex(
        dialog.notification_minutes_combo.findData(60)
    )
    dialog.minimize_to_tray_checkbox.setChecked(True)
    latest = replace(
        original,
        ignore_environment_api_key=True,
        selected_competitions={"CL"},
        favorites_only=True,
        window_geometry="YWJjZA==",
        custom_team_names={64: "우리 팀"},
    )
    qtbot.mouseClick(
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton
    )
    assert dialog.result() == QDialog.DialogCode.Accepted
    applied = dialog.apply_to(latest)
    assert applied == replace(
        latest,
        theme="dark",
        font_size=18,
        notifications_enabled=True,
        notification_minutes=60,
        minimize_to_tray=True,
    )
    assert applied is not latest
    assert original == Preferences({64}, False, False, {64: "Liverpool FC"})
    assert latest.theme == "light"


def test_cancel_and_preview_do_not_mutate_preferences_or_parent(qtbot):
    parent = QDialog()
    qtbot.addWidget(parent)
    original_style = theme_stylesheet("light", 13)
    parent.setStyleSheet(original_style)
    preferences = Preferences()
    dialog = PersonalizationDialog(preferences, parent)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
    dialog.font_size_spin.setValue(20)
    dialog.notifications_checkbox.setChecked(True)
    assert parent.styleSheet() == original_style
    assert dialog.styleSheet() == theme_stylesheet("dark", 20)
    qtbot.mouseClick(
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton
    )
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert preferences == Preferences()


def test_dialog_loads_saved_options_and_bounds_controls(qtbot):
    preferences = Preferences(
        theme="dark",
        font_size=11,
        notifications_enabled=True,
        notification_minutes=10,
        minimize_to_tray=True,
    )
    dialog = PersonalizationDialog(preferences)
    qtbot.addWidget(dialog)
    assert dialog.apply_to(preferences) == preferences
    assert dialog.notification_minutes_combo.isEnabled()
    dialog.notifications_checkbox.setChecked(False)
    assert not dialog.notification_minutes_combo.isEnabled()
    assert dialog.font_size_spin.minimum() == 11
    assert dialog.font_size_spin.maximum() == 20
    assert [dialog.notification_minutes_combo.itemData(i) for i in range(3)] == [10, 30, 60]


def contrast(first, second):
    def luminance(color):
        channels = [channel / 255 for channel in (color.red(), color.green(), color.blue())]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    levels = sorted([luminance(first), luminance(second)])
    return (levels[1] + 0.05) / (levels[0] + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_stylesheet_produces_readable_input_table_and_calendar(qtbot, theme):
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setStyleSheet(theme_stylesheet(theme, 20))
    layout = QVBoxLayout(dialog)
    field = QLineEdit("글자 크기 확인")
    table = QTableWidget(1, 1)
    calendar = QCalendarWidget()
    for widget in (field, table, calendar):
        layout.addWidget(widget)
    dialog.show()
    calendar_view = calendar.findChild(QTableView)
    assert calendar_view is not None
    for widget in (field, table, calendar_view):
        palette = widget.palette()
        assert (
            contrast(palette.color(QPalette.ColorRole.Text), palette.color(QPalette.ColorRole.Base))
            >= 4.5
        )
    assert (
        contrast(
            field.palette().color(QPalette.ColorRole.HighlightedText),
            field.palette().color(QPalette.ColorRole.Highlight),
        )
        >= 4.5
    )
    assert field.font().pixelSize() == 20
    assert table.font().pixelSize() == 20


@pytest.mark.parametrize(
    "theme,size", [("unknown", 13), ("light", 10), ("dark", 21), ("dark", True)]
)
def test_stylesheet_rejects_invalid_options(theme, size):
    with pytest.raises(ValueError, match="설정"):
        theme_stylesheet(theme, size)
