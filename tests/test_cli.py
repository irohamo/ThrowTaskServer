import json
import subprocess
import sys


def test_missing_local_configuration_has_exit_two(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mytodo.cli",
            "--config",
            str(tmp_path / "missing"),
            "--json",
            "auth",
            "status",
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "CONFIGURATION_REQUIRED"
    assert result.stderr == ""
