import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from throwtask.errors import TodoError
from throwtask.server import create_app
from throwtask.storage import Database, token_digest


@pytest.fixture
def identity_system(tmp_path):
    database = Database(tmp_path / "data")
    database.initialize()
    # An old configuration must not re-enable retired authentication methods.
    (database.path.parent / "auth.json").write_text('{"rpID":"old.example.test"}')
    with TestClient(create_app(database.path.parent)) as client:
        yield client, database, tmp_path


def issue_user_key(system, username):
    _, database, root = system
    user = database.create_user(username)
    file = root / str(uuid4())
    database.issue_token("test device", file, owner_id=user["id"])
    return {"user": user, "key": file.read_text().strip()}


def authorize(client, response):
    client.headers["Authorization"] = "Bearer " + response["key"]


def test_only_api_keys_are_advertised_and_retired_endpoints_are_absent(identity_system):
    client, _, _ = identity_system
    assert client.get("/v1/auth/config").json()["data"] == {
        "authenticationMethods": ["apiKey"],
        "passkeysEnabled": False,
        "rpID": None,
    }
    for path in [
        "register/options",
        "register/verify",
        "login/options",
        "login/verify",
        "session/refresh",
        "session/logout",
        "recovery/options",
        "recovery-codes",
    ]:
        response = client.post(
            "/v1/auth/" + path, json={"invitation": "do-not-echo", "refreshToken": "do-not-echo"}
        )
        assert response.status_code == 404
        assert "do-not-echo" not in response.text
    assert client.get("/.well-known/apple-app-site-association").status_code == 404


def test_legacy_session_rows_cannot_authenticate_or_refresh(identity_system):
    client, database, _ = identity_system
    user = issue_user_key(identity_system, "alice")
    access, refresh = "tta_" + "a" * 43, "ttr_" + "b" * 43
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with database.transaction(write=True) as db:
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,NULL)",
            (
                str(uuid4()),
                user["user"]["id"],
                token_digest(access),
                token_digest(refresh),
                future,
                future,
                future,
            ),
        )
    client.headers["Authorization"] = "Bearer " + access
    assert client.get("/v1/auth/status").status_code == 401
    assert client.get("/v1/snapshot").status_code == 401
    with pytest.raises(TodoError) as failure:
        database.auth_status(token_digest(access))
    assert failure.value.code == "AUTHENTICATION_REQUIRED"
    assert client.post("/v1/auth/session/refresh", json={"refreshToken": refresh}).status_code == 404
    authorize(client, user)
    status = client.get("/v1/auth/status").json()["data"]
    assert status["method"] == "apiKey" and status["user"]["id"] == user["user"]["id"]


def test_admin_user_and_device_key_management_remain_private(tmp_path, monkeypatch, capsys):
    from throwtask.admin import main

    root = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["throwtask-admin", "--data-dir", str(root), "user", "create", "Alice"])
    assert main() == 0
    user = json.loads(capsys.readouterr().out)["data"]
    assert user["username"] == "alice"
    key_file = tmp_path / "device.credential"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "throwtask-admin",
            "--data-dir",
            str(root),
            "token",
            "create",
            "iPhone",
            "--user",
            user["id"],
            "--output",
            str(key_file),
        ],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "mtd_" not in output
    assert key_file.stat().st_mode & 0o777 == 0o600
    database = Database(root)
    assert database.auth_status(token_digest(key_file.read_text().strip()))["user"]["id"] == user["id"]
    assert any(row["id"] == user["id"] for row in database.list_users())
    with pytest.raises(TodoError) as failure:
        database.create_user("ALICE")
    assert failure.value.code == "USERNAME_UNAVAILABLE"
    with pytest.raises(TodoError) as failure:
        database.create_user("invalid user")
    assert failure.value.code == "INVALID_USERNAME"


