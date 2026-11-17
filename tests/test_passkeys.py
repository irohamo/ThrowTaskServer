import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from passkey_authenticator import SoftwarePasskey

from throwtask.errors import TodoError
from throwtask.passkeys import PasskeyService, PasskeySettings, future
from throwtask.server import create_app
from throwtask.storage import Database, token_digest


@pytest.fixture
def identity_system(tmp_path):
    data = tmp_path / "data"
    database = Database(data)
    database.initialize()
    settings = PasskeySettings(
        "auth.example.test",
        "https://192.0.2.10:8443",
        ("https://auth.example.test",),
        ("ABCDE12345.com.example.ThrowTask",),
    )
    service = PasskeyService(database, settings)
    with TestClient(create_app(data, passkey_settings=settings)) as client:
        yield client, database, service, tmp_path


def enroll(system, username, *, legacy=False):
    client, _, service, root = system
    file = root / (str(uuid4()) + ".json")
    metadata = service.issue_invitation(file, claim_legacy=legacy)
    invitation = json.loads(file.read_text())
    started = client.post(
        "/v1/auth/register/options", json={"invitation": invitation["invitation"], "username": username}
    )
    assert started.status_code == 200
    key = SoftwarePasskey()
    options = started.json()["data"]
    response = client.post(
        "/v1/auth/register/verify",
        json={"challengeID": options["challengeID"], "credential": key.registration(options["options"])},
    )
    assert response.status_code == 200
    return response.json()["data"], key, invitation, metadata


def login(client, key, owner):
    options = client.post("/v1/auth/login/options").json()["data"]
    result = client.post(
        "/v1/auth/login/verify",
        json={"challengeID": options["challengeID"], "credential": key.assertion(options["options"], owner)},
    )
    assert result.status_code == 200
    return result.json()["data"]


def authorize(client, response):
    client.headers["Authorization"] = "Bearer " + response["session"]["accessToken"]


def test_https_ip_port_is_an_api_address_while_rp_id_is_a_domain(identity_system):
    client, _, service, _ = identity_system
    assert service.settings.server_url == "https://192.0.2.10:8443"
    assert client.get("/v1/auth/config").json()["data"] == {
        "passkeysEnabled": True,
        "rpID": "auth.example.test",
    }
    assert client.get("/.well-known/apple-app-site-association").json() == {
        "webcredentials": {"apps": ["ABCDE12345.com.example.ThrowTask"]}
    }
    for invalid in ("192.0.2.10", "auth.example.test:8443", "https://auth.example.test", "AUTH.EXAMPLE.TEST"):
        with pytest.raises(TodoError, match="INVALID_PASSKEY_CONFIGURATION"):
            PasskeySettings(invalid, "https://192.0.2.10:8443", ("https://auth.example.test",))


def test_disabled_passkeys_preserve_existing_api(system):
    client, *_ = system
    assert client.get("/v1/auth/config").json()["data"]["passkeysEnabled"] is False
    assert client.post("/v1/auth/login/options").status_code == 503
    assert client.get("/v1/tasks").status_code == 200


def test_invite_files_are_private_and_legacy_data_and_bot_key_keep_their_owner(identity_system):
    client, database, service, root = identity_system
    credential = root / "bot.credential"
    database.issue_token("existing bot", credential)
    bot_digest = token_digest(credential.read_text().strip())
    original = database.create(bot_digest, "task", {"title": "Keep legacy data", "externalID": "mail:legacy"})
    registered, _, invitation, metadata = enroll(identity_system, "Owner.Name", legacy=True)
    authorize(client, registered)
    observed = client.get("/v1/tasks/" + original["id"]).json()["data"]
    assert observed["revision"] == original["revision"] and observed["title"] == original["title"]
    assert registered["user"]["username"] == "owner.name" and registered["user"]["isLegacy"] is True
    assert database.auth_status(bot_digest)["user"]["id"] == registered["user"]["id"]
    assert Path(metadata["invitationFile"]).stat().st_mode & 0o077 == 0
    assert invitation["invitation"] not in json.dumps(metadata)
    assert (
        client.post(
            "/v1/auth/register/options",
            json={"invitation": invitation["invitation"], "username": "different"},
        ).status_code
        == 401
    )
    with pytest.raises(TodoError, match="LEGACY_OWNER_ALREADY_CLAIMED"):
        service.issue_invitation(root / "duplicate.json", claim_legacy=True)


