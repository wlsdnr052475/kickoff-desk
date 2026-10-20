import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from kickoff_desk.logos import CrestProvider

URL = "https://crests.football-data.org/999.png"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="90">'
    b'<path fill="red" d="M0 0H60V90H0Z"/></svg>'
)


def png_bytes():
    image = QImage(40, 60, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff0000"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


class FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.payload = b""
        self.status = 200
        self.failure = QNetworkReply.NetworkError.NoError
        self.aborted = False

    def setReadBufferSize(self, size):
        self.buffer_size = size

    def readAll(self):
        value, self.payload = self.payload, b""
        return QByteArray(value)

    def error(self):
        return self.failure

    def attribute(self, attribute):
        assert attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute
        return self.status

    def abort(self):
        self.aborted = True
        self.failure = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()

    def complete(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.readyRead.emit()
        if not self.aborted:
            self.finished.emit()


class FakeNetwork(QObject):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(request)
        reply = FakeReply()
        self.replies.append(reply)
        return reply


@pytest.fixture
def provider(qtbot, tmp_path):
    network = FakeNetwork()
    instance = CrestProvider(tmp_path, network_manager=network)
    yield instance, network
    instance.close()


def test_mock_uses_real_bundled_crests_without_network(provider):
    crests, network = provider
    fallback = crests.icon(None).cacheKey()
    keys = set()
    for team_id in (5, 57, 65, 73, 81, 86):
        icon = crests.icon(team_id, URL)
        assert not icon.isNull()
        assert icon.cacheKey() != fallback
        keys.add(icon.cacheKey())
    assert len(keys) == 6
    assert not network.requests


@pytest.mark.parametrize("team_id", [None, -1, 0, True, "../key"])
def test_unknown_team_has_neutral_fallback(provider, team_id):
    crests, network = provider
    assert not crests.icon(team_id, URL, allow_network=True).isNull()
    assert not network.requests


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "http://crests.football-data.org/9.png",
        "https://example.com/9.png",
        URL + "?token=secret",
    ],
)
def test_rejects_non_public_crest_urls(provider, payload):
    crests, network = provider
    crests.icon(999, payload, allow_network=True)
    assert not network.requests


@pytest.mark.parametrize("image_bytes", [png_bytes, lambda: SVG])
def test_download_caches_png_and_restores_offline(provider, tmp_path, qtbot, image_bytes):
    crests, network = provider
    old_icon = crests.icon(999, URL, allow_network=True)
    request = network.requests[0]
    headers = [bytes(header).lower() for header in request.rawHeaderList()]
    assert b"x-auth-token" not in headers
    assert b"authorization" not in headers
    assert request.url().toString() == URL
    assert request.transferTimeout() == 5000
    assert (
        request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute)
        == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    )
    with qtbot.waitSignal(crests.updated) as signal:
        network.replies[0].complete(image_bytes())
    assert signal.args == [999]
    assert crests.icon(999).cacheKey() != old_icon.cacheKey()
    cache = tmp_path / "crests" / "999.png"
    assert cache.read_bytes().startswith(b"\x89PNG")
    assert QImage(str(cache)).size().height() <= 96
    second_network = FakeNetwork()
    restored = CrestProvider(tmp_path, network_manager=second_network)
    assert restored.icon(999, URL, allow_network=True).cacheKey() != restored.icon(None).cacheKey()
    assert not second_network.requests
    restored.close()


@pytest.mark.parametrize(
    "payload,status",
    [
        (b"not an image", 200),
        (b"x" * (512 * 1024 + 1), 200),
        (SVG, 302),
        (SVG, 404),
        (b'<!DOCTYPE svg [<!ENTITY a "x">]>' + SVG, 200),
        (SVG.replace(b"<path", b'<image href="file:///tmp/private"/><path'), 200),
        (SVG.replace(b"<path", b"<script>alert(1)</script><path"), 200),
        (SVG.replace(b'fill="red"', b'fill="url(https://example.com/x)"'), 200),
        (SVG.replace(b'width="60"', b'width="20000"'), 200),
        (b'<?xml version="1.0" encoding="unknown-charset"?>' + SVG, 200),
        (('<!DOCTYPE svg [<!ENTITY a "x">]>' + SVG.decode()).encode("utf-16"), 200),
        (
            SVG.replace(b'fill="red"', b'style="fill:url(#ok);stroke:url(https://example.com)"'),
            200,
        ),
    ],
)
def test_bad_download_keeps_name_fallback_and_does_not_repeat(provider, tmp_path, payload, status):
    crests, network = provider
    before = crests.icon(999, URL, allow_network=True)
    network.replies[0].complete(payload, status)
    assert crests.icon(999, URL, allow_network=True).cacheKey() == before.cacheKey()
    assert not (tmp_path / "crests" / "999.png").exists()
    assert len(network.requests) == 1


def test_corrupt_cache_recovers_without_crashing(provider, tmp_path):
    cache = tmp_path / "crests"
    cache.mkdir()
    (cache / "57.png").write_bytes(b"corrupt")
    crests, network = provider
    assert crests.icon(57).cacheKey() != crests.icon(None).cacheKey()
    assert not network.requests


def test_failed_disk_write_still_displays_download(provider, tmp_path):
    (tmp_path / "crests").write_text("not a directory")
    crests, network = provider
    before = crests.icon(999, URL, allow_network=True)
    network.replies[0].complete(png_bytes())
    assert crests.icon(999).cacheKey() != before.cacheKey()


def test_queue_limits_concurrency_deduplicates_and_cancels(provider):
    crests, network = provider
    for team_id in range(100, 106):
        for _ in range(2):
            crests.icon(team_id, URL, allow_network=True)
    assert len(network.requests) == 3
    network.replies[0].complete(png_bytes())
    assert len(network.requests) == 4
    crests.close()
    assert all(reply.aborted for reply in network.replies[1:])
    crests.icon(500, URL, allow_network=True)
    assert len(network.requests) == 4
    assert not crests._active


def test_network_failure_does_not_poison_existing_bundled_icon(provider):
    crests, network = provider
    before = crests.icon(57, URL, allow_network=True)
    network.replies[0].failure = QNetworkReply.NetworkError.HostNotFoundError
    network.replies[0].complete(b"")
    assert crests.icon(57).cacheKey() == before.cacheKey()
