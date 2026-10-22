"""Run entry-point event loops in subprocesses to isolate pytest-qt's QApplication."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest


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
        from kickoff_desk import app, logos, ui

        def forbidden(*args, **kwargs):
            raise AssertionError('Smoke tests must not resolve credentials or access the network')

        ui.KeyManager = forbidden
        requests.sessions.Session.request = forbidden
        QNetworkAccessManager.get = forbidden
        logos.QNetworkAccessManager = forbidden
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


@pytest.mark.parametrize("with_worker", [False, True], ids=["idle", "active-worker"])
def test_hidden_tray_quit_exits_event_loop_after_worker_cleanup(tmp_path, with_worker):
    run_app_script(
        f"with_worker = {with_worker!r}\n"
        + textwrap.dedent(
            """
            import threading
            import requests
            from PySide6.QtCore import QObject, QTimer, Signal
            from PySide6.QtWidgets import QApplication
            from kickoff_desk import ui
            from kickoff_desk.service import LoadResult
            from kickoff_desk.settings import Preferences, SettingsStore

            def forbidden(*args, **kwargs):
                raise AssertionError('Tray lifecycle tests must not access API or credentials')

            requests.sessions.Session.request = forbidden
            ui.KeyManager = forbidden
            application = QApplication([])
            original_quit_setting = application.quitOnLastWindowClosed()
            entered = threading.Event()
            release = threading.Event()
            worker_completed = threading.Event()
            state = {'watchdog': False}

            class FakeNotifications(QObject):
                restore_requested = Signal()
                quit_requested = Signal()
                warnings = Signal(str)

                def __init__(self):
                    super().__init__()
                    self.hide_calls = 0
                    self.close_calls = 0
                    self.closed_after_worker = None

                def update(self, matches, preferences, use_mock):
                    pass

                def hide_to_tray(self):
                    self.hide_calls += 1
                    application.setQuitOnLastWindowClosed(False)
                    return True

                def close(self):
                    self.close_calls += 1
                    self.closed_after_worker = worker_completed.is_set()
                    application.setQuitOnLastWindowClosed(original_quit_setting)

            class DelayedService:
                def load_month(self, year, month, refresh):
                    entered.set()
                    release.wait(timeout=4)
                    worker_completed.set()
                    return LoadResult(year, month, [], None, 'mock')

            store = SettingsStore()
            store.save(Preferences(minimize_to_tray=True))
            notifications = FakeNotifications()
            window = ui.MainWindow(
                store, auto_load=False, use_mock=True,
                notification_controller=notifications,
                service_factory=lambda _: DelayedService(),
            )
            window.show()

            def hide_and_quit():
                if with_worker and not entered.is_set():
                    QTimer.singleShot(5, hide_and_quit)
                    return
                state['shown_before_close'] = window.isVisible()
                state['close_result'] = window.close()
                state['hidden'] = not window.isVisible()
                state['quit_on_close_while_hidden'] = application.quitOnLastWindowClosed()
                notifications.quit_requested.emit()
                state['loading_after_quit'] = window.is_loading
                state['closed_after_quit'] = notifications.close_calls
                if with_worker:
                    # A queued release proves quit keeps processing events until
                    # the worker finishes instead of exiting while it is active.
                    QTimer.singleShot(50, release.set)

            def begin():
                if with_worker:
                    window.load_month()
                hide_and_quit()

            def watchdog():
                state['watchdog'] = True
                release.set()
                application.exit(9)

            watchdog_timer = QTimer()
            watchdog_timer.setSingleShot(True)
            watchdog_timer.timeout.connect(watchdog)
            watchdog_timer.start(5000)
            QTimer.singleShot(0, begin)
            code = application.exec()
            watchdog_timer.stop()
            state['loading_on_return'] = window.is_loading
            state['controller_closes_on_return'] = notifications.close_calls
            state['worker_completed_on_return'] = worker_completed.is_set()

            # Keep a failure from leaving a QThread alive during interpreter
            # shutdown, so the parent receives an assertion instead of a crash.
            release.set()
            worker = window._worker
            if worker is not None:
                worker.wait(5000)
            QApplication.processEvents()
            window.preferences.minimize_to_tray = False
            window.close()
            QApplication.processEvents()

            assert code == 0, state
            assert not state['watchdog'], state
            assert state['shown_before_close'] and state['hidden'], state
            assert state['close_result'] is False, state
            assert state['quit_on_close_while_hidden'] is False, state
            assert notifications.hide_calls == 1, state
            assert not state['loading_on_return'], state
            assert state['controller_closes_on_return'] == 1, state
            assert notifications.close_calls == 1, state
            assert application.quitOnLastWindowClosed() == original_quit_setting, state
            if with_worker:
                assert state['loading_after_quit'], state
                assert state['closed_after_quit'] == 0, state
                assert state['worker_completed_on_return'], state
                assert notifications.closed_after_worker, state
            """
        ),
        tmp_path,
    )
