import pytest
from fastapi.testclient import TestClient

from throwtask.config import read_credential
from throwtask.server import create_app
from throwtask.storage import Database, token_digest


@pytest.fixture
def system(tmp_path):
    db = Database(tmp_path / "data")
    db.initialize()
    secret = tmp_path / "credential"
    token = db.issue_token("test", secret)
    value = read_credential(secret)
    with TestClient(create_app(tmp_path / "data")) as client:
        client.headers["Authorization"] = "Bearer " + value
        yield client, db, token, token_digest(value), secret
