from __future__ import annotations

import json
import sys
import traceback
from dataclasses import replace
from types import ModuleType

import pytest

from kickoff_desk import settings
from kickoff_desk.settings import (
    CredentialError,
    KeyManager,
    Preferences,
    SettingsError,
    SettingsStore,
)

native_backend_factory = settings._native_backend


class FakeBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        del self.values[(service, username)]


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch):
    # No test ever reads the developer's actual configured key or OS vault.
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.setattr(settings, "_native_backend", lambda: None)


def test_preferences_roundtrip_and_defaults(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.load() == Preferences()
    preferences = Preferences({64, 86}, False, True, {64: "Liverpool FC", 86: "레알 마드리드"})
    store.save(preferences)
    assert store.load() == preferences
    assert sorted(path.name for path in tmp_path.iterdir()) == ["settings.json"]


@pytest.mark.parametrize(
    "content",
    ["{", "[]", '{"use_mock": "false"}', '{"favorite_team_ids": [true]}', '{"version": 2}'],
)
def test_corrupt_preferences_have_safe_errors(tmp_path, content):
    store = SettingsStore(tmp_path)
    store.path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match="설정 파일"):
        store.load()


def test_failed_atomic_write_preserves_previous_settings(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path)
    original = Preferences({64})
    store.save(original)

    def reject_replace(*args):
        raise OSError("private system detail")

    monkeypatch.setattr(settings.os, "replace", reject_replace)
    with pytest.raises(SettingsError) as caught:
        store.save(Preferences({86}))
    assert "private system detail" not in "".join(traceback.format_exception(caught.value))
    assert store.load() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["settings.json"]


def test_invalid_unicode_team_name_preserves_previous_settings(tmp_path):
    store = SettingsStore(tmp_path)
    original = Preferences({64}, favorite_team_names={64: "Liverpool FC"})
    store.save(original)
    malformed = Preferences({64}, favorite_team_names={64: "bad\ud800team"})
    with pytest.raises(SettingsError, match="저장하지 못했습니다") as caught:
        store.save(malformed)
    assert "bad" not in "".join(traceback.format_exception(caught.value))
    assert store.load() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["settings.json"]


def test_environment_key_is_supported_without_writing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-environment-placeholder")
    manager = KeyManager(SettingsStore(tmp_path))
    assert manager.get_key() == "test-environment-placeholder"
    assert manager.source == "environment"
    assert not list(tmp_path.iterdir())


