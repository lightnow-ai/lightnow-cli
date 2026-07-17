import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from lightnow_cli import updates
from lightnow_cli.main import app


def package_state(
    installed: str, latest: str, method: str, status: str = "outdated"
) -> dict:
    return {
        "installed_version": installed,
        "install_method": method,
        "latest_version": latest,
        "status": status,
        "executable": f"/bin/{'lightnow' if method != 'missing' else 'none'}",
        "result": "checked",
        "error": None,
    }


def update_state() -> dict:
    return {
        "schema_version": 1,
        "checked_at": "2026-07-17T08:00:00Z",
        "packages": {
            "lightnow-cli": package_state("1.3.1", "1.4.0", "homebrew"),
            "lightnow-proxy": package_state("1.4.1", "1.4.2", "uv"),
        },
    }


def test_compare_status_covers_all_public_states() -> None:
    assert updates.compare_status("1.0.0", "1.0.0") == "current"
    assert updates.compare_status("1.0.0", "1.0.1") == "outdated"
    assert updates.compare_status("2.0.0", "1.0.1") == "ahead"
    assert updates.compare_status(None, "1.0.1") == "unknown"
    assert updates.compare_status("editable", "1.0.1") == "unknown"


def test_executable_version_handles_success_failure_and_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        updates.subprocess,
        "run",
        lambda *args, **kwargs: MagicMock(
            returncode=0, stdout="LightNow Proxy 1.4.2", stderr=""
        ),
    )
    assert updates.executable_version("/bin/lightnow-proxy") == "1.4.2"
    assert updates.executable_version(None) is None
    monkeypatch.setattr(
        updates.subprocess,
        "run",
        lambda *args, **kwargs: MagicMock(returncode=1, stdout="", stderr="failed"),
    )
    assert updates.executable_version("/bin/lightnow-proxy") is None
    monkeypatch.setattr(
        updates.subprocess, "run", MagicMock(side_effect=OSError("gone"))
    )
    assert updates.executable_version("/bin/lightnow-proxy") is None
    assert updates.parse_executable_version("no stable version") is None


def test_build_and_refresh_update_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        updates,
        "fetch_release_catalog",
        lambda: {"lightnow-cli": "1.4.0", "lightnow-proxy": "1.4.2"},
    )
    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(updates, "executable_version", lambda executable: "1.4.1")
    monkeypatch.setattr(
        updates, "_utc_now", lambda: datetime(2026, 7, 17, 8, tzinfo=UTC)
    )

    state = updates.refresh_update_state(tmp_path / "state.json")

    assert state["checked_at"] == "2026-07-17T08:00:00Z"
    assert state["packages"]["lightnow-cli"]["installed_version"] == updates.__version__
    assert state["packages"]["lightnow-proxy"]["status"] == "outdated"
    assert updates.read_update_state(tmp_path / "state.json") == state


def test_read_and_freshness_reject_malformed_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    assert updates.read_update_state(path) is None
    path.write_text("not json")
    assert updates.read_update_state(path) is None
    path.write_text(json.dumps({"schema_version": 2}))
    assert updates.read_update_state(path) is None
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    assert updates.state_is_fresh(None, now) is False
    assert updates.state_is_fresh({"checked_at": "invalid"}, now) is False
    assert updates.state_is_fresh({"checked_at": "2026-07-17T08:00:00Z"}, now) is True
    assert updates.state_is_fresh({"checked_at": "2026-07-15T08:00:00Z"}, now) is False
    assert updates.state_is_fresh({"checked_at": "2026-07-18T08:00:00Z"}, now) is False
    assert updates.state_is_fresh({"checked_at": "2026-07-17T08:00:00"}, now) is False


def test_installer_detection_and_commands_are_allowlisted() -> None:
    assert (
        updates.detect_install_method(
            "lightnow-cli", "/opt/homebrew/Cellar/lightnow-cli/1.3.1/bin/lightnow"
        )
        == "homebrew"
    )
    assert (
        updates.detect_install_method(
            "lightnow-proxy",
            "/home/me/.local/pipx/venvs/lightnow-proxy/bin/lightnow-proxy",
        )
        == "pipx"
    )
    assert (
        updates.detect_install_method(
            "lightnow-proxy",
            "/home/me/.local/share/uv/tools/lightnow-proxy/bin/lightnow-proxy",
        )
        == "uv"
    )
    assert (
        updates.detect_install_method("lightnow-cli", "/tmp/venv/bin/lightnow")
        == "unknown"
    )
    assert updates.update_command("lightnow-cli", "homebrew") == [
        "brew",
        "upgrade",
        "lightnow-ai/tap/lightnow-cli",
    ]
    assert updates.update_command("lightnow-proxy", "pipx") == [
        "pipx",
        "upgrade",
        "lightnow-proxy",
    ]
    assert updates.update_command("lightnow-proxy", "uv") == [
        "uv",
        "tool",
        "upgrade",
        "lightnow-proxy",
    ]
    assert updates.update_command("lightnow-cli", "unknown") is None


