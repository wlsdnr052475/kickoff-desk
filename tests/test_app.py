"""Run entry-point event loops in subprocesses to isolate pytest-qt's QApplication."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def run_app_script(source, tmp_path):
    environment = os.environ.copy()
    environment.pop("FOOTBALL_DATA_API_KEY", None)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["LOCALAPPDATA"] = str(tmp_path)
    environment["XDG_DATA_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_smoke_success_returns_zero_without_credentials_or_network(tmp_path):
    run_app_script(
        """
        import requests
        from PySide6.QtNetwork import QNetworkAccessManager
        from kickoff_desk import app, ui

        def forbidden(*args, **kwargs):
            raise AssertionError('Smoke tests must not resolve credentials or access the network')

        ui.KeyManager = forbidden
        requests.sessions.Session.request = forbidden
        QNetworkAccessManager.get = forbidden
        assert app.main(['--smoke-test']) == 0
        """,
        tmp_path,
    )


def test_smoke_fails_when_packaged_crests_are_missing(tmp_path):
    run_app_script(
        """
        from pathlib import Path
        from kickoff_desk import app, logos

        logos._BUNDLED = Path('missing-crest-package')
        assert app.main(['--smoke-test']) == 1
        """,
        tmp_path,
    )


def test_smoke_timeout_waits_for_worker_before_cleanup(tmp_path):
    run_app_script(
        """
        import threading
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from kickoff_desk import app
        from kickoff_desk.service import LoadResult

        release = threading.Event()
        retained_windows = []
        directories = []
        directories_during_work = []
        original_window = app.MainWindow

        class DelayedService:
            def load_month(self, year, month, refresh):
                release.wait(timeout=3)
                directories_during_work.append(directories[0].exists())
                return LoadResult(year, month, [], None, 'mock')

        class FastTimeoutTimer(QTimer):
            def start(self, interval):
                super().start(20 if interval == 10000 else interval)

        def retained_window(store, **kwargs):
            directories.append(store.directory)
            window = original_window(store, service_factory=lambda _: DelayedService(), **kwargs)
            retained_windows.append(window)
            QTimer.singleShot(150, release.set)
            return window

        app.QTimer = FastTimeoutTimer
        app.MainWindow = retained_window
        code = app.main(['--smoke-test'])
        window = retained_windows[0]
        worker = window._worker
        worker_was_running = worker is not None and worker.isRunning()
        # Clean up safely even if this regression reappears, so pytest reports
        # the assertion rather than a QThread-destructor process abort.
        release.set()
        if worker is not None:
            worker.wait(4000)
        QApplication.processEvents()
        window.close()
        QApplication.processEvents()
        assert code == 2, code
        assert not worker_was_running, 'main returned before the worker finished'
        assert directories_during_work == [True], 'temporary storage was removed during work'
        assert not directories[0].exists(), 'temporary storage was not cleaned up after shutdown'
        """,
        tmp_path,
    )
