"""Non-secret preferences and optional Windows Credential Manager integration.

API keys are never serialized by this module. The only persistent key backend
used by the application is Windows' native credential vault; other platforms
support an environment variable or a key held in memory for this session.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class SettingsError(Exception):
    """A safe, user-facing preferences error."""


class CredentialError(Exception):
    """A safe, user-facing error that never contains an API key."""


@dataclass
class Preferences:
    favorite_team_ids: set[int] = field(default_factory=set)
    use_mock: bool = True
    ignore_environment_api_key: bool = False
    favorite_team_names: dict[int, str] = field(default_factory=dict)
    theme: str = "light"
    font_size: int = 13
    window_geometry: str = ""
    selected_competitions: set[str] = field(default_factory=lambda: {"PL", "PD", "CL"})
    favorites_only: bool = False
    notifications_enabled: bool = False
    notification_minutes: int = 30
    minimize_to_tray: bool = False
    custom_team_names: dict[int, str] = field(default_factory=dict)


def data_dir() -> Path:
    """Return the per-user application data directory without creating it."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "KickoffDesk"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "kickoff-desk"


def _parse_preferences(payload: object) -> Preferences:
    if not isinstance(payload, dict):
        raise ValueError
    if payload.get("version", 1) != 1:
        raise ValueError
    team_ids = payload.get("favorite_team_ids", [])
    names = payload.get("favorite_team_names", {})
    use_mock = payload.get("use_mock", True)
    ignore_environment = payload.get("ignore_environment_api_key", False)
    if not isinstance(team_ids, list) or any(
        type(item) is not int or item <= 0 for item in team_ids
    ):
        raise ValueError
    if (
        type(use_mock) is not bool
        or type(ignore_environment) is not bool
        or not isinstance(names, dict)
    ):
        raise ValueError
    parsed_names: dict[int, str] = {}
    for team_id, name in names.items():
        if not isinstance(team_id, str) or not team_id.isascii() or not team_id.isdigit():
            raise ValueError
        numeric_id = int(team_id)
        if numeric_id <= 0 or not isinstance(name, str):
            raise ValueError
        parsed_names[numeric_id] = name
    theme = payload.get("theme", "light")
    font_size = payload.get("font_size", 13)
    geometry = payload.get("window_geometry", "")
    competitions = payload.get("selected_competitions", ["PL", "PD", "CL"])
    favorites_only = payload.get("favorites_only", False)
    notifications_enabled = payload.get("notifications_enabled", False)
    notification_minutes = payload.get("notification_minutes", 30)
    minimize_to_tray = payload.get("minimize_to_tray", False)
    custom_names = payload.get("custom_team_names", {})
    if theme not in ("light", "dark") or type(font_size) is not int or not 11 <= font_size <= 20:
        raise ValueError
    if not isinstance(geometry, str) or len(geometry) > 4096:
        raise ValueError
    if geometry:
        base64.b64decode(geometry, validate=True)
    if not isinstance(competitions, list) or any(
        not isinstance(code, str) or code not in {"PL", "PD", "CL"} for code in competitions
    ):
        raise ValueError
    if any(
        type(value) is not bool
        for value in (favorites_only, notifications_enabled, minimize_to_tray)
    ):
        raise ValueError
    if type(notification_minutes) is not int or notification_minutes not in {10, 30, 60}:
        raise ValueError
    if not isinstance(custom_names, dict) or len(custom_names) > 1000:
        raise ValueError
    parsed_custom_names: dict[int, str] = {}
    for team_id, name in custom_names.items():
        if not isinstance(team_id, str) or not team_id.isascii() or not team_id.isdigit():
            raise ValueError
        numeric_id = int(team_id)
        if numeric_id <= 0 or not isinstance(name, str):
            raise ValueError
        trimmed = name.strip()
        if not trimmed or len(trimmed) > 80 or not trimmed.isprintable():
            raise ValueError
        parsed_custom_names[numeric_id] = trimmed
    return Preferences(
        favorite_team_ids=set(team_ids),
        use_mock=use_mock,
        ignore_environment_api_key=ignore_environment,
        favorite_team_names=parsed_names,
        theme=theme,
        font_size=font_size,
        window_geometry=geometry,
        selected_competitions=set(competitions),
        favorites_only=favorites_only,
        notifications_enabled=notifications_enabled,
        notification_minutes=notification_minutes,
        minimize_to_tray=minimize_to_tray,
        custom_team_names=parsed_custom_names,
    )


