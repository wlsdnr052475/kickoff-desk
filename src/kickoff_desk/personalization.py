"""Appearance and notification preferences without persistence side effects."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .settings import Preferences


def theme_stylesheet(theme: str, font_size: int) -> str:
    """Return a complete, bounded Qt stylesheet for the selected appearance."""
    if theme not in {"light", "dark"} or type(font_size) is not int or not 11 <= font_size <= 20:
        raise ValueError("화면 설정이 올바르지 않습니다.")
    if theme == "dark":
        colors = {
            "background": "#18232e",
            "surface": "#223241",
            "text": "#e5edf5",
            "muted": "#b2c1d0",
            "border": "#52687a",
            "hover": "#334b5e",
            "disabled": "#92a4b5",
            "disabled_bg": "#2b3b49",
            "accent": "#217d70",
            "accent_hover": "#237f71",
            "selected": "#285c56",
            "alternate": "#293b4b",
            "header": "#2b4052",
            "notice": "#493d23",
            "notice_text": "#ffe5a6",
            "demo": "#204a43",
            "demo_text": "#b0f0db",
            "grid": "#344858",
        }
    else:
        colors = {
            "background": "#f2f5f8",
            "surface": "#ffffff",
            "text": "#162b41",
            "muted": "#566b7e",
            "border": "#c0cdd9",
            "hover": "#edf4f8",
            "disabled": "#687e92",
            "disabled_bg": "#e8eef3",
            "accent": "#137969",
            "accent_hover": "#096251",
            "selected": "#deefeb",
            "alternate": "#f5f8fa",
            "header": "#eaf0f5",
            "notice": "#fff2d5",
            "notice_text": "#775000",
            "demo": "#e0f4ef",
            "demo_text": "#126554",
            "grid": "#ecf0f4",
        }
    return f"""
QMainWindow, QDialog {{ background: {colors["background"]}; color: {colors["text"]}; }}
QWidget {{ font-family: "Malgun Gothic", "Noto Sans CJK KR", sans-serif;
           font-size: {font_size}px; color: {colors["text"]}; }}
QWidget:disabled {{ color: {colors["disabled"]}; }}
QLabel#title {{ font-size: {font_size + 16}px; font-weight: 700; }}
QLabel#subtitle, QLabel#metadata {{ color: {colors["muted"]}; }}
QLabel#section {{ font-size: {font_size + 4}px; font-weight: 700; }}
QLabel#notice {{ background: {colors["notice"]}; color: {colors["notice_text"]};
                padding: 11px; border-radius: 7px; }}
QLabel#demo {{ background: {colors["demo"]}; color: {colors["demo_text"]};
              padding: 10px; border-radius: 7px; }}
QFrame#panel {{ background: {colors["surface"]}; border-radius: 10px; }}
QPushButton, QToolButton {{ background: {colors["surface"]}; color: {colors["text"]};
    border: 1px solid {colors["border"]}; border-radius: 6px; padding: 8px 13px; }}
QPushButton:hover, QToolButton:hover {{ background: {colors["hover"]}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {colors["disabled"]};
    background: {colors["disabled_bg"]}; }}
QPushButton#primary {{ background: {colors["accent"]}; color: white;
                       border-color: {colors["accent"]}; }}
QPushButton#primary:hover {{ background: {colors["accent_hover"]}; }}
QPushButton#primary:disabled {{ background: {colors["disabled_bg"]};
    color: {colors["disabled"]}; border-color: {colors["border"]}; }}
QCheckBox {{ spacing: 7px; padding: 5px 0; }}
QLineEdit, QListWidget, QComboBox, QSpinBox, QTextEdit {{ background: {colors["surface"]};
    color: {colors["text"]}; border: 1px solid {colors["border"]}; border-radius: 5px;
    padding: 7px; selection-background-color: {colors["accent"]}; selection-color: white; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {colors["disabled_bg"]}; color: {colors["disabled"]}; }}
QComboBox QAbstractItemView {{ background: {colors["surface"]}; color: {colors["text"]};
    selection-background-color: {colors["accent"]}; selection-color: white; }}
QListWidget::indicator:unchecked {{ border: 1px solid {colors["muted"]};
    background: {colors["surface"]}; border-radius: 2px; }}
