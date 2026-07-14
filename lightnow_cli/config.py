"""Configuration and token management."""

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import typer
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

    def _ensure_config_dir(self) -> None:
        """Ensure config directory exists."""
        self.config_dir.mkdir(mode=0o700, exist_ok=True)
        self.config_dir.chmod(0o700)

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
        return self._config

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
    ) -> None:
        """Set access token, refresh token and optionally user info."""
        config = self.load_config()
        config.access_token = token
        if refresh_token is not None:
            config.refresh_token = refresh_token
        config.user_info = user_info
        self.save_config(config)

    def clear_token(self) -> None:
        """Clear stored access token and account-scoped context."""
        config = self.load_config()
        config.access_token = None
        config.refresh_token = None
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
