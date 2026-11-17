"""Real HTTP server + CLI subprocess + official MCP SDK over stdio. Uses temporary data only."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from throwtask.config import Settings
from throwtask.storage import Database


@pytest.fixture
def live_system(tmp_path):
    database = Database(tmp_path / "data")
    database.initialize()
    credential = tmp_path / "credential"
    token = database.issue_token("e2e", credential)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    process = subprocess.Popen(
        [sys.executable, "-m", "throwtask.server", "--data-dir", str(tmp_path / "data"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    config = tmp_path / "config.json"
    Settings(url, credential).save(config)
    try:
        with httpx.Client(trust_env=False, timeout=1) as http:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError("Test API process exited before becoming ready")
                try:
                    if http.get(url + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Test API did not become ready")
        yield config, database, token
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stderr.close()


def cli(config, *arguments, expected_exit=0):
    result = subprocess.run(
        [sys.executable, "-m", "throwtask.cli", "--config", str(config), "--json", *arguments],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == expected_exit, result.stdout
    assert not result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_cli_mcp_http_workflow_and_revocation(live_system, mode):
    config, database, token = live_system
    record = cli(config, "tasks", "create", "E2E専用タスク", "--when", "2026-11-15")["data"]

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "throwtask.mcp_server", "--config", str(config)],
            env=dict(os.environ),
        )
        async with Client(parameters, mode=mode) as mcp:
            tools = (await mcp.list_tools()).tools
            assert {"create_task", "complete_task", "delete_task", "restore_task", "list_projects"} <= {
                tool.name for tool in tools
            }
            schemas = json.dumps([tool.input_schema for tool in tools])
            assert "credential" not in schemas.lower() and "api_key" not in schemas.lower()
            done = await mcp.call_tool("complete_task", {"task_id": record["id"], "expected_revision": 1})
            assert not done.is_error
            assert done.structured_content["data"]["isCompleted"] is True
            observed = cli(config, "tasks", "get", record["id"])["data"]
            assert observed["isCompleted"] and observed["revision"] == 2
            conflict = await mcp.call_tool(
                "update_task",
                {"task_id": record["id"], "expected_revision": 1, "changes": {"notes": "must not overwrite"}},
            )
            assert conflict.is_error and conflict.structured_content["error"]["code"] == "CONFLICT"
            preview = await mcp.call_tool("delete_task", {"task_id": record["id"], "expected_revision": 2})
            assert (
                not preview.is_error
                and preview.structured_content["data"]["wouldDelete"]["id"] == record["id"]
            )
            assert cli(config, "tasks", "get", record["id"])["data"]["revision"] == 2
            reopened = await mcp.call_tool("reopen_task", {"task_id": record["id"], "expected_revision": 2})
            assert not reopened.is_error
            removed = cli(config, "tasks", "delete", record["id"], "--revision", "3", "--confirm")["data"]
            assert removed["deletedAt"]
            restored = await mcp.call_tool("restore_task", {"task_id": record["id"], "expected_revision": 4})
            assert not restored.is_error and restored.structured_content["data"]["revision"] == 5
            database.revoke_token(token["id"])
            blocked = await mcp.call_tool("complete_task", {"task_id": record["id"], "expected_revision": 5})
            assert blocked.is_error
            assert blocked.structured_content["error"]["code"] == "AUTHENTICATION_REQUIRED"
            denied = cli(config, "tasks", "complete", record["id"], "--revision", "5", expected_exit=1)
            assert denied["error"]["code"] == "AUTHENTICATION_REQUIRED"

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_bot_settings_identity_states_and_since_over_cli_and_mcp(live_system, mode):
    config, *_ = live_system
    record = cli(
        config,
        "tasks",
        "create",
        "Weekly review",
        "--when",
        "2090-01-31",
        "--checklist-item",
        "Gather notes",
        "--checklist-item",
        "Review",
        "--repeat",
        "month",
        "--repeat-interval",
        "1",
        "--repeat-anchor",
        "2090-01-31",
        "--external-id",
        "routine:weekly-review",
        "--source",
        "planner-bot",
    )["data"]
    assert record["sharedRepeat"] == {"unit": "month", "interval": 1, "anchorDay": "2090-01-31"}
    assert len(record["checklist"]) == 2 and record["source"] == "planner-bot"

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "throwtask.mcp_server", "--config", str(config)],
            env=dict(os.environ),
        )
        async with Client(parameters, mode=mode) as mcp:
            tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            create_schema = json.dumps(tools["create_task"].input_schema)
            assert all(
                field in create_schema
                for field in ("checklist", "sharedRepeat", "externalID", "source", "state")
            )
            assert "since" in tools["list_changes"].input_schema["properties"]
            checklist = record["checklist"]
            checklist[0]["isCompleted"] = True
            changed = await mcp.call_tool(
                "update_task",
                {
                    "task_id": record["id"],
                    "expected_revision": 1,
                    "changes": {"state": "waiting", "checklist": checklist},
                },
            )
            assert not changed.is_error
            waiting = cli(config, "tasks", "list", "--state", "waiting", "--source", "planner-bot")["data"][
                "items"
            ]
            assert len(waiting) == 1 and waiting[0]["checklist"][0]["id"] == checklist[0]["id"]
            assert waiting[0]["checklist"][0]["isCompleted"] is True
            duplicate = await mcp.call_tool(
                "create_task",
                {"task": {"title": "Duplicate", "externalID": record["externalID"], "source": "other-bot"}},
            )
            assert duplicate.is_error
            assert duplicate.structured_content["error"]["code"] == "EXTERNAL_ID_ALREADY_EXISTS"
            assert duplicate.structured_content["error"]["existingRecordId"] == record["id"]
            lookup = await mcp.call_tool(
                "list_tasks", {"external_id": record["externalID"], "state": "waiting"}
            )
            assert lookup.structured_content["data"]["items"][0]["id"] == record["id"]
            first = await mcp.call_tool("list_changes", {"since": record["createdAt"], "limit": 1})
            assert not first.is_error and first.structured_content["data"]["hasMore"]
            cursor = first.structured_content["data"]["cursor"]
            last = cli(config, "changes", "--since", record["createdAt"], "--after", str(cursor), "--json")[
                "data"
            ]
            assert not last["hasMore"] and last["items"][0]["record"]["state"] == "waiting"
            invalid = await mcp.call_tool("list_changes", {"since": "2026-11-16T00:00:00"})
            assert invalid.is_error and invalid.structured_content["error"]["code"] == "VALIDATION_ERROR"
            done = await mcp.call_tool("complete_task", {"task_id": record["id"], "expected_revision": 2})
            assert not done.is_error
            tasks = cli(config, "tasks", "list")["data"]["items"]
            child = next(t for t in tasks if t["id"] != record["id"])
            assert child["scheduledDay"] == "2090-02-28" and child["externalID"] is None
            assert all(not item["isCompleted"] for item in child["checklist"])
            blocked = cli(config, "tasks", "edit", child["id"], "--revision", "1", "--state", "blocked")[
                "data"
            ]
            assert blocked["state"] == "blocked"
            cleared = cli(
                config,
                "tasks",
                "edit",
                child["id"],
                "--revision",
                "2",
                "--clear",
                "checklist",
                "--clear",
                "sharedRepeat",
            )["data"]
            assert cleared["checklist"] == [] and cleared["sharedRepeat"] is None

    asyncio.run(exercise())


def test_admin_never_prints_api_key_and_preserves_backup(tmp_path):
    root = tmp_path / "admin"
    credential = tmp_path / "admin-key"
    base = [sys.executable, "-m", "throwtask.admin", "--data-dir", str(root)]
    result = subprocess.run(
        [*base, "token", "create", "device", "--output", str(credential)],
        capture_output=True,
        text=True,
        check=True,
    )
    key = credential.read_text().strip()
    assert key not in result.stdout + result.stderr
    assert credential.stat().st_mode & 0o077 == 0
    backup = tmp_path / "backup.sqlite3"
    subprocess.run([*base, "backup", "--output", str(backup)], check=True, capture_output=True)
    restored_root = tmp_path / "restored"
    restored = subprocess.run(
        [
            sys.executable,
            "-m",
            "throwtask.admin",
            "--data-dir",
            str(restored_root),
            "restore",
            "--backup",
            str(backup),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(restored.stdout)["data"]["restored"]
    assert (restored_root / "throwtask.sqlite3").stat().st_mode & 0o077 == 0


def test_dependencies_cross_cli_mcp_and_http(live_system):
    config, _, _ = live_system
    first = cli(config, "tasks", "create", "Dependency first")["data"]
    task = cli(config, "tasks", "create", "Dependency then", "--depends-on", first["id"])["data"]
    assert task["effectiveState"] == "blocked" and task["state"] == "active"
    assert task["id"] in {
        item["id"] for item in cli(config, "tasks", "list", "--state", "blocked")["data"]["items"]
    }

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "throwtask.mcp_server", "--config", str(config)],
            env=dict(os.environ),
        )
        async with Client(parameters, mode="auto") as mcp:
            schemas = json.dumps([tool.input_schema for tool in (await mcp.list_tools()).tools])
            assert "dependencyIDs" in schemas and "blockReason" in schemas
            cycle = await mcp.call_tool(
                "update_task",
                {"task_id": first["id"], "expected_revision": 1, "changes": {"dependencyIDs": [task["id"]]}},
            )
            assert cycle.is_error and cycle.structured_content["error"]["code"] == "DEPENDENCY_CYCLE"
            done = await mcp.call_tool("complete_task", {"task_id": first["id"], "expected_revision": 1})
            assert not done.is_error
            ready = await mcp.call_tool("get_task", {"task_id": task["id"]})
            assert not ready.is_error and ready.structured_content["data"]["effectiveState"] == "active"

    asyncio.run(exercise())
    manual = cli(
        config,
        "tasks",
        "edit",
        task["id"],
        "--revision",
        "1",
        "--state",
        "blocked",
        "--block-reason",
        "Permissions missing",
    )["data"]
    assert manual["effectiveState"] == "blocked" and manual["blockReason"] == "Permissions missing"
    cleared = cli(
        config,
        "tasks",
        "edit",
        task["id"],
        "--revision",
        str(manual["revision"]),
        "--clear",
        "dependencyIDs",
        "--clear",
        "blockReason",
        "--state",
        "active",
    )["data"]
    assert (
        cleared["dependencyIDs"] == []
        and cleared["blockReason"] is None
        and cleared["effectiveState"] == "active"
    )
