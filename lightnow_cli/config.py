"""Configuration and token management."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from filelock import FileLock
from pydantic import BaseModel, Field

DEFAULT_ISSUER = "https://auth.lightnow.ai/realms/lightnow"
DEFAULT_CLIENT_ID = "lightnow-cli"
DEFAULT_REGISTRY_API_URL = "https://registry-api.lightnow.ai/v0.1"
DEFAULT_ADMIN_API_URL = "https://admin-api.lightnow.ai/v0/portal"
LOCAL_ISSUER = "https://auth.lightnow.local/realms/lightnow-local"
LOCAL_REGISTRY_API_URL = "https://registry-api.lightnow.local/v0.1"
LOCAL_ADMIN_API_URL = "https://admin-api.lightnow.local/v0/portal"


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


class ConfigManager:
    """Manages CLI configuration and token storage."""

    def __init__(self) -> None:
        self.config_dir = Path.home() / ".lightnow"
        self.config_file = self.config_dir / "config.json"
        self._config: Optional[Config] = None

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

    def set_token(
        self,
        token: str,
        refresh_token: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        *,
        update_active_session: bool = True,
    ) -> None:
        """Set access token, refresh token and optionally user info."""
        config = self.load_config()
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

    def persist_current_session(
        self, user_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Persist the active login under a stable issuer-and-subject identity."""
        config = self.load_config()
        identity = user_info or config.user_info or {}
        subject = identity.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValueError("The current LightNow session has no subject.")
        if not config.access_token:
            raise ValueError("The current LightNow session has no access token.")
        issuer = config.issuer or DEFAULT_ISSUER
        client_id = config.client_id or DEFAULT_CLIENT_ID
        session_id = hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()[:24]
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
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd: Optional[int] = None
        tmp_path: Optional[Path] = None
        try:
            fd, raw_tmp_path = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
            )
            tmp_path = Path(raw_tmp_path)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            path.chmod(0o600)
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

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

    def set_personal_context(self) -> None:
        """Use the personal LightNow context by default."""
        config = self.load_config()
        config.context_type = "personal"
        config.context_tenant = None
        config.context_label = None
        self.save_config(config)

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


# Global config manager instance
config_manager = ConfigManager()
