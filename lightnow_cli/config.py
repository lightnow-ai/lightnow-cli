"""Configuration and token management."""

import hashlib
import json
import os
import tempfile
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import typer
from filelock import FileLock
from pydantic import BaseModel, Field

from .target import ConnectionTarget

DEFAULT_ISSUER = "https://auth.lightnow.ai/realms/lightnow"
DEFAULT_CLIENT_ID = "lightnow-cli"
DEFAULT_REGISTRY_API_URL = "https://registry-api.lightnow.ai/v0.1"
DEFAULT_ADMIN_API_URL = "https://admin-api.lightnow.ai/v0/portal"
LOCAL_ISSUER = "https://auth.lightnow.local/realms/lightnow-local"
LOCAL_REGISTRY_API_URL = "https://registry-api.lightnow.local/v0.1"
LOCAL_ADMIN_API_URL = "https://admin-api.lightnow.local/v0/portal"


def _serialized_write(method: Callable[..., Any]) -> Callable[..., Any]:
    """Use one reentrant config lock before any per-session lock."""

    @wraps(method)
    def wrapped(self: "ConfigManager", *args: Any, **kwargs: Any) -> Any:
        with self._transaction_lock():
            self._refresh_for_write()
            return method(self, *args, **kwargs)

    return wrapped


class Config(BaseModel):
    """CLI configuration model."""

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    issuer: Optional[str] = Field(default=DEFAULT_ISSUER)
    client_id: Optional[str] = Field(default=DEFAULT_CLIENT_ID)
    registry_api_url: Optional[str] = Field(default=DEFAULT_REGISTRY_API_URL)
    admin_api_url: Optional[str] = Field(default=DEFAULT_ADMIN_API_URL)
    active_session_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{24}$")
    user_info: Optional[Dict[str, Any]] = None
    context_type: str = Field(default="personal")
    context_tenant: Optional[str] = None
    context_label: Optional[str] = None
    device_installation_id: Optional[str] = None


