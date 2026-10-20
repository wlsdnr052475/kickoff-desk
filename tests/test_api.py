from datetime import date, datetime, timezone

import pytest
import requests

from kickoff_desk.api import (
    ApiClient,
    ApiError,
    AuthenticationError,
    InvalidResponseError,
    NetworkError,
    RateLimitError,
)

TEST_TOKEN = "test-only-placeholder"
START = date(2026, 10, 1)
END = date(2026, 10, 31)


def raw_match(code="PL", identifier=1):
    return {
        "id": identifier,
        "competition": {"code": code},
        "homeTeam": {"id": 57, "name": "Home"},
        "awayTeam": {"id": 73, "name": "Away"},
        "utcDate": "2026-10-16T20:00:00Z",
        "status": "TIMED",
        "score": {"fullTime": {"home": 1, "away": 0}},
    }


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = {"matches": []} if payload is None else payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_fetches_three_competitions_using_header_auth_and_safe_transport():
    session = FakeSession(
        [
            FakeResponse(payload={"matches": [raw_match("PL", 1), raw_match("PL", 1)]}),
            FakeResponse(payload={"matches": [raw_match("PD", 2)]}),
            FakeResponse(payload={"matches": [raw_match("CL", 3)]}),
        ]
    )
    matches = ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert len(matches) == 3
    assert {match.competition for match in matches} == {"PL", "PD", "CL"}
    for (url, kwargs), competition in zip(session.calls, ("PL", "PD", "CL"), strict=True):
        assert url == f"https://api.football-data.org/v4/competitions/{competition}/matches"
        assert kwargs["headers"]["X-Auth-Token"] == TEST_TOKEN
        assert kwargs["params"] == {"dateFrom": "2026-10-01", "dateTo": "2026-10-31"}
        assert kwargs["timeout"] == (5, 15)
        assert kwargs["allow_redirects"] is False
        assert TEST_TOKEN not in url
        assert TEST_TOKEN not in str(kwargs["params"])
    assert all("score" not in match.to_dict() for match in matches)


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_errors_are_safe_and_not_retried(status):
    session = FakeSession([FakeResponse(status=status, payload={"message": TEST_TOKEN})])
    with pytest.raises(AuthenticationError) as error:
        ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert TEST_TOKEN not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "status, error_class",
    [(500, ApiError), (503, ApiError), (400, InvalidResponseError), (302, InvalidResponseError)],
)
def test_http_failures_never_disclose_server_text(status, error_class):
    session = FakeSession([FakeResponse(status=status, payload={"message": TEST_TOKEN})])
    with pytest.raises(error_class) as error:
        ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert TEST_TOKEN not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "exception", [requests.Timeout(TEST_TOKEN), requests.ConnectionError(TEST_TOKEN)]
)
def test_network_errors_suppress_raw_transport_messages(exception):
    session = FakeSession([exception])
    with pytest.raises(NetworkError) as error:
        ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert TEST_TOKEN not in str(error.value)
    assert error.value.__suppress_context__
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"matches": {}},
        {},
        {"matches": [None]},
        ValueError(TEST_TOKEN),
        {"matches": [raw_match("PD")]},
    ],
)
def test_invalid_payloads_are_rejected_without_raw_details(payload):
    session = FakeSession([FakeResponse(payload=payload)])
    with pytest.raises(InvalidResponseError) as error:
        ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert TEST_TOKEN not in str(error.value)


def test_missing_key_and_invalid_date_range_do_not_send_requests():
    session = FakeSession([])
    client = ApiClient("", session=session)
    with pytest.raises(AuthenticationError):
        client.fetch_matches(START, END)
    with pytest.raises(ValueError):
        client.fetch_matches(END, START)
    assert session.calls == []


def test_registered_key_can_be_changed_and_removed():
    session = FakeSession([FakeResponse(), FakeResponse(), FakeResponse()])
    client = ApiClient(TEST_TOKEN, session=session)
    client.set_api_key("another-test-placeholder")
    client.fetch_matches(START, END)
    assert session.calls[0][1]["headers"]["X-Auth-Token"] == "another-test-placeholder"
    client.set_api_key("")
    with pytest.raises(AuthenticationError):
        client.fetch_matches(START, END)
    assert len(session.calls) == 3


def test_header_control_characters_are_rejected():
    with pytest.raises(AuthenticationError):
        ApiClient("placeholder\r\nInjected: value")


def test_quota_exhaustion_prevents_next_competition_and_preserves_cooldown():
    clock = [100.0]
    session = FakeSession(
        [
            FakeResponse(
                payload={"matches": [raw_match()]},
                headers={"X-Requests-Available-Minute": "0", "X-RequestCounter-Reset": "12"},
            ),
            FakeResponse(),
            FakeResponse(),
            FakeResponse(),
        ]
    )
    client = ApiClient(TEST_TOKEN, session=session, monotonic=lambda: clock[0])
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == 12
    assert len(session.calls) == 1
    clock[0] = 105.1
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == 7
    assert len(session.calls) == 1
    clock[0] = 112.0
    assert client.fetch_matches(START, END) == []
    assert len(session.calls) == 4


def test_last_response_can_exhaust_quota_and_still_return_complete_results():
    session = FakeSession(
        [
            FakeResponse(),
            FakeResponse(),
            FakeResponse(
                headers={"x-requests-available-minute": "0", "x-requestcounter-reset": "9"}
            ),
        ]
    )
    client = ApiClient(TEST_TOKEN, session=session, monotonic=lambda: 100)
    assert client.fetch_matches(START, END) == []
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == 9
    assert len(session.calls) == 3


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"Retry-After": "23"}, 23),
        ({"Retry-After": "8", "X-RequestCounter-Reset": "15"}, 15),
        ({}, 60),
        ({"Retry-After": "invalid", "X-RequestCounter-Reset": "nan"}, 60),
    ],
)
def test_429_cooldown_honors_response_headers(headers, expected):
    session = FakeSession([FakeResponse(status=429, headers=headers)])
    client = ApiClient(TEST_TOKEN, session=session, monotonic=lambda: 100)
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == expected
    with pytest.raises(RateLimitError):
        client.fetch_matches(START, END)
    assert len(session.calls) == 1


def test_retry_after_accepts_http_date(monkeypatch):
    timestamp = datetime(2026, 12, 16, 8, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr("kickoff_desk.api.time.time", lambda: timestamp)
    session = FakeSession(
        [FakeResponse(status=429, headers={"Retry-After": "Wed, 16 Dec 2026 08:00:30 GMT"})]
    )
    client = ApiClient(TEST_TOKEN, session=session, monotonic=lambda: 100)
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == 30


def test_retry_after_on_server_error_is_honored_even_when_quota_remains():
    session = FakeSession(
        [
            FakeResponse(
                status=503, headers={"Retry-After": "10", "X-Requests-Available-Minute": "5"}
            )
        ]
    )
    client = ApiClient(TEST_TOKEN, session=session, monotonic=lambda: 100)
    with pytest.raises(ApiError):
        client.fetch_matches(START, END)
    with pytest.raises(RateLimitError) as error:
        client.fetch_matches(START, END)
    assert error.value.retry_after == 10
    assert len(session.calls) == 1


def test_later_failure_does_not_return_partial_competitions():
    session = FakeSession(
        [
            FakeResponse(payload={"matches": [raw_match()]}),
            FakeResponse(status=503),
        ]
    )
    with pytest.raises(ApiError):
        ApiClient(TEST_TOKEN, session=session).fetch_matches(START, END)
    assert len(session.calls) == 2