def test_owners_isolate_uuid_external_id_projects_changes_and_deletion(identity_system):
    client, database, _, root = identity_system
    alice, _, _, _ = enroll(identity_system, "alice")
    bob, _, _, _ = enroll(identity_system, "bob")
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
    assert client.post("/v1/auth/recovery-codes").status_code == 403


@pytest.mark.parametrize("fault", ["origin", "rp", "verification"])
def test_registration_rejects_invalid_origin_rp_and_missing_user_verification(identity_system, fault):
    client, _, service, root = identity_system
    file = root / "invitation.json"
    service.issue_invitation(file)
    invitation = json.loads(file.read_text())["invitation"]
    options = client.post(
        "/v1/auth/register/options", json={"invitation": invitation, "username": "new-user"}
    ).json()["data"]
    key = SoftwarePasskey(
        origin="https://evil.example.test" if fault == "origin" else "https://auth.example.test",
        rp_id="evil.example.test" if fault == "rp" else "auth.example.test",
    )
    body = {
        "challengeID": options["challengeID"],
        "credential": key.registration(options["options"], verified=fault != "verification"),
    }
    assert client.post("/v1/auth/register/verify", json=body).status_code == 401
    assert client.post("/v1/auth/register/verify", json=body).json()["error"]["code"] == "CHALLENGE_INVALID"


def test_login_refresh_replay_expiry_and_logout(identity_system):
    client, database, _, _ = identity_system
    registered, key, _, _ = enroll(identity_system, "alice")
    logged_in = login(client, key, registered["user"]["id"])
    authorize(client, logged_in)
    assert client.get("/v1/auth/status").json()["data"]["method"] == "session"
    with database.transaction(write=True) as db:
        db.execute(
            "UPDATE sessions SET access_expires_at=? WHERE access_digest=?",
            (future(-1), token_digest(logged_in["session"]["accessToken"])),
        )
    assert client.get("/v1/tasks").status_code == 401
    original_refresh = logged_in["session"]["refreshToken"]
    rotated = client.post("/v1/auth/session/refresh", json={"refreshToken": original_refresh}).json()["data"]
    authorize(client, rotated)
    assert client.get("/v1/tasks").status_code == 200
    assert client.post("/v1/auth/session/refresh", json={"refreshToken": original_refresh}).status_code == 401
    assert client.get("/v1/tasks").status_code == 401
    authorize(client, registered)
    assert client.get("/v1/tasks").status_code == 200
    assert (
        client.post(
            "/v1/auth/session/logout", json={"refreshToken": registered["session"]["refreshToken"]}
        ).status_code
        == 200
    )
    assert client.get("/v1/tasks").status_code == 401


def test_assertion_user_handle_must_match_the_credential_owner(identity_system):
    client, _, _, _ = identity_system
    registered, key, _, _ = enroll(identity_system, "alice")
    options = client.post("/v1/auth/login/options").json()["data"]
    result = client.post(
        "/v1/auth/login/verify",
        json={
            "challengeID": options["challengeID"],
            "credential": key.assertion(options["options"], str(uuid4())),
        },
    )
    assert result.status_code == 401
    assert registered["user"]["id"]


def test_recovery_requires_a_new_verified_passkey_and_revokes_old_sessions(identity_system):
    client, _, _, _ = identity_system
    registered, old_key, _, _ = enroll(identity_system, "alice")
    code = registered["recoveryCodes"][0]
    body = {"username": "alice", "recoveryCode": code}
    options = client.post("/v1/auth/recovery/options", json=body).json()["data"]
    assert "session" not in options
    assert client.post("/v1/auth/recovery/options", json=body).status_code == 401
    replacement = SoftwarePasskey()
    recovered = client.post(
        "/v1/auth/register/verify",
        json={
            "challengeID": options["challengeID"],
            "credential": replacement.registration(options["options"]),
        },
    )
    assert recovered.status_code == 200
    assert recovered.json()["data"]["user"]["id"] == registered["user"]["id"]
    authorize(client, registered)
    assert client.get("/v1/tasks").status_code == 401
    options = client.post("/v1/auth/login/options").json()["data"]
    assert (
        client.post(
            "/v1/auth/login/verify",
            json={
                "challengeID": options["challengeID"],
                "credential": old_key.assertion(options["options"], registered["user"]["id"]),
            },
        ).status_code
        == 401
    )
    fresh = login(client, replacement, registered["user"]["id"])
    authorize(client, fresh)
    assert client.get("/v1/tasks").status_code == 200
    assert (
        client.post(
            "/v1/auth/recovery/options",
            json={"username": "alice", "recoveryCode": registered["recoveryCodes"][1]},
        ).status_code
        == 401
    )