QListWidget::indicator:unchecked:hover {{ background: {colors["hover"]}; }}
QTableWidget, QTableView {{ background: {colors["surface"]};
    alternate-background-color: {colors["alternate"]}; color: {colors["text"]}; border: none;
    selection-background-color: {colors["selected"]}; selection-color: {colors["text"]};
    gridline-color: {colors["grid"]}; }}
QHeaderView::section, QTableCornerButton::section {{ background: {colors["header"]};
    color: {colors["text"]}; border: none; padding: 10px 6px; font-weight: 600; }}
QCalendarWidget QWidget {{ alternate-background-color: {colors["header"]}; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {colors["header"]}; }}
QCalendarWidget QAbstractItemView {{ background: {colors["surface"]}; color: {colors["text"]};
    selection-background-color: {colors["accent"]}; selection-color: white; border: none; }}
QCalendarWidget QToolButton {{ background: {colors["header"]}; color: {colors["text"]};
                              border: none; padding: 6px; }}
QCalendarWidget QSpinBox {{ background: {colors["surface"]}; color: {colors["text"]}; }}
QMenu, QToolTip {{ background: {colors["surface"]}; color: {colors["text"]};
                  border: 1px solid {colors["border"]}; }}
QMenu::item:selected {{ background: {colors["accent"]}; color: white; }}
QMenu::item:disabled {{ color: {colors["disabled"]}; }}
QScrollBar {{ background: {colors["background"]}; }}
QScrollBar::handle {{ background: {colors["border"]}; border-radius: 4px; min-height: 20px; }}
QSplitter::handle {{ background: {colors["background"]}; }}
"""


class PersonalizationDialog(QDialog):
    """Edit a copy; callers apply changes to their latest preferences on acceptance."""

    def __init__(self, preferences: Preferences, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("화면 및 알림 설정 — Kickoff Desk")
        self.setMinimumWidth(440)
        self.setStyleSheet(theme_stylesheet(preferences.theme, preferences.font_size))
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        form = QFormLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("화면 테마")
        self.theme_combo.addItem("밝은 화면", "light")
        self.theme_combo.addItem("어두운 화면", "dark")
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(preferences.theme))
        form.addRow("테마", self.theme_combo)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setAccessibleName("글자 크기")
        self.font_size_spin.setRange(11, 20)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setValue(preferences.font_size)
        form.addRow("글자 크기", self.font_size_spin)
        self.notifications_checkbox = QCheckBox("관심 팀 경기 시작 전 알림")
        self.notifications_checkbox.setChecked(preferences.notifications_enabled)
        form.addRow(self.notifications_checkbox)
        self.notification_minutes_combo = QComboBox()
        self.notification_minutes_combo.setAccessibleName("경기 전 알림 시간")
        for minutes in (10, 30, 60):
            self.notification_minutes_combo.addItem(f"{minutes}분 전", minutes)
        self.notification_minutes_combo.setCurrentIndex(
            self.notification_minutes_combo.findData(preferences.notification_minutes)
        )
        self.notification_minutes_combo.setEnabled(preferences.notifications_enabled)
        self.notifications_checkbox.toggled.connect(self.notification_minutes_combo.setEnabled)
        form.addRow("알림 시점", self.notification_minutes_combo)
        self.minimize_to_tray_checkbox = QCheckBox("창을 닫으면 트레이에 두기")
        self.minimize_to_tray_checkbox.setChecked(preferences.minimize_to_tray)
        form.addRow(self.minimize_to_tray_checkbox)
        layout.addLayout(form)
        note = QLabel(
            "알림은 프로그램이 실행 중일 때 표시됩니다. "
            "알림과 트레이 지원은 Windows 설정에 따라 달라질 수 있습니다."
        )
        note.setWordWrap(True)
        note.setObjectName("metadata")
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.theme_combo.currentIndexChanged.connect(self._preview)
        self.font_size_spin.valueChanged.connect(self._preview)

    def _preview(self) -> None:
        self.setStyleSheet(
            theme_stylesheet(self.theme_combo.currentData(), self.font_size_spin.value())
        )

    def apply_to(self, preferences: Preferences) -> Preferences:
        """Copy only edited fields, preserving filters, names, geometry and key policy."""
        return replace(
            preferences,
            theme=self.theme_combo.currentData(),
            font_size=self.font_size_spin.value(),
            notifications_enabled=self.notifications_checkbox.isChecked(),
            notification_minutes=self.notification_minutes_combo.currentData(),
            minimize_to_tray=self.minimize_to_tray_checkbox.isChecked(),
        )