def test_fetch_release_catalog_validates_the_public_contract(monkeypatch) -> None:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {
        "schema_version": 1,
        "generated_at": "2026-07-17T08:00:00Z",
        "packages": {
            "lightnow-cli": {"version": "1.4.0"},
            "lightnow-proxy": {"version": "1.4.2"},
        },
    }
    monkeypatch.setattr(updates.httpx, "get", lambda *args, **kwargs: response)

    assert updates.fetch_release_catalog() == {
        "lightnow-cli": "1.4.0",
        "lightnow-proxy": "1.4.2",
    }
    response.raise_for_status.assert_called_once()


def test_fetch_release_catalog_rejects_invalid_contracts(monkeypatch) -> None:
    response = MagicMock(spec=httpx.Response)
    monkeypatch.setattr(updates.httpx, "get", lambda *args, **kwargs: response)
    response.json.return_value = {"schema_version": 2}
    with pytest.raises(ValueError, match="schema"):
        updates.fetch_release_catalog()
    response.json.return_value = {"schema_version": 1, "packages": {}}
    with pytest.raises(ValueError, match="supported packages"):
        updates.fetch_release_catalog()
    response.json.return_value = {
        "schema_version": 1,
        "packages": {
            "lightnow-cli": {"version": 4},
            "lightnow-proxy": {"version": "1.4.2"},
        },
    }
    with pytest.raises(ValueError, match="entry"):
        updates.fetch_release_catalog()
    response.json.return_value["packages"]["lightnow-cli"]["version"] = "not-a-version"
    with pytest.raises(ValueError, match="version"):
        updates.fetch_release_catalog()


def test_write_update_state_is_atomic_and_private(tmp_path) -> None:
    target = tmp_path / ".lightnow" / "update-state.json"
    state = update_state()
    updates.write_update_state(state, target)

    assert json.loads(target.read_text()) == state
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.parent.stat().st_mode & 0o777 == 0o700


def test_write_update_state_works_without_fchmod(monkeypatch, tmp_path) -> None:
    target = tmp_path / ".lightnow" / "update-state.json"
    monkeypatch.delattr(updates.os, "fchmod", raising=False)

    updates.write_update_state(update_state(), target)

    assert json.loads(target.read_text()) == update_state()


def test_apply_updates_runs_brew_update_once_and_verifies_components(
    monkeypatch, tmp_path
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout: int = 300):
        commands.append(command)
        return True, None

    monkeypatch.setattr(updates, "_run", fake_run)
    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        updates,
        "executable_version",
        lambda executable: "1.4.0" if executable.endswith("lightnow") else "1.4.2",
    )
    monkeypatch.setattr(updates, "UPDATE_STATE_PATH", tmp_path / "state.json")

    state, success = updates.apply_updates(update_state())

    assert success is True
    assert commands == [
        ["brew", "update"],
        ["brew", "upgrade", "lightnow-ai/tap/lightnow-cli"],
        ["uv", "tool", "upgrade", "lightnow-proxy"],
    ]
    assert state["packages"]["lightnow-cli"]["result"] == "updated"
    assert state["packages"]["lightnow-proxy"]["result"] == "updated"


def test_apply_updates_refuses_unknown_installer(monkeypatch, tmp_path) -> None:
    state = update_state()
    state["packages"]["lightnow-cli"]["install_method"] = "unknown"
    state["packages"]["lightnow-proxy"]["status"] = "current"
    monkeypatch.setattr(updates, "UPDATE_STATE_PATH", tmp_path / "state.json")

    state, success = updates.apply_updates(state)

    assert success is False
    assert state["packages"]["lightnow-cli"]["result"] == "unsupported"


def test_run_reports_manager_results_without_a_shell(monkeypatch) -> None:
    monkeypatch.setattr(updates.shutil, "which", lambda name: None)
    assert updates._run(["brew", "update"])[0] is False
    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(updates.subprocess, "run", run)
    assert updates._run(["brew", "update"]) == (True, None)
    assert run.call_args.args[0] == ["/bin/brew", "update"]
    assert "shell" not in run.call_args.kwargs
    run.return_value = MagicMock(returncode=2, stdout="", stderr="first\nlast error")
    assert updates._run(["brew", "update"]) == (False, "last error")
    run.side_effect = subprocess.TimeoutExpired("brew", 1)
    assert updates._run(["brew", "update"], timeout=1)[0] is False


