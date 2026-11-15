from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from toidone.config import Settings, default_config, load_settings, read_credential
from toidone.errors import TodoError
from toidone.server import default_data_dir
from toidone.storage import Database, token_digest


def test_existing_default_paths_remain_usable_and_new_paths_take_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    current_config = tmp_path / ".config/toidone/config.json"
    current_data = tmp_path / ".local/share/toidone-server"
    assert default_config() == current_config
    assert default_data_dir() == current_data

    legacy_config = tmp_path / ".config/mytodo/config.json"
    Settings("http://127.0.0.1:8766").save(legacy_config)
    legacy_data = tmp_path / ".local/share/mytodo-server"
    legacy_data.mkdir(parents=True)
    assert default_config() == legacy_config
    assert load_settings(default_config()).url == "http://127.0.0.1:8766"
    assert default_data_dir() == legacy_data

    Settings("http://127.0.0.1:8767").save(current_config)
    current_data.mkdir()
    assert default_config() == current_config
    assert load_settings(default_config()).url == "http://127.0.0.1:8767"
    assert default_data_dir() == current_data


def test_legacy_database_keeps_tasks_and_revocation_state(tmp_path):
    source = Database(tmp_path / "source")
    source.initialize()
    credential = tmp_path / "credential"
    issued = source.issue_token("existing device", credential)
    digest = token_digest(read_credential(credential))
    record = source.create(digest, "task", {"id": str(uuid4()), "title": "Keep this task"})
    source.revoke_token(issued["id"])
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_file = legacy_dir / "mytodo.sqlite3"
    source.backup(legacy_file)

    reopened = Database(legacy_dir)
    reopened.initialize()
    assert reopened.path == legacy_file
    assert not (legacy_dir / "toidone.sqlite3").exists()
    assert reopened.list_tokens()[0]["revokedAt"] is not None
    with pytest.raises(TodoError, match="AUTHENTICATION_REQUIRED"):
        reopened.auth_status(digest)
    active_file = tmp_path / "active-credential"
    reopened.issue_token("new device", active_file)
    active_digest = token_digest(read_credential(active_file))
    assert reopened.get(active_digest, "task", record["id"])["title"] == "Keep this task"


def test_legacy_keyring_is_used_only_when_current_key_is_absent(monkeypatch):
    settings = Settings("https://todo.example.com")
    backend = Mock()
    old_token = "mtd_" + "a" * 43
    new_token = "mtd_" + "b" * 43
    values = {settings.legacy_keyring_service: old_token}
    backend.get_password.side_effect = lambda service, account: values.get(service)
    monkeypatch.setattr("toidone.config.keyring_backend", lambda: backend)
    assert settings.credential() == old_token
    values[settings.keyring_service] = new_token
    assert settings.credential() == new_token
    values[settings.keyring_service] = "invalid-current-key"
    backend.reset_mock()
    with pytest.raises(TodoError, match="CREDENTIAL_UNAVAILABLE"):
        settings.credential()
    backend.get_password.assert_called_once_with(settings.keyring_service, "api")