class SettingsStore:
    """Persist preferences atomically; the schema intentionally has no API key."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else data_dir()
        self.path = self.directory / "settings.json"

    def load(self) -> Preferences:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Preferences()
        except (OSError, UnicodeError):
            raise SettingsError(
                "설정 파일을 읽지 못했습니다. 저장 폴더의 접근 권한을 확인해 주세요."
            ) from None
        try:
            return _parse_preferences(json.loads(content))
        except (ValueError, TypeError, OverflowError):
            raise SettingsError("설정 파일이 손상되었거나 지원하지 않는 형식입니다.") from None

    def save(self, preferences: Preferences) -> None:
        try:
            if any(type(team_id) is not int for team_id in preferences.favorite_team_names):
                raise ValueError
            if any(type(team_id) is not int for team_id in preferences.custom_team_names):
                raise ValueError
            payload = {
                "version": 1,
                "favorite_team_ids": sorted(preferences.favorite_team_ids),
                "favorite_team_names": {
                    str(team_id): name for team_id, name in preferences.favorite_team_names.items()
                },
                "use_mock": preferences.use_mock,
                "ignore_environment_api_key": preferences.ignore_environment_api_key,
                "theme": preferences.theme,
                "font_size": preferences.font_size,
                "window_geometry": preferences.window_geometry,
                "selected_competitions": sorted(preferences.selected_competitions),
                "favorites_only": preferences.favorites_only,
                "notifications_enabled": preferences.notifications_enabled,
                "notification_minutes": preferences.notification_minutes,
                "minimize_to_tray": preferences.minimize_to_tray,
                "custom_team_names": {
                    str(team_id): name for team_id, name in preferences.custom_team_names.items()
                },
            }
            normalized = _parse_preferences(payload)
            payload["custom_team_names"] = {
                str(team_id): name for team_id, name in normalized.custom_team_names.items()
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise SettingsError("저장할 설정이 올바르지 않습니다.") from None

        temporary_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".settings-", suffix=".tmp", dir=self.directory
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, UnicodeError):
            raise SettingsError(
                "설정을 저장하지 못했습니다. 저장 폴더와 디스크 공간을 확인해 주세요."
            ) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


class CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


def _native_backend() -> CredentialBackend | None:
    if sys.platform != "win32":
        return None
    try:
        # Deliberately do not use keyring.get_keyring(): it may select a
        # third-party plaintext backend configured on the user's machine.
        from keyring.backends.Windows import WinVaultKeyring

        backend = WinVaultKeyring()
        return backend if backend.priority > 0 else None
    except Exception:
        return None


def _validate_key(value: str) -> str:
    # A proxy-injected placeholder is valid too; do not assume a token length.
    if not isinstance(value, str) or not value or any(not 33 <= ord(char) <= 126 for char in value):
        raise CredentialError("API 키는 공백 없는 영문·숫자·기호로 입력해 주세요.")
    return value


class KeyManager:
    """Resolve session key, native stored key, then FOOTBALL_DATA_API_KEY.

    ``source`` is one of ``session``, ``credential_store``, ``environment``,
    or ``none``. Setting/deleting a key disables environment fallback until
    ``use_environment_key()`` is selected. A nonpersistent replacement also
    removes an older stored key, so it cannot unexpectedly return on restart.
    The injectable backend is intended for tests; production selects only the
    native Windows vault.
    """

    SERVICE = "KickoffDesk"
    USERNAME = "football-data.org"

    def __init__(
        self, settings_store: SettingsStore, backend: CredentialBackend | None = None
    ) -> None:
        self.settings_store = settings_store
        self._backend = backend if backend is not None else _native_backend()
        self._session_key: str | None = None
        self._source = "none"

    @property
    def can_persist(self) -> bool:
        return self._backend is not None

    @property
    def source(self) -> str:
        self.get_key()
        return self._source

    def _stored_key(self) -> str | None:
        if self._backend is None:
            return None
        try:
            key = self._backend.get_password(self.SERVICE, self.USERNAME)
        except Exception:
            raise CredentialError(
                "Windows 자격 증명 관리자에서 API 키를 읽지 못했습니다."
            ) from None
        return _validate_key(key) if key is not None else None

    def get_key(self) -> str | None:
        if self._session_key is not None:
            self._source = "session"
            return self._session_key
        stored_key = self._stored_key()
        if stored_key is not None:
            self._source = "credential_store"
            return stored_key
        if not self.settings_store.load().ignore_environment_api_key:
            environment_key = os.environ.get("FOOTBALL_DATA_API_KEY")
            if environment_key:
                self._source = "environment"
                return _validate_key(environment_key)
        self._source = "none"
        return None

    def _set_environment_ignored(self, ignored: bool) -> None:
        preferences = self.settings_store.load()
        preferences.ignore_environment_api_key = ignored
        self.settings_store.save(preferences)

    def _delete_stored_key(self) -> None:
        if self._backend is None:
            return
        try:
            # A missing key is already deleted. Read directly so even a
            # malformed credential can be removed through the settings UI.
            if self._backend.get_password(self.SERVICE, self.USERNAME) is not None:
                self._backend.delete_password(self.SERVICE, self.USERNAME)
        except Exception:
            raise CredentialError(
                "Windows 자격 증명 관리자에서 API 키를 삭제하지 못했습니다."
            ) from None

    def set_key(self, value: str, persist: bool = False) -> None:
        key = _validate_key(value)
        if persist and self._backend is None:
            raise CredentialError(
                "이 환경에서는 API 키를 안전하게 저장할 수 없습니다. 이번 실행에만 사용해 주세요."
            )
        # Persist the fallback choice before altering credentials. If the
        # settings disk is unavailable, leave the existing key untouched.
        self._set_environment_ignored(True)
        if persist:
            try:
                self._backend.set_password(self.SERVICE, self.USERNAME, key)
            except Exception:
                raise CredentialError(
                    "Windows 자격 증명 관리자에 API 키를 저장하지 못했습니다."
                ) from None
            self._session_key = None
            self._source = "credential_store"
        else:
            self._delete_stored_key()
            self._session_key = key
            self._source = "session"

    def delete_key(self) -> None:
        self._set_environment_ignored(True)
        self._delete_stored_key()
        self._session_key = None
        self._source = "none"

    def use_environment_key(self) -> None:
        self._delete_stored_key()
        self._set_environment_ignored(False)
        self._session_key = None
        self._source = "none"
