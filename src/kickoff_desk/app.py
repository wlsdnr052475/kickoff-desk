"""Desktop entry point. Smoke runs never resolve credentials or contact the API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .settings import SettingsStore
from .ui import MainWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kickoff Desk — 한국시간 축구 일정표")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="키 없이 데모 일정 사용")
    mode.add_argument("--live", action="store_true", help="football-data.org API 일정 사용")
    parser.add_argument(
        "--smoke-test", action="store_true", help="격리된 Mock GUI 실행 점검 후 종료"
    )
    args = parser.parse_args(argv)
    use_mock = True if args.mock or args.smoke_test else False if args.live else None

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("Kickoff Desk")
    application.setOrganizationName("KickoffDesk")
    application.setStyle("Fusion")
    temporary = TemporaryDirectory(prefix="kickoff-desk-smoke-") if args.smoke_test else None
    store = SettingsStore(Path(temporary.name)) if temporary is not None else SettingsStore()
    window = MainWindow(store, use_mock=use_mock)
    smoke_exit_code = 2

    if args.smoke_test:
        smoke_timed_out = False
        smoke_timer = QTimer(window)
        smoke_timer.setSingleShot(True)

        def finish(result):
            nonlocal smoke_exit_code
            # A visible window and actual data must both be present; startup alone is insufficient.
            if not smoke_timed_out:
                passed = result.source == "mock" and bool(result.matches) and window.isVisible()
                smoke_exit_code = 0 if passed else 1
            smoke_timer.stop()
            window.close()

        def timeout():
            nonlocal smoke_timed_out
            smoke_timed_out = True
            # Closing is deferred by MainWindow while its worker is active.
            # Keep the event loop and temporary directory alive until it finishes.
            window.close()

        window.load_completed.connect(finish)
        smoke_timer.timeout.connect(timeout)
        smoke_timer.start(10_000)

    window.show()
    code = application.exec()
    # Both normal closing and smoke timeouts wait for MainWindow's worker.
    window.close()
    if temporary is not None:
        temporary.cleanup()
    return smoke_exit_code if args.smoke_test else code