class ConfigManager:
    """Manages CLI configuration and token storage."""

    def __init__(self) -> None:
        self.config_dir = Path.home() / ".lightnow"
        self.config_file = self.config_dir / "config.json"
        self._config: Optional[Config] = None
        self._loaded_binding: Optional[tuple[Any, ...]] = None
        self._write_lock: Optional[FileLock] = None

    def _transaction_lock(self) -> FileLock:
        self._ensure_config_dir()
        lock_path = str(self.config_file) + ".lock"
        if self._write_lock is None or self._write_lock.lock_file != lock_path:
            self._write_lock = FileLock(lock_path)
        return self._write_lock

    @staticmethod
    def _target(config: Config) -> ConnectionTarget:
        return ConnectionTarget(
            config.issuer or DEFAULT_ISSUER,
            config.client_id or DEFAULT_CLIENT_ID,
            config.registry_api_url or DEFAULT_REGISTRY_API_URL,
            config.admin_api_url or DEFAULT_ADMIN_API_URL,
        )

    @classmethod
    def _binding(cls, config: Config) -> tuple[Any, ...]:
        return (
            *cls._target(config).environment,
            config.active_session_id or (config.user_info or {}).get("sub"),
        )

    def connection_binding(self, config: Config) -> tuple[Any, ...]:
        """Capture the selected connection before a network refresh."""
        return self._binding(config)

    def _subject(self, config: Config) -> Optional[str]:
        subject = (config.user_info or {}).get("sub")
        if isinstance(subject, str):
            return subject
        if config.active_session_id:
            try:
                session = json.loads(
                    (self.sessions_dir / f"{config.active_session_id}.json").read_text()
                )
            except (OSError, ValueError):
                return None
            if isinstance(session, dict) and session.get("issuer") == config.issuer:
                subject = session.get("subject")
                if isinstance(subject, str):
                    return subject
        return None

    @staticmethod
    def _session_id(target: ConnectionTarget, subject: str) -> str:
        binding = json.dumps([*target.environment, subject], separators=(",", ":"))
        return hashlib.sha256(binding.encode()).hexdigest()[:24]

    def _disk_config(self) -> Config:
        if not self.config_file.exists():
            return Config()
        try:
            return Config.model_validate_json(self.config_file.read_bytes())
        except (OSError, ValueError):
            raise ValueError(
                "Cannot safely update the existing CLI configuration."
            ) from None

    def _refresh_for_write(self) -> None:
        current = self._disk_config()
        binding = self._binding(current)
        if self._loaded_binding is not None and self._loaded_binding != binding:
            raise ValueError(
                "The LightNow connection changed. Repeat the command in a fresh session."
            )
        self._config = current
        self._load_active_session_tokens(current)
        self._loaded_binding = binding

    def _ensure_config_dir(self) -> None:
        """Ensure config directory exists."""
        self.config_dir.mkdir(mode=0o700, exist_ok=True)
        self.config_dir.chmod(0o700)

    @property
    def sessions_dir(self) -> Path:
        """Return the private directory containing account-bound sessions."""
        return self.config_dir / "sessions"

    def load_config(self) -> Config:
        """Load configuration from file."""
        if self._config is not None:
            return self._config

        # Start with default config
        config_data = {}

        # Load from file if exists
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    config_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                typer.echo(f"Warning: Failed to load config file: {e}", err=True)

        self._config = Config(**config_data)
        self._load_active_session_tokens(self._config)
        self._loaded_binding = self._binding(self._config)
        return self._config

    def _load_active_session_tokens(self, config: Config) -> None:
        """Overlay tokens from the named session shared with active proxies."""
        if not config.active_session_id:
            return
        session_path = self.sessions_dir / f"{config.active_session_id}.json"
        try:
            session = json.loads(session_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(session, dict):
            return
        if session.get("issuer") != (config.issuer or DEFAULT_ISSUER):
            return
        access_token = session.get("access_token")
        refresh_token = session.get("refresh_token")
        if isinstance(access_token, str) and access_token:
            config.access_token = access_token
        if isinstance(refresh_token, str) and refresh_token:
            config.refresh_token = refresh_token

    def save_config(self, config: Config) -> None:
        """Save configuration to file."""
        with self._transaction_lock():
            current_binding = self._binding(self._disk_config())
            if (
                self._loaded_binding is not None
                and self._loaded_binding != current_binding
            ):
                raise ValueError(
                    "The LightNow connection changed. Repeat the command in a fresh session."
                )
            self._save_config_unlocked(config)

    def _save_config_unlocked(self, config: Config) -> None:
        self._ensure_config_dir()

        config_data = config.model_dump()
        serialized = json.dumps(config_data, indent=2)
        fd: Optional[int] = None
        tmp_path: Optional[Path] = None

        try:
            fd, raw_tmp_path = tempfile.mkstemp(
                prefix=f".{self.config_file.name}.",
                suffix=".tmp",
                dir=self.config_dir,
                text=True,
            )
            tmp_path = Path(raw_tmp_path)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                fd = None
                f.write(serialized)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_file)
            self.config_file.chmod(0o600)
            self._fsync_config_dir()
            self._config = config
            self._loaded_binding = self._binding(config)
        except IOError as e:
            typer.echo(f"Error: Failed to save config: {e}", err=True)
            raise typer.Exit(1)
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    def _fsync_config_dir(self) -> None:
        """Best-effort fsync for the config directory after atomic replacement."""
        try:
            dir_fd = os.open(self.config_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def get_token(self) -> Optional[str]:
        """Get access token from config or environment."""
        config = self.load_config()
        return config.access_token

    @_serialized_write
    def set_token(
        self,
        token: str,
        refresh_token: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        *,
        update_active_session: bool = True,
        expected_binding: Optional[tuple[Any, ...]] = None,
    ) -> None:
        """Set access token, refresh token and optionally user info."""
        config = self.load_config()
        if expected_binding is not None and self._binding(config) != expected_binding:
            raise ValueError(
                "The LightNow connection changed during token refresh. Repeat the command."
            )
        config.access_token = token
        if refresh_token is not None:
            config.refresh_token = refresh_token
        config.user_info = user_info
        if update_active_session and config.active_session_id:
            session_path = self.sessions_dir / f"{config.active_session_id}.json"
            with FileLock(f"{session_path}.lock"):
                try:
                    session = json.loads(session_path.read_text())
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    session = None
                if isinstance(session, dict):
                    session["access_token"] = token
                    if refresh_token is not None:
                        session["refresh_token"] = refresh_token
                    self._atomic_write_json(session_path, session)
        elif not update_active_session:
            config.active_session_id = None
        self.save_config(config)

    @_serialized_write
    def persist_current_session(
        self, user_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Persist the active login under a stable full-target-and-subject identity."""
        config = self.load_config()
        identity = user_info or config.user_info or {}
        subject = identity.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValueError("The current LightNow session has no subject.")
        if not config.access_token:
            raise ValueError("The current LightNow session has no access token.")
        issuer = config.issuer or DEFAULT_ISSUER
        client_id = config.client_id or DEFAULT_CLIENT_ID
        session_id = self._session_id(self._target(config), subject)
        account_label = next(
            (
                str(identity[key])
                for key in ("name", "preferred_username", "email")
                if identity.get(key)
            ),
            subject,
        )
        session_path = self.sessions_dir / f"{session_id}.json"
        payload = {
            "version": 1,
            "session_id": session_id,
            "subject": subject,
            "account_label": account_label,
            "issuer": issuer,
            "client_id": client_id,
            "registry_api_url": config.registry_api_url,
            "admin_api_url": config.admin_api_url,
            "access_token": config.access_token,
            "refresh_token": config.refresh_token,
        }
        with FileLock(f"{session_path}.lock"):
            self._atomic_write_json(session_path, payload)
        config.active_session_id = session_id
        config.user_info = dict(identity)
        self.save_config(config)
        return {
            "session_id": session_id,
            "path": str(session_path),
            "issuer": issuer,
            "subject": subject,
            "account_label": account_label,
        }

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        """Write a private JSON file atomically without exposing credentials."""
        self._atomic_write_bytes(path, (json.dumps(payload, indent=2) + "\n").encode())

    def _atomic_write_bytes(self, path: Path, payload: bytes) -> None:
        """Replace private file contents, including exact rollback snapshots."""
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd: Optional[int] = None
        tmp_path: Optional[Path] = None
        try:
            fd, raw_tmp_path = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            tmp_path = Path(raw_tmp_path)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            path.chmod(0o600)
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    def commit_login(
        self,
        target: ConnectionTarget,
        token: str,
        refresh_token: Optional[str],
        user_info: Dict[str, Any],
    ) -> None:
        """Serialize fresh target/session snapshots through commit or rollback."""
        with self._transaction_lock():
            self._config = None
            self._loaded_binding = None
            self._refresh_for_write()
            self._commit_login_unlocked(target, token, refresh_token, user_info)

    def _commit_login_unlocked(
        self,
        target: ConnectionTarget,
        token: str,
        refresh_token: Optional[str],
        user_info: Dict[str, Any],
    ) -> None:
        """Commit a verified login, restoring prior bytes on persistence failure."""
        subject = user_info.get("sub")
        if not isinstance(subject, str) or not subject.strip() or not token:
            raise ValueError(
                "Verified login requires an access token and user subject."
            )
        previous = self.load_config()
        candidate = previous.model_copy(deep=True)
        previous_environment = self._target(previous).environment
        if (
            previous_environment != target.environment
            or self._subject(previous) != subject
        ):
            candidate.context_type = "personal"
            candidate.context_tenant = None
            candidate.context_label = None
        candidate.issuer = target.issuer
        candidate.client_id = target.client_id
        candidate.registry_api_url = target.registry_api_url
        candidate.admin_api_url = target.admin_api_url
        candidate.access_token = token
        candidate.refresh_token = refresh_token
        candidate.user_info = dict(user_info)
        session_id = self._session_id(target, subject)
        candidate.active_session_id = session_id
        session_path = self.sessions_dir / f"{session_id}.json"
        account_label = next(
            (
                str(user_info[key])
                for key in ("name", "preferred_username", "email")
                if user_info.get(key)
            ),
            subject,
        )
        payload = {
            "version": 1,
            "session_id": session_id,
            "subject": subject,
            "account_label": account_label,
            "issuer": target.issuer,
            "client_id": target.client_id,
            "registry_api_url": target.registry_api_url,
            "admin_api_url": target.admin_api_url,
            "access_token": token,
            "refresh_token": refresh_token,
        }
        self._ensure_config_dir()
        self.sessions_dir.mkdir(mode=0o700, exist_ok=True)
        with FileLock(f"{session_path}.lock"):
            previous_session = (
                session_path.read_bytes() if session_path.exists() else None
            )
            previous_config = (
                self.config_file.read_bytes() if self.config_file.exists() else None
            )
            try:
                self._atomic_write_json(session_path, payload)
                self.save_config(candidate)
            except BaseException:
                self._config = previous
                self._loaded_binding = self._binding(previous)
                if previous_session is None:
                    session_path.unlink(missing_ok=True)
                else:
                    self._atomic_write_bytes(session_path, previous_session)
                if previous_config is None:
                    self.config_file.unlink(missing_ok=True)
                else:
                    self._atomic_write_bytes(self.config_file, previous_config)
                raise

    @_serialized_write
    def clear_token(self) -> None:
        """Clear stored credentials for the active session and account context."""
        config = self.load_config()
        if config.active_session_id:
            session_path = self.sessions_dir / f"{config.active_session_id}.json"
            with FileLock(f"{session_path}.lock"):
                session_path.unlink(missing_ok=True)
        config.access_token = None
        config.refresh_token = None
        config.active_session_id = None
        config.user_info = None
        config.context_type = "personal"
        config.context_tenant = None
        config.context_label = None
        self.save_config(config)

    @_serialized_write
    def set_auth_config(
        self,
        issuer: str,
        client_id: str,
        registry_api_url: Optional[str] = None,
        admin_api_url: Optional[str] = None,
    ) -> None:
        """Set authentication configuration."""
        config = self.load_config()
        config.issuer = issuer
        config.client_id = client_id
        config.registry_api_url = registry_api_url or DEFAULT_REGISTRY_API_URL
        config.admin_api_url = admin_api_url or DEFAULT_ADMIN_API_URL
        self.save_config(config)

    @_serialized_write
    def set_personal_context(self) -> None:
        """Use the personal LightNow context by default."""
        config = self.load_config()
        config.context_type = "personal"
        config.context_tenant = None
        config.context_label = None
        self.save_config(config)

    @_serialized_write
    def set_tenant_context(self, tenant_id: str, label: str) -> None:
        """Use a tenant context by default."""
        if not tenant_id:
            raise ValueError("Tenant context requires a tenant id.")
        config = self.load_config()
        config.context_type = "tenant"
        config.context_tenant = tenant_id
        config.context_label = label
        self.save_config(config)

    def effective_tenant(self, explicit_tenant: Optional[str] = None) -> Optional[str]:
        """Return explicit tenant or the stored default tenant context."""
        if explicit_tenant:
            return explicit_tenant
        config = self.load_config()
        if config.context_type == "tenant":
            return config.context_tenant
        return None

    def context_display_name(self) -> str:
        """Return a human-readable current context label."""
        config = self.load_config()
        if config.context_type == "tenant":
            return config.context_label or config.context_tenant or "Organization"
        return "Personal"

    @_serialized_write
    def get_or_create_device_installation_id(self) -> str:
        """Return the stable, protected identifier for this CLI installation."""
        config = self.load_config()
        if config.device_installation_id:
            try:
                return str(uuid.UUID(config.device_installation_id))
            except ValueError:
                pass

        config.device_installation_id = str(uuid.uuid4())
        self.save_config(config)
        assert config.device_installation_id is not None
        return config.device_installation_id


# Global config manager instance
config_manager = ConfigManager()