def test_session_key_overrides_environment_without_secret_files(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-environment-placeholder")
    store = SettingsStore(tmp_path)
    manager = KeyManager(store)
    manager.set_key("test-session-secret")
    assert manager.get_key() == "test-session-secret"
    assert manager.source == "session"
    assert not manager.can_persist
    assert KeyManager(store).get_key() is None
    assert "test-session-secret" not in store.path.read_text()
    assert "test-environment-placeholder" not in store.path.read_text()
    assert "test-session-secret" not in caplog.text
    assert set(json.loads(store.path.read_text())) == {
        "version",
        "favorite_team_ids",
        "favorite_team_names",
        "use_mock",
        "ignore_environment_api_key",
        "theme",
        "font_size",
        "window_geometry",
        "selected_competitions",
        "favorites_only",
        "notifications_enabled",
        "notification_minutes",
        "minimize_to_tray",
        "custom_team_names",
    }


def test_persistent_key_survives_manager_reload_without_secret_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-environment-placeholder")
    store = SettingsStore(tmp_path)
    backend = FakeBackend()
    manager = KeyManager(store, backend)
    assert manager.can_persist
    manager.set_key("test-vault-secret", persist=True)
    reloaded = KeyManager(store, backend)
    assert reloaded.get_key() == "test-vault-secret"
    assert reloaded.source == "credential_store"
    assert "test-vault-secret" not in store.path.read_text()


def test_delete_disables_environment_fallback_across_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-environment-placeholder")
    store = SettingsStore(tmp_path)
    backend = FakeBackend()
    manager = KeyManager(store, backend)
    manager.set_key("test-vault-secret", persist=True)
    manager.delete_key()
    assert not backend.values
    assert manager.get_key() is None
    assert manager.source == "none"
    assert KeyManager(store, backend).get_key() is None
    assert settings.os.environ["FOOTBALL_DATA_API_KEY"] == "test-environment-placeholder"
    manager.use_environment_key()
    assert manager.get_key() == "test-environment-placeholder"
    assert KeyManager(store, backend).source == "environment"


def test_session_replacement_removes_old_persistent_key(tmp_path):
    store = SettingsStore(tmp_path)
    backend = FakeBackend()
    manager = KeyManager(store, backend)
    manager.set_key("old-test-secret", persist=True)
    manager.set_key("new-test-secret")
    assert manager.get_key() == "new-test-secret"
    assert not backend.values
    assert KeyManager(store, backend).get_key() is None


def test_use_environment_removes_session_and_vault_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-environment-placeholder")
    backend = FakeBackend()
    manager = KeyManager(SettingsStore(tmp_path), backend)
    manager.set_key("test-vault-secret", persist=True)
    manager.use_environment_key()
    assert manager.source == "environment"
    assert not backend.values
    manager.set_key("test-session-secret")
    manager.use_environment_key()
    assert manager.source == "environment"


@pytest.mark.parametrize(
    "invalid", ["", "with spaces", "secret\nInjected", "secret\t", "실제비밀문자열", "\x7f"]
)
def test_invalid_keys_are_rejected_without_echoing_input(tmp_path, invalid):
    manager = KeyManager(SettingsStore(tmp_path))
    with pytest.raises(CredentialError) as caught:
        manager.set_key(invalid)
    if invalid:
        assert invalid not in str(caught.value)
    assert manager.get_key() is None
    assert not list(tmp_path.iterdir())


def test_placeholder_key_has_no_artificial_length_constraint(tmp_path):
    manager = KeyManager(SettingsStore(tmp_path))
    manager.set_key("proxy:placeholder-for-football-api")
    assert manager.get_key() == "proxy:placeholder-for-football-api"


def test_persist_is_rejected_without_native_backend(tmp_path):
    manager = KeyManager(SettingsStore(tmp_path))
    with pytest.raises(CredentialError, match="이번 실행"):
        manager.set_key("test-secret", persist=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("method", ["read", "write", "delete"])
def test_backend_errors_never_reveal_key_or_original_exception(tmp_path, method, caplog):
    secret = "sensitive-test-secret"

    class FailingBackend(FakeBackend):
        def get_password(self, service, username):
            if method == "read":
                raise RuntimeError(secret)
            return secret

        def set_password(self, service, username, password):
            raise RuntimeError(secret)

        def delete_password(self, service, username):
            raise RuntimeError(secret)

    manager = KeyManager(SettingsStore(tmp_path), FailingBackend())
    with pytest.raises(CredentialError) as caught:
        if method == "read":
            manager.get_key()
        elif method == "write":
            manager.set_key(secret, persist=True)
        else:
            manager.delete_key()
    assert secret not in "".join(traceback.format_exception(caught.value))
    assert secret not in caplog.text
    for path in tmp_path.iterdir():
        assert secret not in path.read_text()


def test_key_updates_preserve_other_preferences(tmp_path):
    store = SettingsStore(tmp_path)
    store.save(Preferences({64}, False, False, {64: "Liverpool FC"}))
    manager = KeyManager(store)
    manager.set_key("test-key")
    assert store.load() == Preferences({64}, False, True, {64: "Liverpool FC"})


def test_platform_data_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert settings.data_dir() == tmp_path / "local" / "KickoffDesk"
    monkeypatch.setattr(settings.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert settings.data_dir() == tmp_path / "xdg" / "kickoff-desk"


def test_windows_uses_native_vault_without_backend_auto_selection(monkeypatch):
    keyring_module = ModuleType("keyring")
    backends_module = ModuleType("keyring.backends")
    windows_module = ModuleType("keyring.backends.Windows")

    class NativeBackend(FakeBackend):
        priority = 5

    def reject_auto_selection():
        pytest.fail("Automatic keyring selection can choose an insecure backend")

    keyring_module.get_keyring = reject_auto_selection
    windows_module.WinVaultKeyring = NativeBackend
    monkeypatch.setitem(sys.modules, "keyring", keyring_module)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends_module)
    monkeypatch.setitem(sys.modules, "keyring.backends.Windows", windows_module)
    monkeypatch.setattr(settings.sys, "platform", "win32")
    assert isinstance(native_backend_factory(), NativeBackend)
    monkeypatch.setattr(settings.sys, "platform", "linux")
    assert native_backend_factory() is None


def test_malformed_stored_key_can_still_be_deleted(tmp_path):
    backend = FakeBackend()
    backend.set_password(KeyManager.SERVICE, KeyManager.USERNAME, "bad\ncredential")
    manager = KeyManager(SettingsStore(tmp_path), backend)
    with pytest.raises(CredentialError):
        manager.get_key()
    manager.delete_key()
    assert manager.get_key() is None
    assert not backend.values


def test_old_version_one_preferences_keep_values_and_gain_safe_defaults(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "favorite_team_ids": [64],
                "favorite_team_names": {"64": "Liverpool FC"},
                "use_mock": False,
                "ignore_environment_api_key": True,
            }
        )
    )
    assert store.load() == Preferences({64}, False, True, {64: "Liverpool FC"})
    assert store.load().selected_competitions == {"PL", "PD", "CL"}
    assert not store.load().notifications_enabled
    assert not store.load().minimize_to_tray
    assert store.load().theme == "light"


