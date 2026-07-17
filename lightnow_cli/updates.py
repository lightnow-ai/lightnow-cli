"""Managed LightNow CLI and Local Proxy update lifecycle."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from . import __version__

RELEASE_CATALOG_URL = (
    "https://raw.githubusercontent.com/lightnow-ai/homebrew-tap/main/releases.json"
)
UPDATE_STATE_PATH = Path.home() / ".lightnow" / "update-state.json"
UPDATE_PENDING_PATH = Path.home() / ".lightnow" / ".update-check-pending"
CHECK_INTERVAL = timedelta(hours=24)
PENDING_MAX_AGE = timedelta(minutes=10)
PACKAGES = {
    "lightnow-cli": {
        "executable": "lightnow",
        "formula": "lightnow-ai/tap/lightnow-cli",
    },
    "lightnow-proxy": {
        "executable": "lightnow-proxy",
        "formula": "lightnow-ai/tap/lightnow-proxy",
    },
}
VERSION_PATTERN = re.compile(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])")


@dataclass
class PackageState:
    installed_version: str | None
    install_method: str
    latest_version: str | None
    status: str
    executable: str | None
    result: str = "checked"
    error: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def detect_install_method(package: str, executable: str | None) -> str:
    if package not in PACKAGES or executable is None:
        return "unknown"
    try:
        path = str(Path(executable).expanduser().resolve()).lower()
    except OSError:
        path = executable.lower()
    normalized = package.lower()
    windows = path.replace("/", "\\")
    if f"/cellar/{normalized}/" in path or f"/homebrew/{normalized}/" in path:
        return "homebrew"
    if (
        f"/pipx/venvs/{normalized}/" in path
        or f"\\pipx\\venvs\\{normalized}\\" in windows
    ):
        return "pipx"
    if f"/uv/tools/{normalized}/" in path or f"\\uv\\tools\\{normalized}\\" in windows:
        return "uv"
    return "unknown"


def parse_executable_version(output: str) -> str | None:
    match = VERSION_PATTERN.search(output)
    return match.group(1) if match else None


def executable_version(executable: str | None) -> str | None:
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_executable_version(f"{result.stdout}\n{result.stderr}")


def installed_package_state(package: str, latest: str | None) -> PackageState:
    executable_name = PACKAGES[package]["executable"]
    executable = shutil.which(executable_name)
    installed = (
        __version__ if package == "lightnow-cli" else executable_version(executable)
    )
    method = detect_install_method(package, executable)
    return PackageState(
        installed_version=installed,
        install_method=method,
        latest_version=latest,
        status=compare_status(installed, latest),
        executable=executable,
    )


def compare_status(installed: str | None, latest: str | None) -> str:
    if installed is None or latest is None:
        return "unknown"
    try:
        current_version = Version(installed)
        latest_version = Version(latest)
    except InvalidVersion:
        return "unknown"
    if current_version < latest_version:
        return "outdated"
    if current_version > latest_version:
        return "ahead"
    return "current"


def fetch_release_catalog() -> dict[str, str]:
    response = httpx.get(RELEASE_CATALOG_URL, timeout=5.0, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported release catalog schema")
    packages = payload.get("packages")
    if not isinstance(packages, dict) or set(packages) != set(PACKAGES):
        raise ValueError("release catalog does not contain the supported packages")
    result: dict[str, str] = {}
    for package in PACKAGES:
        entry = packages.get(package)
        version = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(version, str):
            raise ValueError(f"release catalog entry is invalid: {package}")
        try:
            Version(version)
        except InvalidVersion as error:
            raise ValueError(
                f"release catalog version is invalid: {package}"
            ) from error
        result[package] = version
    return result


def build_update_state() -> dict[str, Any]:
    catalog = fetch_release_catalog()
    return {
        "schema_version": 1,
        "checked_at": _iso(_utc_now()),
        "packages": {
            package: asdict(installed_package_state(package, catalog[package]))
            for package in PACKAGES
        },
    }


def write_update_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = path or UPDATE_STATE_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary_path.exists():
            temporary_path.unlink()


def read_update_state(path: Path | None = None) -> dict[str, Any] | None:
    path = path or UPDATE_STATE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        payload
        if isinstance(payload, dict) and payload.get("schema_version") == 1
        else None
    )


def state_is_fresh(state: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if state is None or not isinstance(state.get("checked_at"), str):
        return False
    try:
        checked_at = datetime.fromisoformat(state["checked_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now or _utc_now()) - checked_at < CHECK_INTERVAL


def refresh_update_state(path: Path | None = None) -> dict[str, Any]:
    state = build_update_state()
    write_update_state(state, path or UPDATE_STATE_PATH)
    return state


def update_command(package: str, method: str) -> list[str] | None:
    if method == "homebrew":
        return ["brew", "upgrade", PACKAGES[package]["formula"]]
    if method == "pipx":
        return ["pipx", "upgrade", package]
    if method == "uv":
        return ["uv", "tool", "upgrade", package]
    return None


def _run(command: list[str], timeout: int = 300) -> tuple[bool, str | None]:
    executable = shutil.which(command[0])
    if executable is None:
        return False, f"{command[0]} is not available on PATH"
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode == 0:
        return True, None
    details = (result.stderr or result.stdout).strip().splitlines()
    return False, details[-1][:500] if details else f"exit code {result.returncode}"


def apply_updates(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    packages = state.get("packages")
    if not isinstance(packages, dict):
        return state, False
    success = True
    needs_brew = any(
        isinstance(entry, dict)
        and entry.get("status") == "outdated"
        and entry.get("install_method") == "homebrew"
        for entry in packages.values()
    )
    brew_ready = True
    if needs_brew:
        brew_ready, error = _run(["brew", "update"])
        if not brew_ready:
            success = False
            for entry in packages.values():
                if (
                    isinstance(entry, dict)
                    and entry.get("install_method") == "homebrew"
                    and entry.get("status") == "outdated"
                ):
                    entry["result"] = "failed"
                    entry["error"] = error

    for package in PACKAGES:
        entry = packages.get(package)
        if not isinstance(entry, dict) or entry.get("status") != "outdated":
            continue
        method = str(entry.get("install_method", "unknown"))
        command = update_command(package, method)
        if command is None:
            entry["result"] = "unsupported"
            entry["error"] = (
                "managed updates support Homebrew, pipx, and uv installations"
            )
            success = False
            continue
        if method == "homebrew" and not brew_ready:
            continue
        updated, error = _run(command)
        if not updated:
            entry["result"] = "failed"
            entry["error"] = error
            success = False
            continue
        executable = shutil.which(PACKAGES[package]["executable"])
        verified = executable_version(executable)
        entry["installed_version"] = verified
        entry["executable"] = executable
        entry["status"] = compare_status(verified, entry.get("latest_version"))
        entry["result"] = (
            "updated" if entry["status"] in {"current", "ahead"} else "failed"
        )
        entry["error"] = (
            None
            if entry["result"] == "updated"
            else "updated executable did not report the expected version"
        )
        success = success and entry["result"] == "updated"

    state["checked_at"] = _iso(_utc_now())
    write_update_state(state)
    return state, success


def should_check_automatically() -> bool:
    return bool(
        sys.stdout.isatty()
        and not os.environ.get("CI")
        and not os.environ.get("LIGHTNOW_NO_UPDATE_CHECK")
        and not os.environ.get("LIGHTNOW_UPDATE_HELPER")
    )


def cached_outdated_packages(state: dict[str, Any] | None) -> list[str]:
    packages = state.get("packages") if isinstance(state, dict) else None
    if not isinstance(packages, dict):
        return []
    return [
        name
        for name, entry in packages.items()
        if isinstance(entry, dict) and entry.get("status") == "outdated"
    ]


def start_background_refresh() -> None:
    state = read_update_state()
    if state_is_fresh(state):
        return
    UPDATE_PENDING_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if UPDATE_PENDING_PATH.exists():
        age = _utc_now() - datetime.fromtimestamp(
            UPDATE_PENDING_PATH.stat().st_mtime, UTC
        )
        if age < PENDING_MAX_AGE:
            return
        UPDATE_PENDING_PATH.unlink(missing_ok=True)
    try:
        descriptor = os.open(
            UPDATE_PENDING_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        return
    os.close(descriptor)
    environment = os.environ.copy()
    environment["LIGHTNOW_UPDATE_HELPER"] = "1"
    try:
        subprocess.Popen(
            [sys.executable, "-m", "lightnow_cli.main", "_refresh-update-state"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
    except OSError:
        UPDATE_PENDING_PATH.unlink(missing_ok=True)
