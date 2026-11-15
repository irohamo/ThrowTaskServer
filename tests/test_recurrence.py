from datetime import date

from mytodo.recurrence import next_day


def test_calendar_anchors():
    monthly = {"unit": "month", "interval": 1, "anchorDay": "2027-01-31"}
    assert next_day("2027-01-31", monthly, date(2027, 1, 31)) == "2027-02-28"
    assert next_day("2027-02-28", monthly, date(2027, 2, 28)) == "2027-03-31"
    yearly = {"unit": "year", "interval": 1, "anchorDay": "2024-02-29"}
    assert next_day("2027-02-28", yearly, date(2027, 2, 28)) == "2028-02-29"


def test_recurrence_transactions_preserve_edited_child(system):
    client, *_ = system
    parent = client.post(
        "/v1/tasks",
        json={"title": "Recurring probe", "scheduledDay": "2090-01-31", "sharedRepeat": {"unit": "month"}},
    ).json()["data"]
    path = "/v1/tasks/" + parent["id"]
    client.patch(path, json={"isCompleted": True}, headers={"If-Match": '"1"'})
    records = client.get("/v1/tasks").json()["data"]["items"]
    child = next(record for record in records if record["recurrenceParentID"] == parent["id"])
    assert child["scheduledDay"] == "2090-02-28"
    client.patch("/v1/tasks/" + child["id"], json={"notes": "keep this edit"}, headers={"If-Match": '"1"'})
    client.patch(path, json={"isCompleted": False}, headers={"If-Match": '"2"'})
    client.patch(path, json={"isCompleted": True}, headers={"If-Match": '"3"'})
    children = [
        record
        for record in client.get("/v1/tasks").json()["data"]["items"]
        if record["recurrenceParentID"] == parent["id"]
    ]
    assert len(children) == 1 and children[0]["notes"] == "keep this edit"


def test_imported_child_is_not_duplicated(system):
    client, *_ = system
    parent = client.post(
        "/v1/tasks",
        json={
            "title": "Imported parent",
            "isCompleted": True,
            "scheduledDay": "2090-01-01",
            "sharedRepeat": {"unit": "day"},
        },
    ).json()["data"]
    client.post("/v1/tasks", json={"title": "Imported child", "recurrenceParentID": parent["id"]})
    path = "/v1/tasks/" + parent["id"]
    client.patch(path, json={"isCompleted": False}, headers={"If-Match": '"1"'})
    client.patch(path, json={"isCompleted": True}, headers={"If-Match": '"2"'})
    assert len(client.get("/v1/tasks").json()["data"]["items"]) == 2


def test_recurrence_overflow_rolls_back_completion(system):
    client, *_ = system
    parent = client.post(
        "/v1/tasks",
        json={"title": "Range probe", "scheduledDay": "9999-12-31", "sharedRepeat": {"unit": "day"}},
    ).json()["data"]
    path = "/v1/tasks/" + parent["id"]
    response = client.patch(path, json={"isCompleted": True}, headers={"If-Match": '"1"'})
    assert response.status_code == 409
    current = client.get(path).json()["data"]
    assert current["revision"] == 1 and not current["isCompleted"]
