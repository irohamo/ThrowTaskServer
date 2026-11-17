from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from throwtask.config import read_credential
from throwtask.identity import timestamp


def create(client, title, **fields):
    response = client.post("/v1/tasks", json={"title": title, **fields})
    assert response.status_code == 200, response.json()
    return response.json()["data"]


def patch(client, record, **fields):
    response = client.patch(
        "/v1/tasks/" + record["id"], headers={"If-Match": '"' + str(record["revision"]) + '"'}, json=fields
    )
    assert response.status_code == 200, response.json()
    return response.json()["data"]


def get(client, record):
    return client.get("/v1/tasks/" + record["id"]).json()["data"]


def test_dependencies_derive_blocking_without_overwriting_manual_state(system):
    client, *_ = system
    a, b = create(client, "First"), create(client, "Second")
    task = create(client, "Then", dependencyIDs=[a["id"], b["id"]])
    assert task["state"] == "active" and task["effectiveState"] == "blocked"
    assert task["blockingDependencyIDs"] == [a["id"], b["id"]]
    assert task["id"] in {t["id"] for t in client.get("/v1/tasks?state=blocked").json()["data"]["items"]}
    a = patch(client, a, isCompleted=True)
    assert get(client, task)["blockingDependencyIDs"] == [b["id"]]
    b = patch(client, b, isCompleted=True)
    ready = get(client, task)
    assert ready["effectiveState"] == "active" and ready["revision"] == task["revision"]
    assert ready["dependencyIDs"] == task["dependencyIDs"]
    a = patch(client, a, isCompleted=False)
    assert get(client, task)["effectiveState"] == "blocked"
    assert task["id"] not in {t["id"] for t in client.get("/v1/tasks?state=active").json()["data"]["items"]}


@pytest.mark.parametrize("state", ["waiting", "blocked"])
def test_manual_deferral_survives_prerequisite_completion(system, state):
    client, *_ = system
    first = create(client, "First")
    reason = "Approval unavailable" if state == "blocked" else None
    task = create(client, "Deferred", state=state, blockReason=reason, dependencyIDs=[first["id"]])
    patch(client, first, isCompleted=True)
    result = get(client, task)
    assert result["state"] == result["effectiveState"] == state
    assert result["blockReason"] == reason and result["blockingDependencyIDs"] == []


def test_self_cycle_transitive_cycle_and_duplicate_links_do_not_write(system):
    client, db, *_ = system
    identifier = str(uuid4())
    before = client.get("/v1/snapshot").json()["data"]["cursor"]
    response = client.post(
        "/v1/tasks", json={"id": identifier, "title": "Self", "dependencyIDs": [identifier]}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "DEPENDENCY_CYCLE"
    assert client.get("/v1/snapshot").json()["data"]["cursor"] == before
    a = create(client, "A")
    b = create(client, "B", dependencyIDs=[a["id"]])
    c = create(client, "C", dependencyIDs=[b["id"]])
    response = client.patch(
        "/v1/tasks/" + a["id"], headers={"If-Match": '"1"'}, json={"dependencyIDs": [c["id"]]}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "DEPENDENCY_CYCLE"
    assert get(client, a)["revision"] == 1 and get(client, a)["dependencyIDs"] == []
    response = client.post("/v1/tasks", json={"title": "Duplicate", "dependencyIDs": [a["id"], a["id"]]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_concurrent_reverse_edges_cannot_create_a_cycle(system):
    client, *_ = system
    a, b = create(client, "A"), create(client, "B")

    def connect(pair):
        source, target = pair
        return client.patch(
            "/v1/tasks/" + source["id"], headers={"If-Match": '"1"'}, json={"dependencyIDs": [target["id"]]}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(connect, [(a, b), (b, a)]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 409).json()["error"]["code"] == "DEPENDENCY_CYCLE"


def test_deleted_prerequisite_stays_visible_as_blocking_until_unlinked_or_restored(system):
    client, *_ = system
    first = create(client, "First", isCompleted=True)
    task = create(client, "Then", dependencyIDs=[first["id"]])
    preview = client.get("/v1/tasks/" + first["id"] + "/delete-preview", headers={"If-Match": '"1"'}).json()[
        "data"
    ]
    assert preview["blockedDependentTaskIDs"] == [task["id"]]
    removed = client.delete("/v1/tasks/" + first["id"] + "?confirm=true", headers={"If-Match": '"1"'}).json()[
        "data"
    ]
    assert get(client, task)["effectiveState"] == "blocked"
    task = patch(client, task, notes="Keep missing link while editing")
    assert task["dependencyIDs"] == [first["id"]]
    response = client.post("/v1/tasks", json={"title": "New link", "dependencyIDs": [first["id"]]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    response = client.post(
        "/v1/tasks/" + first["id"] + "/restore", headers={"If-Match": '"' + str(removed["revision"]) + '"'}
    )
    assert response.status_code == 200
    assert get(client, task)["effectiveState"] == "active"
    task = patch(client, task, dependencyIDs=[])
    assert task["dependencyIDs"] == [] and task["effectiveState"] == "active"


def test_other_user_and_missing_prerequisites_share_an_error(system, tmp_path):
    client, db, *_ = system
    first = create(client, "Private first")
    owner = str(uuid4())
    with db.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, 0)", (owner, "dependency-other-user", timestamp())
        )
    secret = tmp_path / "other-key"
    db.issue_token("Other user", secret, owner_id=owner)
    headers = {"Authorization": "Bearer " + read_credential(secret)}
    for identifier in [first["id"], str(uuid4())]:
        response = client.post(
            "/v1/tasks", headers=headers, json={"title": "Unrelated", "dependencyIDs": [identifier]}
        )
        assert response.status_code == 409 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    own = client.post("/v1/tasks", headers=headers, json={"id": first["id"], "title": "Own first"})
    assert own.status_code == 200
    response = client.post(
        "/v1/tasks", headers=headers, json={"title": "Own dependent", "dependencyIDs": [first["id"]]}
    )
    assert response.status_code == 200 and response.json()["data"]["effectiveState"] == "blocked"
    assert get(client, first)["title"] == "Private first"


def test_dependency_edits_require_revision_and_legacy_create_retries_stay_idempotent(system):
    client, *_ = system
    identifier = str(uuid4())
    task = create(client, "Legacy defaults", id=identifier)
    retried = client.post(
        "/v1/tasks",
        json={"id": identifier, "title": "Legacy defaults", "dependencyIDs": [], "blockReason": None},
    )
    assert retried.status_code == 200 and retried.json()["data"] == task
    first = create(client, "First")
    changed = patch(client, task, dependencyIDs=[first["id"]])
    stale = client.patch("/v1/tasks/" + task["id"], headers={"If-Match": '"1"'}, json={"dependencyIDs": []})
    assert stale.status_code == 412 and get(client, task)["dependencyIDs"] == changed["dependencyIDs"]
