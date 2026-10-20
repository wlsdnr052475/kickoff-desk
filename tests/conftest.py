"""All tests are offline, including on machines with a real runtime API key."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import requests  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_or_real_credentials(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)

    def blocked(*args, **kwargs):
        raise AssertionError("Tests must use mock HTTP responses")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
