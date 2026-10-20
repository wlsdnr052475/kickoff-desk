"""Small, optional team crests; never share the authenticated API session."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QRectF,
    QSaveFile,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtSvg import QSvgRenderer

from .models import normalize_crest_url

_MAX_BYTES = 512 * 1024
_MAX_DIMENSION = 2048
_ICON_SIZE = 96
_MAX_DOWNLOADS = 3
_BUNDLED = Path(__file__).parent / "assets" / "crests"


def _decode_image(data: bytes) -> QImage | None:
    """Decode bounded PNG/SVG input into a small raster image before caching."""
    if not data or len(data) > _MAX_BYTES:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"png")
        size = reader.size()
        if not size.isValid() or size.width() > _MAX_DIMENSION or size.height() > _MAX_DIMENSION:
            return None
        reader.setScaledSize(
            size.scaled(QSize(_ICON_SIZE, _ICON_SIZE), Qt.AspectRatioMode.KeepAspectRatio)
        )
        image = reader.read()
        return None if image.isNull() else image

    # Reject entities, resource references, and active content before Qt sees SVG.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered or "<?xml-stylesheet" in lowered:
        return None
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError, LookupError):
        return None
    if root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
        return None
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"image", "script", "foreignobject", "style"}:
            return None
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1].lower() in {"href", "src"} and not value.startswith("#"):
                return None
            for reference in re.findall(r"url\s*\((.*?)\)", value, flags=re.IGNORECASE):
                if not reference.strip().strip("\"'").startswith("#"):
                    return None
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        return None
    renderer.setAnimationEnabled(False)
    size = renderer.defaultSize()
    if not size.isValid() or size.width() > _MAX_DIMENSION or size.height() > _MAX_DIMENSION:
        return None
    size.scale(QSize(_ICON_SIZE, _ICON_SIZE), Qt.AspectRatioMode.KeepAspectRatio)
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(image.rect()))
    painter.end()
    return image


def _fallback_icon() -> QIcon:
    image = QImage(_ICON_SIZE, _ICON_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shield = QPainterPath()
    shield.moveTo(22, 16)
    shield.lineTo(74, 16)
    shield.lineTo(74, 46)
    shield.cubicTo(74, 66, 58, 77, 48, 83)
    shield.cubicTo(38, 77, 22, 66, 22, 46)
    shield.closeSubpath()
    painter.fillPath(shield, QColor("#c5d1dc"))
    painter.setPen(QColor("#667f94"))
    painter.drawPath(shield)
    painter.end()
    return QIcon(QPixmap.fromImage(image))


@dataclass
class _Download:
    team_id: int
    timer: QTimer
    data: bytearray = field(default_factory=bytearray)
    rejected: bool = False


class CrestProvider(QObject):
    """Return icons immediately and update live crests asynchronously on the GUI thread."""

    updated = Signal(int)

    def __init__(
        self,
        directory: Path,
        parent: QObject | None = None,
        *,
        network_manager: QNetworkAccessManager | None = None,
    ):
        super().__init__(parent)
        self.directory = Path(directory) / "crests"
        self._network = network_manager
        self._icons: dict[int, QIcon] = {}
        self._cached: set[int] = set()
        self._attempted: set[tuple[int, str]] = set()
        self._queue: deque[tuple[int, str]] = deque()
        self._active: dict[QNetworkReply, _Download] = {}
        self._closed = False
        self._fallback = _fallback_icon()

    def icon(
        self,
        team_id: int | None,
        crest_url: str | None = None,
        *,
        allow_network: bool = False,
    ) -> QIcon:
        if isinstance(team_id, bool) or not isinstance(team_id, int) or team_id <= 0:
            return self._fallback
        if team_id not in self._icons:
            self._icons[team_id] = self._fallback
            for path in (self.directory / f"{team_id}.png", _BUNDLED / f"{team_id}.png"):
                try:
                    with path.open("rb") as handle:
                        image = _decode_image(handle.read(_MAX_BYTES + 1))
                except OSError:
                    continue
                if image is not None:
                    self._icons[team_id] = QIcon(QPixmap.fromImage(image))
                    if path.parent == self.directory:
                        self._cached.add(team_id)
                    break
        url = normalize_crest_url(crest_url)
        if (
            allow_network
            and url
            and team_id not in self._cached
            and (team_id, url) not in self._attempted
            and not self._closed
        ):
            self._attempted.add((team_id, url))
            self._queue.append((team_id, url))
            self._pump()
        return self._icons[team_id]

    def _pump(self) -> None:
        while self._queue and len(self._active) < _MAX_DOWNLOADS and not self._closed:
            if self._network is None:
                self._network = QNetworkAccessManager(self)
            team_id, url = self._queue.popleft()
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
            )
            request.setTransferTimeout(5000)
            request.setRawHeader(b"Accept", b"image/png, image/svg+xml")
            reply = self._network.get(request)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(reply.abort)
            self._active[reply] = _Download(team_id, timer)
            reply.setReadBufferSize(_MAX_BYTES + 1)
            reply.readyRead.connect(lambda reply=reply: self._read(reply))
            reply.finished.connect(lambda reply=reply: self._finished(reply))
            timer.start(5000)

    def _read(self, reply: QNetworkReply) -> None:
        download = self._active.get(reply)
        if download is None or download.rejected:
            return
        data = bytes(reply.readAll())
        if len(download.data) + len(data) > _MAX_BYTES:
            download.rejected = True
            reply.abort()
            return
        download.data.extend(data)

    def _finished(self, reply: QNetworkReply) -> None:
        self._read(reply)
        download = self._active.pop(reply, None)
        if download is None:
            return
        download.timer.stop()
        download.timer.deleteLater()
        if (
            not self._closed
            and not download.rejected
            and reply.error() == QNetworkReply.NetworkError.NoError
            and reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200
        ):
            image = _decode_image(bytes(download.data))
            if image is not None:
                self._icons[download.team_id] = QIcon(QPixmap.fromImage(image))
                self._save(download.team_id, image)
                self.updated.emit(download.team_id)
        reply.deleteLater()
        self._pump()

    def _save(self, team_id: int, image: QImage) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        file = QSaveFile(str(self.directory / f"{team_id}.png"))
        if file.open(QIODevice.OpenModeFlag.WriteOnly):
            if image.save(file, "PNG") and file.commit():
                self._cached.add(team_id)
            else:
                file.cancelWriting()

    def close(self) -> None:
        """Cancel outstanding decoration requests without delaying application exit."""
        self._closed = True
        self._queue.clear()
        for reply in list(self._active):
            reply.abort()