def test_owners_isolate_uuid_external_id_projects_changes_and_deletion(identity_system):
    client, database, root = identity_system
    alice = issue_user_key(identity_system, "alice")
    bob = issue_user_key(identity_system, "bob")
    task_id, project_id = str(uuid4()), str(uuid4())
    for user in (alice, bob):
        authorize(client, user)
        assert (
            client.post(
                "/v1/projects", json={"id": project_id, "title": user["user"]["username"]}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/tasks",
                json={
                    "id": task_id,
                    "title": user["user"]["username"],
                    "externalID": "github:same#1",
                    "projectID": project_id,
                },
            ).status_code
            == 200
        )
        snapshot = client.get("/v1/snapshot").json()["data"]
        assert len(snapshot["records"]) == 2 and snapshot["cursor"] == 2
        assert snapshot["userID"] == user["user"]["id"]
        changes = client.get("/v1/changes").json()["data"]
        assert [change["cursor"] for change in changes["items"]] == [1, 2]
        assert {change["record"]["title"] for change in changes["items"]} == {user["user"]["username"]}
    authorize(client, alice)
    alice_only = client.post("/v1/projects", json={"title": "Alice only"}).json()["data"]
    client.delete("/v1/projects/" + project_id, headers={"If-Match": '"1"'}, params={"confirm": True})
    authorize(client, bob)
    assert client.get("/v1/projects/" + project_id).json()["data"]["title"] == "bob"
    assert client.get("/v1/tasks/" + task_id).json()["data"]["projectID"] == project_id
    assert client.get("/v1/projects/" + alice_only["id"]).status_code == 404
    assert (
        client.patch(
            "/v1/tasks/" + task_id, headers={"If-Match": '"1"'}, json={"projectID": alice_only["id"]}
        ).status_code
        == 409
    )
    key_file = root / "read-only"
    database.issue_token("read-only bot", key_file, owner_id=bob["user"]["id"], read_only=True)
    client.headers["Authorization"] = "Bearer " + key_file.read_text().strip()
    assert client.get("/v1/tasks").status_code == 200
    assert (
        client.patch(
            "/v1/tasks/" + task_id, headers={"If-Match": '"1"'}, json={"notes": "denied"}
        ).status_code
        == 403
    )


def test_schema_one_migration_preserves_owner_data_tokens_fingerprints_cursors_and_backup(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    path = root / "toidone.sqlite3"
    owner_key = "mtd_" + "k" * 43
    record_id, child_id = str(uuid4()), str(uuid4())
    date = "2026-11-16T00:00:00.000000Z"
    body = json.dumps(
        {
            "id": record_id,
            "title": "Existing legacy task",
            "externalID": "github:issue:42",
            "revision": 3,
            "createdAt": date,
            "updatedAt": date,
            "deletedAt": None,
        }
    )
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE tokens(id TEXT PRIMARY KEY,label TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,revoked_at TEXT);
            CREATE TABLE records(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
                body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT,
                fingerprint TEXT NOT NULL,PRIMARY KEY(kind,id));
            CREATE TABLE changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,
                record_id TEXT NOT NULL,record TEXT NOT NULL);
            CREATE TABLE recurrence_links(parent_id TEXT PRIMARY KEY,child_id TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX task_external_id ON records(json_extract(body,'$.externalID'))
                WHERE kind='task' AND json_extract(body,'$.externalID') IS NOT NULL;
            PRAGMA user_version=1;
        """)
        db.execute(
            "INSERT INTO tokens VALUES (?,?,?, ?,NULL)",
            (str(uuid4()), "existing bot", token_digest(owner_key), date),
        )
        db.execute(
            "INSERT INTO records VALUES ('task',?,3,?,?,?,NULL,?)",
            (record_id, body, date, date, "original-fingerprint"),
        )
        db.execute("INSERT INTO changes VALUES (7,'task',?,?)", (record_id, body))
        db.execute("INSERT INTO recurrence_links VALUES (?,?,0)", (record_id, child_id))
    database = Database(root)
    database.initialize()
    database.initialize()
    status = database.auth_status(token_digest(owner_key))
    assert status["user"]["isLegacy"] is True and status["user"]["username"] is None
    snapshot = database.snapshot(token_digest(owner_key))
    assert snapshot["cursor"] == 7 and snapshot["records"][0]["id"] == record_id
    assert snapshot["records"][0]["revision"] == 3
    with database.transaction(token_digest(owner_key)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT fingerprint FROM records").fetchone()[0] == "original-fingerprint"
        assert (
            db.execute("SELECT child_id FROM recurrence_links WHERE owner_id=?", (db.owner_id,)).fetchone()[0]
            == child_id
        )
    backup = tmp_path / "backup.sqlite3"
    database.backup(backup)
    restored = Database(tmp_path / "restored")
    restored.restore_backup(backup)
    restored.initialize()
    assert restored.snapshot(token_digest(owner_key)) == snapshot
