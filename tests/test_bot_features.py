import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from throwtask.errors import TodoError
from throwtask.storage import encode


def create(client, **fields):
    response = client.post("/v1/tasks", json={"title": "Bot task", **fields})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def patch(client, task, **fields):
    return client.patch("/v1/tasks/" + task["id"], headers={"If-Match": f'"{task["revision"]}"'}, json=fields)


def test_external_id_is_unique_across_bots_and_deleted_tasks(system):
    client, *_ = system
    original = create(client, externalID=" github:owner/repo#42 ", source="github-bot")
    duplicate = client.post(
        "/v1/tasks",
        json={"title": "Different title", "externalID": original["externalID"], "source": "other-bot"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == {
        "code": "EXTERNAL_ID_ALREADY_EXISTS",
        "existingRecordId": original["id"],
    }
    assert client.get("/v1/tasks/" + original["id"]).json()["data"] == original
    found = client.get("/v1/tasks", params={"external_id": original["externalID"], "source": "github-bot"})
    assert found.json()["data"]["items"] == [original]
    assert client.get("/v1/tasks", params={"source": "other-bot"}).json()["data"]["items"] == []
    deleted = client.delete(
        "/v1/tasks/" + original["id"], headers={"If-Match": '"1"'}, params={"confirm": True}
    ).json()["data"]
    assert (
        client.post("/v1/tasks", json={"title": "Again", "externalID": original["externalID"]}).status_code
        == 409
    )
    assert (
        client.get("/v1/tasks", params={"external_id": original["externalID"]}).json()["data"]["items"] == []
    )
    assert client.get(
        "/v1/tasks", params={"external_id": original["externalID"], "include_deleted": True}
    ).json()["data"]["items"] == [deleted]
    assert (
        client.post("/v1/tasks/" + original["id"] + "/restore", headers={"If-Match": '"2"'}).status_code
        == 200
    )


def test_external_id_updates_remain_conditional_and_nullable(system):
    client, *_ = system
    first = create(client, externalID="mail:account:message-1", source="mail-bot")
    second = create(client)
    assert patch(client, second, externalID=first["externalID"]).status_code == 409
    cleared = patch(client, first, externalID=None, source=None).json()["data"]
    assert cleared["externalID"] is None and cleared["source"] is None
    assert patch(client, first, externalID="mail:account:message-2").status_code == 412
    assigned = patch(client, second, externalID="mail:account:message-1", source="other-bot").json()["data"]
    assert assigned["externalID"] == first["externalID"] and assigned["revision"] == 2


def test_competing_imports_only_create_one_task(system):
    _, db, _, digest, _ = system

    def import_task(index):
        try:
            db.create(digest, "task", {"title": f"Import {index}", "externalID": "github:owner/repo#99"})
            return "ok"
        except TodoError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(import_task, range(2))) == ["EXTERNAL_ID_ALREADY_EXISTS", "ok"]
    assert len(db.snapshot(digest)["records"]) == 1


def test_waiting_and_blocked_are_independent_of_completion_and_someday(system):
    client, *_ = system
    active = create(client)
    waiting = create(client, state="waiting", scheduledDay="2026-11-16")
    blocked = create(client, state="blocked", isSomeday=True)
    assert active["state"] == "active"
    for state, task in (("active", active), ("waiting", waiting), ("blocked", blocked)):
        assert client.get("/v1/tasks", params={"state": state, "status": "open"}).json()["data"]["items"] == [
            task
        ]
    completed = patch(client, waiting, isCompleted=True).json()["data"]
    assert completed["state"] == "waiting"
    assert (
        client.get("/v1/tasks", params={"state": "waiting", "status": "open"}).json()["data"]["items"] == []
    )
    resumed = patch(client, completed, isCompleted=False, state="active").json()["data"]
    assert resumed["state"] == "active" and resumed["scheduledDay"] == waiting["scheduledDay"]


def test_checklist_repeat_and_metadata_survive_completion_without_duplicate_external_id(system):
    client, *_ = system
    item_id = str(uuid4())
    task = create(
        client,
        externalID="routine:weekly-review",
        source="planner-bot",
        scheduledDay="2090-01-31",
        sharedRepeat={"unit": "month"},
        checklist=[{"id": item_id, "title": "Review", "isCompleted": True}],
    )
    completed = patch(client, task, isCompleted=True).json()["data"]
    tasks = client.get("/v1/tasks").json()["data"]["items"]
    child = next(t for t in tasks if t["id"] != task["id"])
    assert child["externalID"] is None and child["source"] == "planner-bot"
    assert child["scheduledDay"] == "2090-02-28" and child["sharedRepeat"]["anchorDay"] == "2090-01-31"
    assert child["checklist"] == [{"id": item_id, "title": "Review", "isCompleted": False}]
    assert patch(client, task, checklist=[]).status_code == 412
    assert patch(client, completed, isCompleted=False).status_code == 200
    assert client.get("/v1/tasks/" + child["id"]).status_code == 404


@pytest.mark.parametrize(
    "fields",
    [
        {"externalID": " "},
        {"source": ""},
        {"state": "unknown"},
        {"state": None},
        {"sharedRepeat": {"unit": "day", "interval": 0}},
        {"checklist": [{"id": "invalid", "title": "Invalid id"}]},
    ],
)
def test_invalid_bot_fields_are_rejected(system, fields):
    client, *_ = system
    assert client.post("/v1/tasks", json={"title": "Invalid", **fields}).status_code == 422


def test_since_is_inclusive_normalized_and_pages_equal_timestamps_and_tombstones(system, monkeypatch):
    client, *_ = system
    monkeypatch.setattr("throwtask.storage.now", lambda: "2026-11-15T00:00:00.000000Z")
    old = create(client)
    monkeypatch.setattr("throwtask.storage.now", lambda: "2026-11-16T00:00:00.000000Z")
    waiting = create(client, state="waiting")
    client.delete("/v1/tasks/" + old["id"], headers={"If-Match": '"1"'}, params={"confirm": True})
    since = "2026-11-16T09:00:00+09:00"
    first = client.get("/v1/changes", params={"since": since, "limit": 1}).json()["data"]
    assert first["hasMore"] and first["items"][0]["record"]["id"] == waiting["id"]
    second = client.get("/v1/changes", params={"since": since, "after": first["cursor"], "limit": 1}).json()[
        "data"
    ]
    assert not second["hasMore"] and second["cursor"] == second["latestCursor"]
    assert second["items"][0]["record"]["deletedAt"] is not None
    assert client.get("/v1/changes", params={"after": second["cursor"]}).json()["data"]["items"] == []
    future = client.get("/v1/changes", params={"since": "2099-01-01T00:00:00Z"}).json()["data"]
    assert future["items"] == [] and future["cursor"] == future["latestCursor"]
    assert client.get("/v1/changes", params={"since": since, "after": 10000}).status_code == 409


@pytest.mark.parametrize("since", ["2026-11-16", "2026-11-16T00:00:00", "not-a-date", "2026-02-30T00:00:00Z"])
def test_since_requires_a_valid_timestamp_with_offset(system, since):
    client, *_ = system
    response = client.get("/v1/changes", params={"since": since})
    assert response.status_code == 422 and since not in response.text


def test_existing_bodies_and_creation_fingerprints_remain_compatible(system):
    client, db, *_ = system
    original = {"id": str(uuid4()), "title": "Before bot fields"}
    task = create(client, **original)
    # Recreate the body/fingerprint written by the previous version, with no new fields.
    with db.transaction(write=True) as connection:
        row = connection.execute("SELECT body FROM records WHERE id=?", (task["id"],)).fetchone()
        legacy = json.loads(row["body"])
        for key in ("state", "externalID", "source", "dependencyIDs", "blockReason"):
            legacy.pop(key)
        digest = hashlib.sha256(encode(legacy).encode()).hexdigest()
        connection.execute(
            "UPDATE records SET body=?,fingerprint=? WHERE id=?", (encode(legacy), digest, task["id"])
        )
        legacy_change = {
            key: value
            for key, value in task.items()
            if key
            not in {
                "state",
                "externalID",
                "source",
                "dependencyIDs",
                "blockReason",
                "effectiveState",
                "blockingDependencyIDs",
            }
        }
        connection.execute(
            "UPDATE changes SET record=? WHERE record_id=?", (encode(legacy_change), task["id"])
        )
    db.initialize()
    changes = client.get("/v1/changes").json()["data"]["items"]
    assert changes[0]["record"]["state"] == "active" and changes[0]["record"]["externalID"] is None
    assert patch(client, task, title="Keep later edits").status_code == 200
    retry = client.post("/v1/tasks", json=original)
    assert retry.status_code == 200 and retry.json()["data"]["title"] == "Keep later edits"
    assert retry.json()["data"]["state"] == "active"
    assert client.get("/v1/tasks", params={"state": "active"}).json()["data"]["items"][0]["id"] == task["id"]
