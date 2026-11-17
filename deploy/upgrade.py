#!/usr/bin/env python3
"""Upgrade the documented Debian/systemd installation. Requires only Python's standard library."""

import argparse
import fcntl
import io
import json
import os
import pwd
import re
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


class UpgradeError(Exception):
    def __init__(self, code, state=None):
        super().__init__(code)
        self.code = code
        self.state = state


class Runner:
    log = None

    def run(self, command, *, cwd=None, capture=False):
        environment = dict(os.environ)
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "UV_PROJECT",
            "UV_WORKING_DIRECTORY",
        ):
            environment.pop(key, None)
        result = subprocess.run(
            list(map(str, command)),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else self.log or subprocess.DEVNULL,
            stderr=self.log or subprocess.DEVNULL,
            check=False,
            env=environment,
        )
        if result.returncode:
            raise UpgradeError("COMMAND_FAILED")
        return result.stdout if capture else b""

    def text(self, command, *, cwd=None):
        return self.run(command, cwd=cwd, capture=True).decode().strip()


def admin_executable(repo):
    for name in ("throwtask-admin", "toidone-admin", "mytodo-admin"):
        executable = repo / ".venv/bin" / name
        if executable.is_file():
            return executable
    raise UpgradeError("INSTALLED_ADMIN_REQUIRED")


