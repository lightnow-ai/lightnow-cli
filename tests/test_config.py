"""Test configuration and token management."""

import json
import tempfile
from pathlib import Path

import pytest

from lightnow_cli.config import (
    DEFAULT_ADMIN_API_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_ISSUER,
    DEFAULT_REGISTRY_API_URL,
    Config,
    ConfigManager,
)


@pytest.fixture
def temp_config_dir():
    """Create a temporary config directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def config_manager(temp_config_dir, monkeypatch):
    """Create a config manager with temporary directory."""
    manager = ConfigManager()
    manager.config_dir = temp_config_dir / ".lightnow"
    manager.config_file = manager.config_dir / "config.json"
    manager._config = None  # Reset cached config

    return manager


def test_config_manager_init(config_manager):
    """Test config manager initialization."""
    assert config_manager.config_dir.name == ".lightnow"
    assert config_manager.config_file.name == "config.json"


def test_load_default_config(config_manager):
    """Test loading default configuration."""
    config = config_manager.load_config()

    assert isinstance(config, Config)
    assert config.access_token is None
    assert config.issuer == DEFAULT_ISSUER
    assert config.client_id == DEFAULT_CLIENT_ID
    assert config.registry_api_url == DEFAULT_REGISTRY_API_URL
    assert config.admin_api_url == DEFAULT_ADMIN_API_URL
    assert config.context_type == "personal"
    assert config.context_tenant is None


def test_save_and_load_config(config_manager):
    """Test saving and loading configuration."""
    # Create test config
    test_config = Config(
        access_token="test-token",
        issuer="https://example.com",
        client_id="test-client",
        user_info={"sub": "123", "email": "test@example.com"},
    )

    # Save config
    config_manager.save_config(test_config)

    # Verify file exists
    assert config_manager.config_file.exists()

    # Reset cached config and reload
    config_manager._config = None
    loaded_config = config_manager.load_config()

    assert loaded_config.access_token == "test-token"
    assert loaded_config.issuer == "https://example.com"
    assert loaded_config.client_id == "test-client"
    assert loaded_config.user_info == {"sub": "123", "email": "test@example.com"}


def test_token_methods(config_manager):
    """Test token getter/setter methods."""
    # Initially no token
    assert config_manager.get_token() is None

    # Set token
    config_manager.set_token("new-token", "refresh-token", {"sub": "456"})

    # Get token
    assert config_manager.get_token() == "new-token"

    # Verify user info was saved
    config = config_manager.load_config()
    assert config.refresh_token == "refresh-token"
    assert config.user_info == {"sub": "456"}

    # Clear token
    config_manager.clear_token()
    assert config_manager.get_token() is None
    assert config_manager.load_config().refresh_token is None


def test_set_token_clears_cached_user_info(config_manager):
    """Refreshing tokens clears stale cached identity claims."""
    config_manager.set_token(
        "old-token", "old-refresh-token", {"email": "old@example.com"}
    )

    config_manager.set_token("new-token", "new-refresh-token", None)

    config = config_manager.load_config()
    assert config.access_token == "new-token"
    assert config.refresh_token == "new-refresh-token"
    assert config.user_info is None


def test_auth_config_methods(config_manager):
    """Test authentication configuration methods."""
    config_manager.set_auth_config("https://auth.example.com", "my-client")

    config = config_manager.load_config()
    assert config.issuer == "https://auth.example.com"
    assert config.client_id == "my-client"
    assert config.registry_api_url == DEFAULT_REGISTRY_API_URL
    assert config.admin_api_url == DEFAULT_ADMIN_API_URL


def test_auth_config_accepts_api_overrides(config_manager):
    """Local auth can persist matching local API URLs."""
    config_manager.set_auth_config(
        "https://auth.example.com",
        "my-client",
        "https://registry-api.example.com/v0.1",
        "https://admin-api.example.com/v0/portal",
    )

    config = config_manager.load_config()
    assert config.issuer == "https://auth.example.com"
    assert config.client_id == "my-client"
    assert config.registry_api_url == "https://registry-api.example.com/v0.1"
    assert config.admin_api_url == "https://admin-api.example.com/v0/portal"


def test_context_methods(config_manager):
    """Stored context controls default tenant headers."""
    assert config_manager.effective_tenant() is None
    assert config_manager.context_display_name() == "Personal"

    config_manager.set_tenant_context("tenant-uuid", "Acme (acme)")

    assert config_manager.effective_tenant() == "tenant-uuid"
    assert config_manager.effective_tenant("explicit-tenant") == "explicit-tenant"
    assert config_manager.context_display_name() == "Acme (acme)"

    config_manager.set_personal_context()

    assert config_manager.effective_tenant() is None
    assert config_manager.context_display_name() == "Personal"


def test_logout_clears_context(config_manager):
    """Logging out removes account-scoped organization context."""
    config_manager.set_token("token", "refresh", None)
    config_manager.set_tenant_context("tenant-uuid", "Acme (acme)")

    config_manager.clear_token()

    config = config_manager.load_config()
    assert config.access_token is None
    assert config.refresh_token is None
    assert config.context_type == "personal"
    assert config.context_tenant is None


def test_config_file_permissions(config_manager):
    """Test that config file has correct permissions."""
    test_config = Config(access_token="secret-token")
    config_manager.save_config(test_config)

    # Check file permissions (should be readable/writable by owner only)
    file_mode = config_manager.config_file.stat().st_mode
    assert file_mode & 0o077 == 0  # No permissions for group/other


def test_config_directory_permissions_are_corrected(config_manager):
    """Existing config directories are tightened before saving token files."""
    config_manager.config_dir.mkdir(mode=0o755, parents=True)
    config_manager.config_dir.chmod(0o755)

    config_manager.save_config(Config(access_token="secret-token"))

    dir_mode = config_manager.config_dir.stat().st_mode
    assert dir_mode & 0o077 == 0


def test_save_config_uses_atomic_replacement(config_manager):
    """Saving config leaves no temporary file behind and preserves valid JSON."""
    config_manager.save_config(Config(access_token="first-token"))
    config_manager.save_config(Config(access_token="second-token"))

    assert json.loads(config_manager.config_file.read_text())["access_token"] == (
        "second-token"
    )
    assert list(config_manager.config_dir.glob("*.tmp")) == []


def test_invalid_config_file(config_manager):
    """Test handling of invalid config file."""
    # Create invalid JSON file
    config_manager._ensure_config_dir()
    with open(config_manager.config_file, "w") as f:
        f.write("invalid json content")

    # Should load default config and show warning
    config_manager._config = None
    config = config_manager.load_config()

    assert isinstance(config, Config)
    assert config.access_token is None
