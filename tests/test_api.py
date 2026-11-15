from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from mytodo.config import Settings, read_credential
from mytodo.errors import TodoError
from mytodo.storage import Database, token_digest


def task(client, **fields):
    value = {"id": str(uuid4()), "title": "検証用タスク", **fields}
    result = client.post("/v1/tasks", json=value)
    assert result.status_code == 200, result.text
    return result.json()["data"], value


def test_idempotent_creation_does_not_overwrite(system):
    client, *_ = system
    created, original = task(client)
    record_id = created["id"]
    changed = client.patch(f"/v1/tasks/{record_id}", headers={"If-Match": '"1"'}, json={"title": "更新済み"})
    assert changed.json()["data"]["revision"] == 2
    repeated = client.post("/v1/tasks", json=original).json()["data"]
    assert repeated["title"] == "更新済み" and repeated["revision"] == 2
    assert client.post("/v1/tasks", json={**original, "title": "別の内容"}).status_code == 409


def test_conflicts_preview_delete_restore_and_change_stream(system):
    client, *_ = system
    created, _ = task(client, scheduledDay="2026-11-15", deadlineDay="2026-11-20")
    path = "/v1/tasks/" + created["id"]
    assert client.patch(path, json={"title": "x"}).status_code == 428
    assert client.patch(path, headers={"If-Match": "*"}, json={"title": "x"}).status_code == 400
    updated = client.patch(path, headers={"If-Match": '"1"'}, json={"isCompleted": True}).json()["data"]
    assert updated["revision"] == 2
    conflict = client.patch(path, headers={"If-Match": '"1"'}, json={"notes": "lost"})
    assert conflict.status_code == 412 and conflict.json()["error"]["currentRevision"] == 2
    preview = client.get(path + "/delete-preview", headers={"If-Match": '"2"'})
    assert preview.json()["data"]["wouldDelete"]["id"] == created["id"]
    assert client.delete(path, headers={"If-Match": '"2"'}).status_code == 400
    assert client.get(path).json()["data"]["revision"] == 2
    deleted = client.delete(path, headers={"If-Match": '"2"'}, params={"confirm": True}).json()["data"]
    assert deleted["deletedAt"] and deleted["revision"] == 3
    assert client.get(path).status_code == 404
    restored = client.post(path + "/restore", headers={"If-Match": '"3"'}).json()["data"]
    assert restored["deletedAt"] is None and restored["revision"] == 4
    first_page = client.get("/v1/changes", params={"limit": 2}).json()["data"]
    assert first_page["hasMore"] and first_page["cursor"] == 2
    second_page = client.get("/v1/changes", params={"after": 2}).json()["data"]
    assert [c["record"]["revision"] for c in second_page["items"]] == [3, 4]
    snapshot = client.get("/v1/snapshot").json()["data"]
    assert snapshot["cursor"] == 4 and snapshot["records"][0]["id"] == created["id"]


def test_invalid_fields_are_rejected_without_echoing_inputs(system):
    client, _, _, _, credential = system
    secret = credential.read_text().strip()
    for fields in (
        {"title": " "},
        {"scheduledDay": "2026-02-30"},
        {"isCompleted": "false"},
        {"deadlineDay": "2026-11-15", "deadlineTime": "09:00"},
        {"unexpected": secret},
        {"tags": ["a", "a"]},
    ):
        response = client.post("/v1/tasks", json={"title": "検証", **fields})
        assert response.status_code == 422
        assert secret not in response.text
    assert client.get("/v1/snapshot").json()["data"]["records"] == []


def test_project_delete_detaches_tasks_and_preserves_changes(system):
    client, *_ = system
    project = client.post("/v1/projects", json={"title": "検証用"}).json()["data"]
    created, _ = task(client, projectID=project["id"])
    path = "/v1/projects/" + project["id"]
    preview = client.get(path + "/delete-preview", headers={"If-Match": '"1"'}).json()["data"]
    assert preview["detachedTaskIds"] == [created["id"]]
    assert client.delete(path, params={"confirm": True}, headers={"If-Match": '"1"'}).status_code == 200
    detached = client.get("/v1/tasks/" + created["id"]).json()["data"]
    assert detached["projectID"] is None and detached["revision"] == 2
    assert (
        client.patch(
            "/v1/tasks/" + created["id"], json={"projectID": project["id"]}, headers={"If-Match": '"2"'}
        ).status_code
        == 409
    )


def test_atomic_competing_updates(system):
    client, db, _, digest, _ = system
    created, _ = task(client)

    def change(title):
        try:
            db.update(digest, "task", created["id"], 1, {"title": title})
            return "ok"
        except TodoError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, ["first", "second"])) == ["CONFLICT", "ok"]


def test_revocation_is_immediate_and_backup_restores(system, tmp_path):
    client, db, token, digest, _ = system
    created, _ = task(client)
    db.revoke_token(token["id"])
    assert client.get("/v1/tasks").status_code == 401
    assert (
        client.patch(
            "/v1/tasks/" + created["id"], json={"title": "blocked"}, headers={"If-Match": '"1"'}
        ).status_code
        == 401
    )
    backup = tmp_path / "backup.sqlite3"
    db.backup(backup)
    assert backup.stat().st_mode & 0o077 == 0
    restored = Database(tmp_path / "restored")
    restored.restore_backup(backup)
    with pytest.raises(TodoError, match="AUTHENTICATION_REQUIRED"):
        restored.snapshot(digest)
    next_credential = tmp_path / "restored-credential"
    restored.issue_token("restored", next_credential)
    snapshot = restored.snapshot(token_digest(read_credential(next_credential)))
    assert snapshot["records"][0]["id"] == created["id"] and snapshot["cursor"] == 1
    with pytest.raises(TodoError, match="RESTORE_TARGET_EXISTS"):
        restored.restore_backup(backup)


def test_credentials_and_url_restrictions(system, tmp_path):
    _, _, _, _, secret = system
    with pytest.raises(TodoError, match="HTTPS_REQUIRED"):
        Settings("http://example.com")
    with pytest.raises(TodoError, match="INVALID_SERVER_URL"):
        Settings("https://user:password@example.com")
    assert Settings("http://127.0.0.1:8766/").url == "http://127.0.0.1:8766"
    secret.chmod(0o644)
    with pytest.raises(TodoError, match="UNSAFE_CREDENTIAL_FILE"):
        read_credential(secret)
    secret.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(TodoError, match="CREDENTIAL_UNAVAILABLE"):
        read_credential(link)


def test_pagination_and_nullable_fields(system):
    client, *_ = system
    for _ in range(3):
        task(client)
    first = client.get("/v1/tasks", params={"limit": 2}).json()["data"]
    second = client.get("/v1/tasks", params={"limit": 2, "after": first["nextCursor"]}).json()["data"]
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    record = first["items"][0]
    response = client.patch("/v1/tasks/" + record["id"], headers={"If-Match": '"1"'}, json={"title": None})
    assert response.status_code == 422