def test_authentication_rate_limit_and_input_redaction(identity_system):
    client, _, _, _ = identity_system
    for _ in range(30):
        assert client.post("/v1/auth/login/options").status_code == 200
    assert client.post("/v1/auth/login/options").status_code == 429
    value = "tti_" + "z" * 43
    response = client.post(
        "/v1/auth/register/options", json={"invitation": value, "username": "valid", "ownerID": str(uuid4())}
    )
    assert response.status_code == 422 and value not in response.text


def test_recovery_and_code_rotation_invalidate_outstanding_recovery_challenges(identity_system):
    client, _, _, _ = identity_system
    registered, _, _, _ = enroll(identity_system, "alice")
    pending = []
    for code in registered["recoveryCodes"][:2]:
        pending.append(
            client.post("/v1/auth/recovery/options", json={"username": "alice", "recoveryCode": code}).json()[
                "data"
            ]
        )
    first = pending[0]
    key = SoftwarePasskey()
    assert (
        client.post(
            "/v1/auth/register/verify",
            json={"challengeID": first["challengeID"], "credential": key.registration(first["options"])},
        ).status_code
        == 200
    )
    stale = pending[1]
    assert (
        client.post(
            "/v1/auth/register/verify",
            json={
                "challengeID": stale["challengeID"],
                "credential": SoftwarePasskey().registration(stale["options"]),
            },
        ).status_code
        == 401
    )
    signed_in = login(client, key, registered["user"]["id"])
    authorize(client, signed_in)
    codes = client.post("/v1/auth/recovery-codes").json()["data"]["recoveryCodes"]
    pending = client.post(
        "/v1/auth/recovery/options", json={"username": "alice", "recoveryCode": codes[0]}
    ).json()["data"]
    assert client.post("/v1/auth/recovery-codes").status_code == 200
    assert (
        client.post(
            "/v1/auth/register/verify",
            json={
                "challengeID": pending["challengeID"],
                "credential": SoftwarePasskey().registration(pending["options"]),
            },
        ).status_code
        == 401
    )


def test_challenge_expiry_malformed_ids_and_recent_primary_authentication(identity_system):
    client, database, _, _ = identity_system
    registered, key, _, _ = enroll(identity_system, "alice")
    options = client.post("/v1/auth/login/options").json()["data"]
    with database.transaction(write=True) as db:
        db.execute("UPDATE challenges SET expires_at=? WHERE id=?", (future(-1), options["challengeID"]))
    assert (
        client.post(
            "/v1/auth/login/verify",
            json={
                "challengeID": options["challengeID"],
                "credential": key.assertion(options["options"], registered["user"]["id"]),
            },
        ).status_code
        == 401
    )
    options = client.post("/v1/auth/login/options").json()["data"]
    assert (
        client.post(
            "/v1/auth/login/verify",
            json={"challengeID": options["challengeID"], "credential": {"id": {"invalid": "type"}}},
        ).status_code
        == 401
    )
    authorize(client, registered)
    with database.transaction(write=True) as db:
        db.execute("UPDATE sessions SET authenticated_at=?", (future(-301),))
    assert client.post("/v1/auth/recovery-codes").status_code == 403
    assert client.get("/v1/tasks").status_code == 200


def test_admin_creates_private_invitation_and_qr_without_printing_the_secret(
    identity_system, monkeypatch, capsys
):
    from PIL import Image

    from throwtask.admin import main

    _, database, service, root = identity_system
    config = {
        "rpID": service.settings.rp_id,
        "serverURL": service.settings.server_url,
        "origins": list(service.settings.origins),
    }
    (database.path.parent / "auth.json").write_text(json.dumps(config))
    file, qr = root / "invite.json", root / "invite.png"
    monkeypatch.setattr(
        "sys.argv",
        [
            "throwtask-admin",
            "--data-dir",
            str(database.path.parent),
            "invite",
            "--output",
            str(file),
            "--qr-output",
            str(qr),
            "--claim-legacy",
        ],
    )
    assert main() == 0
    output = capsys.readouterr().out
    secret = json.loads(file.read_text())["invitation"]
    assert secret not in output and qr.stat().st_mode & 0o077 == 0
    with Image.open(qr) as image:
        assert image.format == "PNG" and image.width > 100


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