def test_personalization_roundtrip_preserves_empty_filters_and_trims_names(tmp_path):
    store = SettingsStore(tmp_path)
    preferences = Preferences(
        {64},
        False,
        True,
        {64: "Liverpool FC"},
        theme="dark",
        font_size=20,
        window_geometry="YWJjZA==",
        selected_competitions=set(),
        favorites_only=True,
        notifications_enabled=True,
        notification_minutes=60,
        minimize_to_tray=True,
        custom_team_names={64: "  내가 응원하는 팀  "},
    )
    store.save(preferences)
    assert store.load() == replace(preferences, custom_team_names={64: "내가 응원하는 팀"})
    payload = json.loads(store.path.read_text())
    assert payload["version"] == 1
    assert payload["custom_team_names"] == {"64": "내가 응원하는 팀"}
    assert payload["selected_competitions"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("theme", "custom"),
        ("theme", None),
        ("font_size", 10),
        ("font_size", 21),
        ("font_size", True),
        ("font_size", 13.0),
        ("window_geometry", "not-base64!"),
        pytest.param("window_geometry", "A" * 4100, id="oversized-geometry"),
        ("window_geometry", 123),
        ("selected_competitions", ["BL1"]),
        ("selected_competitions", "PL"),
        ("selected_competitions", [True]),
        ("favorites_only", "false"),
        ("notifications_enabled", 1),
        ("notification_minutes", 20),
        ("notification_minutes", 30.0),
        ("minimize_to_tray", None),
        ("custom_team_names", {"0": "name"}),
        ("custom_team_names", {"64": " "}),
        ("custom_team_names", {"64": "x" * 81}),
        ("custom_team_names", {"64": "line\nbreak"}),
        ("custom_team_names", {"64": "bad\ud800name"}),
        ("custom_team_names", {str(i): "name" for i in range(1, 1002)}),
    ],
)
def test_invalid_personalization_settings_are_rejected_safely(tmp_path, field, value):
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"version": 1, field: value}))
    with pytest.raises(SettingsError, match="설정 파일"):
        store.load()


def test_invalid_personalization_save_preserves_existing_settings(tmp_path):
    store = SettingsStore(tmp_path)
    original = Preferences(theme="dark", custom_team_names={64: "응원팀"})
    store.save(original)
    with pytest.raises(SettingsError, match="올바르지"):
        store.save(replace(original, font_size=99))
    assert store.load() == original


def test_new_preferences_survive_every_key_policy_change(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "test-only-environment")
    store = SettingsStore(tmp_path)
    original = Preferences(
        {64},
        False,
        False,
        {64: "Liverpool FC"},
        theme="dark",
        font_size=18,
        window_geometry="YWJjZA==",
        selected_competitions={"PL"},
        favorites_only=True,
        notifications_enabled=True,
        notification_minutes=10,
        minimize_to_tray=True,
        custom_team_names={64: "우리 팀"},
    )
    store.save(original)
    manager = KeyManager(store, FakeBackend())
    manager.set_key("test-session-only")
    assert store.load() == replace(original, ignore_environment_api_key=True)
    manager.set_key("test-stored-only", persist=True)
    assert store.load() == replace(original, ignore_environment_api_key=True)
    manager.delete_key()
    assert store.load() == replace(original, ignore_environment_api_key=True)
    manager.use_environment_key()
    assert store.load() == original
    saved_text = store.path.read_text()
    assert "test-session-only" not in saved_text
    assert "test-stored-only" not in saved_text
    assert "test-only-environment" not in saved_text


def test_preference_containers_are_independent():
    first = Preferences()
    second = Preferences()
    first.selected_competitions.clear()
    first.custom_team_names[64] = "팀"
    assert second.selected_competitions == {"PL", "PD", "CL"}
    assert second.custom_team_names == {}