def test_apply_updates_propagates_brew_and_verification_failures(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(updates, "UPDATE_STATE_PATH", tmp_path / "state.json")
    state = update_state()
    monkeypatch.setattr(updates, "_run", lambda command: (False, "tap unavailable"))
    state, success = updates.apply_updates(state)
    assert success is False
    assert state["packages"]["lightnow-cli"]["error"] == "tap unavailable"
    assert state["packages"]["lightnow-proxy"]["result"] == "failed"

    state = update_state()
    state["packages"]["lightnow-cli"]["status"] = "current"
    monkeypatch.setattr(updates, "_run", lambda command: (True, None))
    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(updates, "executable_version", lambda executable: "1.4.1")
    state, success = updates.apply_updates(state)
    assert success is False
    assert (
        state["packages"]["lightnow-proxy"]["error"]
        == "updated executable did not report the expected version"
    )


def test_automatic_check_policy_and_background_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(updates.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("LIGHTNOW_NO_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("LIGHTNOW_UPDATE_HELPER", raising=False)
    assert updates.should_check_automatically() is True
    monkeypatch.setenv("CI", "1")
    assert updates.should_check_automatically() is False
    monkeypatch.delenv("CI")

    pending = tmp_path / ".pending"
    monkeypatch.setattr(updates, "UPDATE_PENDING_PATH", pending)
    monkeypatch.setattr(updates, "read_update_state", lambda: None)
    process = MagicMock()
    monkeypatch.setattr(updates.subprocess, "Popen", process)
    updates.start_background_refresh()
    assert pending.exists()
    assert process.call_args.kwargs["env"]["LIGHTNOW_UPDATE_HELPER"] == "1"
    process.reset_mock()
    updates.start_background_refresh()
    process.assert_not_called()


def test_background_refresh_skips_fresh_state_and_cleans_up_spawn_failure(
    monkeypatch, tmp_path
) -> None:
    pending = tmp_path / ".pending"
    monkeypatch.setattr(updates, "UPDATE_PENDING_PATH", pending)
    monkeypatch.setattr(
        updates,
        "read_update_state",
        lambda: {"checked_at": updates._iso(updates._utc_now())},
    )
    process = MagicMock()
    monkeypatch.setattr(updates.subprocess, "Popen", process)
    updates.start_background_refresh()
    process.assert_not_called()

    monkeypatch.setattr(updates, "read_update_state", lambda: None)
    monkeypatch.setattr(
        updates.subprocess, "Popen", MagicMock(side_effect=OSError("no spawn"))
    )
    updates.start_background_refresh()
    assert pending.exists() is False


def test_background_refresh_tolerates_pending_file_removal(
    monkeypatch, tmp_path
) -> None:
    pending = tmp_path / ".pending"
    pending.touch()
    monkeypatch.setattr(updates, "UPDATE_PENDING_PATH", pending)
    monkeypatch.setattr(updates, "read_update_state", lambda: None)
    original_stat = Path.stat

    def disappearing_stat(path: Path, *args, **kwargs):
        if path == pending:
            path.unlink()
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)
    process = MagicMock()
    monkeypatch.setattr(updates.subprocess, "Popen", process)

    updates.start_background_refresh()

    process.assert_called_once()


def test_update_check_json_does_not_install(monkeypatch) -> None:
    state = update_state()
    monkeypatch.setattr(updates, "refresh_update_state", lambda: state)
    apply = MagicMock()
    monkeypatch.setattr(updates, "apply_updates", apply)

    result = CliRunner().invoke(app, ["update", "--check", "--json"])

    assert result.exit_code == 0
    assert (
        json.loads(result.stdout)["packages"]["lightnow-proxy"]["status"] == "outdated"
    )
    apply.assert_not_called()


def test_update_yes_reports_partial_failure(monkeypatch) -> None:
    state = update_state()
    failed = update_state()
    failed["packages"]["lightnow-proxy"]["result"] = "failed"
    failed["packages"]["lightnow-proxy"]["error"] = "package manager failed"
    monkeypatch.setattr(updates, "refresh_update_state", lambda: state)
    monkeypatch.setattr(updates, "apply_updates", lambda current: (failed, False))

    result = CliRunner().invoke(app, ["update", "--yes", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["packages"]["lightnow-proxy"]["result"] == "failed"


def test_update_command_handles_noop_decline_and_check_errors(monkeypatch) -> None:
    state = update_state()
    for entry in state["packages"].values():
        entry["status"] = "current"
    monkeypatch.setattr(updates, "refresh_update_state", lambda: state)
    result = CliRunner().invoke(app, ["update"])
    assert result.exit_code == 0
    assert "LightNow updates" in result.stdout

    outdated = update_state()
    monkeypatch.setattr(updates, "refresh_update_state", lambda: outdated)
    apply = MagicMock()
    monkeypatch.setattr(updates, "apply_updates", apply)
    result = CliRunner().invoke(app, ["update"], input="n\n")
    assert result.exit_code == 0
    apply.assert_not_called()

    monkeypatch.setattr(
        updates,
        "refresh_update_state",
        MagicMock(side_effect=RuntimeError("catalog down")),
    )
    result = CliRunner().invoke(app, ["update", "--check", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "catalog down"


def test_update_option_combinations_are_non_interactive() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["update", "--check", "--yes"]).exit_code == 2
    assert runner.invoke(app, ["update", "--json"]).exit_code == 2