def plan(args, runner):
    repo = args.repo.resolve()
    data = args.data_dir.resolve()
    if not 1 <= args.port <= 65535:
        raise UpgradeError("INVALID_PORT")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.service", args.service):
        raise UpgradeError("INVALID_SERVICE")
    if data.is_relative_to(repo):
        raise UpgradeError("DATA_MUST_BE_OUTSIDE_REPOSITORY")
    if not any(
        (data / name).is_file() for name in ("throwtask.sqlite3", "toidone.sqlite3", "mytodo.sqlite3")
    ):
        raise UpgradeError("EXISTING_DATABASE_REQUIRED")
    if runner.text(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo):
        raise UpgradeError("DIRTY_WORKTREE")
    previous = runner.text(["git", "rev-parse", "HEAD"], cwd=repo)
    target = runner.text(
        ["git", "rev-parse", "--verify", "--end-of-options", args.ref + "^{commit}"], cwd=repo
    )
    if not all(re.fullmatch(r"[0-9a-f]{40,64}", value) for value in (previous, target)):
        raise UpgradeError("INVALID_REVISION")
    project = tomllib.loads(runner.text(["git", "show", target + ":pyproject.toml"], cwd=repo))["project"]
    version = project["version"]
    # Earlier releases do not report their version from health and need the manual recovery procedure.
    release = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[-+.])", version)
    if previous != target and (release is None or tuple(map(int, release.groups())) < (0, 2, 0)):
        raise UpgradeError("TARGET_VERSION_UNSUPPORTED")
    properties = runner.text(
        ["systemctl", "show", args.service, "--property=ActiveState,User,WorkingDirectory,ExecStart"]
    )
    service = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    command = re.search(r"argv\[\]=(.*?) ;", service.get("ExecStart", ""))
    argv = shlex.split(command[1]) if command else []
    if (
        Path(service.get("WorkingDirectory", "/")).resolve() != repo
        or not argv
        or Path(argv[0]).resolve()
        not in {
            (repo / ".venv/bin" / name).resolve()
            for name in ("throwtask-server", "toidone-server", "mytodo-server")
        }
        or "--data-dir" not in argv
        or argv.index("--data-dir") + 1 >= len(argv)
        or Path(argv[argv.index("--data-dir") + 1]).resolve() != data
        or (
            "--port" in argv
            and (argv.index("--port") + 1 >= len(argv) or argv[argv.index("--port") + 1] != str(args.port))
        )
        or ("--port" not in argv and args.port != 8766)
        or not service.get("User")
        or service["User"] == "root"
    ):
        raise UpgradeError("SERVICE_CONFIGURATION_MISMATCH")
    if service.get("ActiveState") != "active":
        raise UpgradeError("SERVICE_MUST_BE_ACTIVE")
    if Path(argv[0]).name not in project.get("scripts", {}):
        raise UpgradeError("TARGET_COMMAND_UNSUPPORTED")
    if (data / "throwtask.sqlite3").exists() and project["name"] != "throwtask-server":
        raise UpgradeError("TARGET_LAYOUT_UNSUPPORTED")
    if release is not None and tuple(map(int, release.groups())) < (0, 4, 0):
        database = next(
            data / name
            for name in ("throwtask.sqlite3", "toidone.sqlite3", "mytodo.sqlite3")
            if (data / name).is_file()
        )
        if database.is_symlink():
            raise UpgradeError("UNSAFE_DATABASE_PATH")
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            has_records = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'"
            ).fetchone()
            if (
                has_records
                and connection.execute(
                    "SELECT 1 FROM records WHERE kind='task' AND "
                    "(json_type(body,'$.dependencyIDs') IS NOT NULL OR "
                    "json_type(body,'$.blockReason') IS NOT NULL) LIMIT 1"
                ).fetchone()
            ):
                raise UpgradeError("TARGET_DATA_FORMAT_UNSUPPORTED")
    installed_admin = admin_executable(repo)
    return {
        "repository": str(repo),
        "dataDir": str(data),
        "service": args.service,
        "user": service["User"],
        "adminExecutable": str(installed_admin),
        "previousRevision": previous,
        "targetRevision": target,
        "targetVersion": version,
        "changed": previous != target,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_health(port, version):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(40):
        try:
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                value = json.loads(response.read(65536))
            data = value.get("data", {})
            if value.get("ok") and data.get("status") == "ok" and data.get("version") == version:
                return
            raise UpgradeError("HEALTH_VERSION_MISMATCH")
        except urllib.error.HTTPError as error:
            if error.code < 500:
                raise UpgradeError("HEALTH_HTTP_ERROR") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise UpgradeError("HEALTH_CHECK_FAILED")


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def apply(args, runner, selected):
    repo, data = Path(selected["repository"]), Path(selected["dataDir"])
    backups = (args.backup_dir or data / "backups").resolve()
    if backups.is_relative_to(repo):
        raise UpgradeError("BACKUPS_MUST_BE_OUTSIDE_REPOSITORY")
    account = pwd.getpwnam(selected["user"])
    if not backups.exists():
        backups.mkdir(parents=True, mode=0o700)
        os.chown(backups, account.pw_uid, account.pw_gid)
    run_id = "upgrade-" + uuid4().hex
    state = {
        **selected,
        "phase": "preflight",
        "stateFile": str(backups / (run_id + ".json")),
        "logFile": str(backups / (run_id + ".log")),
    }
    state_path = Path(state["stateFile"])
    save_state(state_path, state)
    stop_attempted = False

    def phase(value):
        state["phase"] = value
        save_state(state_path, state)

    def sync(directory, *, production=False):
        command = [
            args.uv,
            "sync",
            "--frozen",
            "--python",
            args.python,
            "--no-python-downloads",
            "--link-mode",
            "copy",
        ]
        if production:
            command += ["--no-dev", "--offline"]
        runner.run(command, cwd=directory)

    def backup(name):
        output = backups / (run_id + "-" + name + ".sqlite3")
        runner.run(
            [
                "runuser",
                "-u",
                selected["user"],
                "--",
                selected["adminExecutable"],
                "--data-dir",
                data,
                "backup",
                "--output",
                output,
            ]
        )
        state[name + "Backup"] = str(output)
        save_state(state_path, state)
        return output

    log = os.fdopen(os.open(state["logFile"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
    runner.log = log
    try:
        with tempfile.TemporaryDirectory(prefix="throwtask-upgrade-") as directory:
            staging = Path(directory) / "source"
            staging.mkdir()
            archive = runner.run(["git", "archive", selected["targetRevision"]], cwd=repo, capture=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(staging, filter="data")
            sync(staging)
            runner.run([staging / ".venv/bin/ruff", "check", "src", "tests", "deploy"], cwd=staging)
            runner.run(
                [staging / ".venv/bin/ruff", "format", "--check", "src", "tests", "deploy"], cwd=staging
            )
            runner.run([staging / ".venv/bin/python", "-m", "pytest", "-q"], cwd=staging)
            preflight = backup("preflight")
            rehearsal = Path(directory) / "restored"
            staged_admin = admin_executable(staging)
            runner.run([staged_admin, "--data-dir", rehearsal, "restore", "--backup", preflight])
            runner.run([staged_admin, "--data-dir", rehearsal, "init"])
            if plan(args, runner) != selected:
                raise UpgradeError("INSTALLATION_CHANGED")
            phase("stopping")
            stop_attempted = True
            runner.run(["systemctl", "stop", args.service])
            backup("stopped")
            phase("switching")
            runner.run(["git", "checkout", "--detach", selected["targetRevision"]], cwd=repo)
            sync(repo, production=True)
            phase("initializing")
            runner.run(
                [
                    "runuser",
                    "-u",
                    selected["user"],
                    "--",
                    admin_executable(repo),
                    "--data-dir",
                    data,
                    "init",
                ]
            )
            phase("starting")
            runner.run(["systemctl", "start", args.service])
            check_health(args.port, selected["targetVersion"])
            if runner.text(["systemctl", "is-active", args.service]) != "active":
                raise UpgradeError("SERVICE_NOT_ACTIVE")
            phase("complete")
    except BaseException as error:
        failed_phase = state["phase"]
        if stop_attempted:
            try:
                runner.run(["systemctl", "stop", args.service])
                state["serviceStopped"] = True
            except Exception:
                state["serviceStopped"] = False
        state.update(phase="failed", failedPhase=failed_phase)
        state["error"] = error.code if isinstance(error, UpgradeError) else "UPGRADE_FAILED"
        save_state(state_path, state)
        raise UpgradeError(state["error"], state) from None
    finally:
        runner.log = None
        log.close()
    return state


def installation_defaults():
    # Existing systemd installations retain their service account, paths and database.
    if not Path("/opt/throwtask-server").exists() and Path("/opt/toidone-server").exists():
        return Path("/opt/toidone-server"), Path("/var/lib/toidone"), "toidone-server.service"
    return Path("/opt/throwtask-server"), Path("/var/lib/throwtask"), "throwtask-server.service"


def parser():
    repo, data, service = installation_defaults()
    root = argparse.ArgumentParser(description="Plan or apply a pinned ThrowTask Debian/systemd upgrade")
    root.add_argument("--repo", type=Path, default=repo)
    root.add_argument("--data-dir", type=Path, default=data)
    root.add_argument("--backup-dir", type=Path)
    root.add_argument("--service", default=service)
    root.add_argument(
        "--ref", required=True, help="Already fetched release tag or commit; never pulls or force-resets"
    )
    root.add_argument("--uv", default="uv", help="Path to uv; installed code uses system Python")
    root.add_argument("--python", default="/usr/bin/python3")
    root.add_argument("--port", type=int, default=8766, metavar="PORT")
    root.add_argument(
        "--apply", action="store_true", help="Execute; default only reads the installation and prints a plan"
    )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    runner = Runner()
    try:
        selected = plan(args, runner)
        if not args.apply or not selected["changed"]:
            result = {"ok": True, "data": {**selected, "applied": False}}
        else:
            if os.geteuid() != 0:
                raise UpgradeError("ROOT_REQUIRED")
            os.umask(0o022)
            args.uv = shutil.which(args.uv)
            if args.uv is None or not Path(args.python).is_file():
                raise UpgradeError("RUNTIME_REQUIRED")
            git_dir = Path(runner.text(["git", "rev-parse", "--absolute-git-dir"], cwd=args.repo))
            # Share the lock with the pre-rename helper to prevent concurrent upgrades.
            with (git_dir / "toidone-upgrade.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise UpgradeError("UPGRADE_IN_PROGRESS") from None
                if plan(args, runner) != selected:
                    raise UpgradeError("INSTALLATION_CHANGED")
                result = {"ok": True, "data": apply(args, runner, selected)}
    except UpgradeError as error:
        result = {"ok": False, "error": {"code": error.code}}
        if error.state:
            result["data"] = error.state
    except Exception:
        result = {"ok": False, "error": {"code": "LOCAL_ERROR"}}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
