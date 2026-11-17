"""Real disposable Git/SQLite files, with systemd and installation boundaries replaced by a fake runner."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from throwtask import __version__

spec = importlib.util.spec_from_file_location("upgrade", Path(__file__).parents[1] / "deploy/upgrade.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


def git(repo, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=repo, stderr=subprocess.DEVNULL).decode().strip()


class FakeRunner(upgrade.Runner):
    def __init__(self, repo, data, *, fail=None, program_prefix="throwtask"):
        self.repo, self.data, self.fail = repo, data, fail
        self.program_prefix = program_prefix
        self.calls = []
        self.active = True

    def run(self, command, *, cwd=None, capture=False):
        command = list(map(str, command))
        self.calls.append((command, cwd))
        if command[0] == "git":
            return super().run(command, cwd=cwd, capture=capture)
        if command[:2] == ["systemctl", "show"]:
            user = "nobody"
            return (
                f"ActiveState={'active' if self.active else 'inactive'}\n"
                f"User={user}\nWorkingDirectory={self.repo}\n"
                f"ExecStart={{ path={self.repo}/.venv/bin/{self.program_prefix}-server ; "
                f"argv[]={self.repo}/.venv/bin/{self.program_prefix}-server --data-dir {self.data} "
                "--host 127.0.0.1 --port 8766 ; }"
            ).encode()
        if command[:2] == ["systemctl", "stop"]:
            self.active = False
        elif command[:2] == ["systemctl", "start"]:
            if self.fail == "start":
                raise upgrade.UpgradeError("COMMAND_FAILED")
            self.active = True
        elif command[:2] == ["systemctl", "is-active"]:
            return b"active" if self.active else b"inactive"
        elif "backup" in command:
            output = Path(command[command.index("--output") + 1])
            source = sqlite3.connect(next(path for path in self.data.glob("*.sqlite3") if path.is_file()))
            destination = sqlite3.connect(output)
            source.backup(destination)
            source.close()
            destination.close()
        elif "restore" in command:
            if self.fail == "rehearsal":
                raise upgrade.UpgradeError("COMMAND_FAILED")
        elif "sync" in command:
            if cwd == self.repo and self.fail == "sync":
                raise upgrade.UpgradeError("COMMAND_FAILED")
            installed = Path(cwd) / ".venv/bin"
            installed.mkdir(parents=True, exist_ok=True)
            (installed / "throwtask-admin").touch()
        return b""


@pytest.fixture
def installation(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    data.mkdir()
    (data / "backups").mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Upgrade test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / ".gitignore").write_text(".venv/\n")

    def project(version):
        return (
            '[project]\nname="throwtask-server"\nversion="' + version + '"\n'
            '[project.scripts]\nthrowtask-server="throwtask.server:main"\n'
            'toidone-server="throwtask.server:main"\nmytodo-server="throwtask.server:main"\n'
        )

    (repo / "pyproject.toml").write_text(project("0.1.0"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Old installation")
    previous = git(repo, "rev-parse", "HEAD")
    (repo / "pyproject.toml").write_text(project("0.2.0"))
    git(repo, "commit", "-qam", "New release")
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", previous)
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/throwtask-admin").touch()
    connection = sqlite3.connect(data / "throwtask.sqlite3")
    connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, revision INTEGER)")
    connection.execute("INSERT INTO tasks VALUES ('keep', 7)")
    connection.commit()
    connection.close()
    args = upgrade.parser().parse_args(["--repo", str(repo), "--data-dir", str(data), "--ref", target])
    return args, repo, data, previous, target


def test_default_plan_performs_no_installation_backup_or_service_change(installation, monkeypatch, capsys):
    args, repo, data, previous, target = installation
    runner = FakeRunner(repo, data)
    monkeypatch.setattr(upgrade, "Runner", lambda: runner)
    assert upgrade.main(["--repo", str(repo), "--data-dir", str(data), "--ref", target]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["targetVersion"] == "0.2.0" and result["previousRevision"] == previous
    assert result["applied"] is False and runner.active
    assert not list((data / "backups").iterdir())
    assert all(command[0] == "git" or command[:2] == ["systemctl", "show"] for command, _ in runner.calls)


def test_dirty_repository_is_left_untouched(installation):
    args, repo, data, previous, _ = installation
    (repo / "user-notes.txt").write_text("Keep my changes")
    with pytest.raises(upgrade.UpgradeError, match="DIRTY_WORKTREE"):
        upgrade.plan(args, FakeRunner(repo, data))
    assert git(repo, "rev-parse", "HEAD") == previous
    assert (repo / "user-notes.txt").read_text() == "Keep my changes"


def test_plan_accepts_a_repository_path_alias(installation):
    args, repo, data, _, target = installation
    alias = repo.parent / "source-alias"
    alias.symlink_to(repo, target_is_directory=True)
    args.repo = alias
    selected = upgrade.plan(args, FakeRunner(alias, data))
    assert selected["repository"] == str(repo.resolve()) and selected["targetRevision"] == target


def test_old_target_requires_manual_recovery_before_any_service_change(installation):
    args, repo, data, previous, target = installation
    git(repo, "checkout", "--detach", target)
    args.ref = previous
    runner = FakeRunner(repo, data)
    with pytest.raises(upgrade.UpgradeError, match="TARGET_VERSION_UNSUPPORTED"):
        upgrade.plan(args, runner)
    assert runner.active and not list((data / "backups").iterdir())


def test_success_keeps_data_and_records_both_backups_and_previous_revision(installation, monkeypatch):
    args, repo, data, previous, target = installation
    runner = FakeRunner(repo, data)
    observed = []
    monkeypatch.setattr(upgrade, "check_health", lambda port, version: observed.append((port, version)))
    state = upgrade.apply(args, runner, upgrade.plan(args, runner))
    assert state["phase"] == "complete" and state["previousRevision"] == previous
    assert git(repo, "rev-parse", "HEAD") == target and runner.active
    assert observed == [(8766, "0.2.0")]
    assert Path(state["stateFile"]).stat().st_mode & 0o077 == 0
    for name in ("preflightBackup", "stoppedBackup"):
        with sqlite3.connect(state[name]) as connection:
            assert connection.execute("SELECT * FROM tasks").fetchall() == [("keep", 7)]
    production_sync = [command for command, cwd in runner.calls if cwd == repo and "sync" in command]
    assert len(production_sync) == 1 and {"--offline", "--frozen", "--no-dev"} <= set(production_sync[0])


@pytest.mark.parametrize("failure", ["sync", "start", "health"])
def test_failure_after_stop_keeps_database_and_backups_without_automatic_restore(
    installation, monkeypatch, failure
):
    args, repo, data, previous, _ = installation
    runner = FakeRunner(repo, data, fail=failure)
    monkeypatch.setattr(
        upgrade,
        "check_health",
        lambda *args: (_ for _ in ()).throw(upgrade.UpgradeError("HEALTH_CHECK_FAILED")),
    )
    with pytest.raises(upgrade.UpgradeError) as error:
        upgrade.apply(args, runner, upgrade.plan(args, runner))
    state = error.value.state
    assert state["phase"] == "failed" and state["previousRevision"] == previous
    assert state["serviceStopped"] is True and not runner.active
    assert Path(state["stoppedBackup"]).exists()
    with sqlite3.connect(data / "throwtask.sqlite3") as connection:
        assert connection.execute("SELECT * FROM tasks").fetchall() == [("keep", 7)]
    for command, _ in runner.calls:
        if "restore" in command:
            assert Path(command[command.index("--data-dir") + 1]) != data


def test_rehearsal_failure_leaves_the_running_service_and_code_unchanged(installation):
    args, repo, data, previous, _ = installation
    runner = FakeRunner(repo, data, fail="rehearsal")
    with pytest.raises(upgrade.UpgradeError) as error:
        upgrade.apply(args, runner, upgrade.plan(args, runner))
    assert error.value.state["failedPhase"] == "preflight"
    assert runner.active and git(repo, "rev-parse", "HEAD") == previous
    assert not any(command[:2] == ["systemctl", "stop"] for command, _ in runner.calls)


@pytest.mark.parametrize("prefix", ["toidone", "mytodo"])
def test_upgrade_preserves_legacy_database_and_service_commands(installation, monkeypatch, prefix):
    args, repo, data, _, _ = installation
    (data / "throwtask.sqlite3").rename(data / (prefix + ".sqlite3"))
    (repo / ".venv/bin/throwtask-admin").rename(repo / (".venv/bin/" + prefix + "-admin"))
    runner = FakeRunner(repo, data, program_prefix=prefix)
    monkeypatch.setattr(upgrade, "check_health", lambda *args: None)
    selected = upgrade.plan(args, runner)
    assert Path(selected["adminExecutable"]).name == prefix + "-admin"
    result = upgrade.apply(args, runner, selected)
    assert result["phase"] == "complete" and runner.active
    assert not (data / "throwtask.sqlite3").exists()
    with sqlite3.connect(data / (prefix + ".sqlite3")) as connection:
        assert connection.execute("SELECT * FROM tasks").fetchall() == [("keep", 7)]
    backups = [command for command, _ in runner.calls if "backup" in command]
    assert len(backups) == 2 and all(prefix + "-admin" in command[4] for command in backups)


@pytest.mark.parametrize("missing_command", [True, False])
def test_renamed_installation_rejects_incompatible_target_before_stop(installation, missing_command):
    args, repo, data, previous, target = installation
    git(repo, "checkout", "--detach", target)
    metadata = (repo / "pyproject.toml").read_text()
    if missing_command:
        metadata = metadata.replace('throwtask-server="throwtask.server:main"\n', "")
    else:
        metadata = metadata.replace('name="throwtask-server"', 'name="toidone-server"')
    (repo / "pyproject.toml").write_text(metadata)
    git(repo, "commit", "-qam", "Incompatible target")
    args.ref = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", previous)
    runner = FakeRunner(repo, data)
    expected = "TARGET_COMMAND_UNSUPPORTED" if missing_command else "TARGET_LAYOUT_UNSUPPORTED"
    with pytest.raises(upgrade.UpgradeError, match=expected):
        upgrade.plan(args, runner)
    assert runner.active and not list((data / "backups").iterdir())


@pytest.mark.parametrize("module", ["cli", "server", "admin", "mcp_server"])
def test_version_commands_need_no_configuration(module, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "throwtask." + module, "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert result.returncode == 0 and result.stdout.strip() == "ThrowTask " + __version__
    assert result.stderr == ""


def test_api_and_packaging_versions_match(system):
    client, *_ = system
    client.headers.pop("Authorization")
    assert client.get("/healthz").json()["data"] == {"status": "ok", "version": __version__}
    assert client.get("/openapi.json").json()["info"]["version"] == __version__
    root = Path(__file__).parents[1]
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == __version__
    lock = tomllib.loads((root / "uv.lock").read_text())
    assert next(p["version"] for p in lock["package"] if p["name"] == "throwtask-server") == __version__
