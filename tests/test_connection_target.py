"""Complete targets and rollback for verified, isolated device logins."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock

import pytest
import yaml
from typer.testing import CliRunner

from lightnow_cli import updates
from lightnow_cli.commands import auth, context, integrations
from lightnow_cli.config import Config, ConfigManager
from lightnow_cli.main import app
from lightnow_cli.target import load_target, parse_target

TARGET = {
    "schemaVersion": 1,
    "issuer": "https://auth.example.test/realms/example",
    "clientId": "lightnow-cli",
    "registryApiUrl": "https://registry.example.test/v0.1",
    "adminApiUrl": "https://admin.example.test/v0/portal",
}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    manager = ConfigManager()
    manager.config_dir = tmp_path / ".lightnow"
    manager.config_file = manager.config_dir / "config.json"
    monkeypatch.setattr(auth, "config_manager", manager)
    monkeypatch.setattr(integrations, "config_manager", manager)
    monkeypatch.setattr(context, "config_manager", manager)
    monkeypatch.setenv("LIGHTNOW_NO_UPDATE_CHECK", "1")
    return manager


@pytest.fixture
def target_file(tmp_path):
    path = tmp_path / "target.json"
    path.write_text(json.dumps(TARGET))
    return path


def seed(manager, target=None, subject="existing-user"):
    selected = parse_target(target or TARGET)
    manager.commit_login(selected, "old-access", "old-refresh", {"sub": subject})
    manager.set_tenant_context("old-tenant", "Existing organization")
    return {path.name: path.read_bytes() for path in manager.config_dir.rglob("*.json")}


def snapshot(manager):
    return {path.name: path.read_bytes() for path in manager.config_dir.rglob("*.json")}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {**TARGET, "schemaVersion": 2},
        {**TARGET, "schemaVersion": True},
        {**TARGET, "schemaVersion": "1"},
        {**TARGET, "clientId": ""},
        {**TARGET, "clientId": "bad\nclient"},
        {**TARGET, "clientSecret": "do-not-echo"},
        {key: value for key, value in TARGET.items() if key != "adminApiUrl"},
    ],
)
def test_invalid_shape_is_rejected_before_authentication(
    manager, target_file, monkeypatch, payload
):
    before = seed(manager)
    target_file.write_text(json.dumps(payload))
    device = AsyncMock()
    monkeypatch.setattr(auth, "device_code_flow", device)
    result = CliRunner().invoke(app, ["login", "--target", str(target_file)])
    assert result.exit_code == 1
    assert "do-not-echo" not in result.stdout
    device.assert_not_called()
    assert snapshot(manager) == before


@pytest.mark.parametrize("field", ["issuer", "registryApiUrl", "adminApiUrl"])
@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "https://name:do-not-echo@example.test",
        "https://example.test?token=do-not-echo",
        "https://example.test#fragment",
        "https://example.test?",
        "https://example.test#",
        "https:///missing",
        "https://example.test:99999",
        "https://example.test:bad",
        "https://example.test:0",
        "https://example.test/\n",
        "https://example.test\\@other.test",
        None,
        7,
    ],
)
def test_unsafe_urls_are_rejected_without_secret_echo(field, url):
    with pytest.raises(ValueError) as error:
        parse_target({**TARGET, field: url})
    assert "do-not-echo" not in str(error.value)


def test_unreadable_malformed_and_duplicate_json(tmp_path):
    path = tmp_path / "target.json"
    for content in [None, "{invalid", '{"schemaVersion":1,"schemaVersion":1}', b"\xff"]:
        if content is not None:
            path.write_bytes(
                content if isinstance(content, bytes) else content.encode()
            )
        with pytest.raises(ValueError):
            load_target(path)


@pytest.mark.parametrize(
    "flags", [["--local"], ["--issuer", TARGET["issuer"]], ["--client-id", "other"]]
)
def test_target_conflicts_do_not_authenticate_or_write(
    manager, target_file, monkeypatch, flags
):
    before = seed(manager)
    device = AsyncMock()
    monkeypatch.setattr(auth, "device_code_flow", device)
    result = CliRunner().invoke(app, ["login", "--target", str(target_file), *flags])
    assert result.exit_code == 1
    device.assert_not_called()
    assert snapshot(manager) == before


def test_custom_legacy_issuer_requires_complete_target(manager, monkeypatch):
    before = seed(manager)
    device = AsyncMock()
    monkeypatch.setattr(auth, "device_code_flow", device)
    result = CliRunner().invoke(app, ["login", "--issuer", TARGET["issuer"]])
    assert result.exit_code == 1
    assert "requires --target" in result.stdout
    device.assert_not_called()
    assert snapshot(manager) == before


@pytest.mark.parametrize("failure", ["device", "userinfo", "subject", "token"])
def test_authentication_failure_preserves_all_old_bytes_and_cache(
    manager, target_file, monkeypatch, failure
):
    before = seed(manager)
    cached = manager.load_config().model_dump()
    device = AsyncMock(return_value={"access_token": "new-access"})
    userinfo = AsyncMock(return_value={"sub": "new-user"})
    if failure == "device":
        device.side_effect = auth.AuthError("Device authorization rejected")
    elif failure == "userinfo":
        userinfo.side_effect = auth.AuthError("Userinfo verification rejected")
    elif failure == "subject":
        userinfo.return_value = {"sub": " "}
    else:
        device.return_value = {"refresh_token": "new-refresh"}
    monkeypatch.setattr(auth, "device_code_flow", device)
    monkeypatch.setattr(auth, "fetch_user_info", userinfo)
    result = CliRunner().invoke(app, ["login", "--target", str(target_file)])
    assert result.exit_code == 1
    assert snapshot(manager) == before
    assert manager.load_config().model_dump() == cached


@pytest.mark.parametrize(
    "change", [None, "issuer", "clientId", "registryApiUrl", "adminApiUrl", "subject"]
)
def test_successful_login_selects_exact_target_and_resets_only_changed_context(
    manager, target_file, monkeypatch, change
):
    before = seed(manager)
    selected = dict(TARGET)
    subject = "existing-user"
    if change == "subject":
        subject = "other-user"
    elif change == "clientId":
        selected[change] = "other-client"
    elif change:
        selected[change] += "/other"
    target_file.write_text(json.dumps(selected))
    device = AsyncMock(return_value={"access_token": "new-access"})
    userinfo = AsyncMock(return_value={"sub": subject})
    monkeypatch.setattr(auth, "device_code_flow", device)
    monkeypatch.setattr(auth, "fetch_user_info", userinfo)
    result = CliRunner().invoke(app, ["login", "--target", str(target_file)])
    assert result.exit_code == 0, result.stdout
    device.assert_awaited_once_with(selected["issuer"], selected["clientId"])
    userinfo.assert_awaited_once_with(selected["issuer"], "new-access")
    config = manager.load_config()
    assert (
        config.issuer,
        config.client_id,
        config.registry_api_url,
        config.admin_api_url,
    ) == parse_target(selected).environment
    assert (
        config.refresh_token is None
    )  # Never retain an earlier account's refresh token.
    assert config.context_type == ("personal" if change else "tenant")
    assert config.context_tenant == (None if change else "old-tenant")
    session_path = manager.sessions_dir / f"{config.active_session_id}.json"
    session = json.loads(session_path.read_text())
    assert session["subject"] == subject and session["issuer"] == selected["issuer"]
    assert session["client_id"] == selected["clientId"]
    assert config.access_token == session["access_token"] == "new-access"
    assert session_path.stat().st_mode & 0o777 == 0o600
    assert manager.config_file.stat().st_mode & 0o777 == 0o600
    persisted = manager.persist_current_session({"sub": subject})
    assert persisted["session_id"] == config.active_session_id
    if change:
        for filename, original in before.items():
            if filename != "config.json":
                assert (manager.sessions_dir / filename).read_bytes() == original


@pytest.mark.parametrize("same_session", [False, True])
@pytest.mark.parametrize("after_replace", [False, True])
def test_persistence_failure_restores_config_session_and_inprocess_cache(
    manager, monkeypatch, same_session, after_replace
):
    before = seed(manager)
    previous = manager.load_config().model_dump()
    save = manager.save_config

    def fail(config: Config):
        if after_replace:
            save(config)
        raise OSError("Synthetic persistence failure")

    monkeypatch.setattr(manager, "save_config", fail)
    with pytest.raises(OSError):
        manager.commit_login(
            parse_target(TARGET),
            "new-access",
            None,
            {"sub": "existing-user" if same_session else "other-user"},
        )
    assert snapshot(manager) == before
    assert manager.load_config().model_dump() == previous
    # A failed commit leaves no held lock or temporary token file behind.
    monkeypatch.setattr(manager, "save_config", save)
    manager.commit_login(
        parse_target(TARGET), "retry-access", None, {"sub": "existing-user"}
    )
    assert manager.get_token() == "retry-access"
    assert not list(manager.config_dir.rglob("*.tmp"))


def test_selected_target_flows_into_named_local_proxy_connection(manager):
    seed(manager)
    config = manager.load_config()
    session = json.loads(
        (manager.sessions_dir / f"{config.active_session_id}.json").read_text()
    )
    binding = {
        "path": str(manager.sessions_dir / f"{config.active_session_id}.json"),
        "issuer": session["issuer"],
        "subject": session["subject"],
    }
    proxy = yaml.safe_load(
        integrations.build_local_proxy_config(
            local_proxy_url="http://127.0.0.1:8080/mcp",
            local_proxy_transport="stdio",
            profile="default",
            client="codex",
            registry_api_url=config.registry_api_url,
            tenant=manager.effective_tenant(),
            session_binding=binding,
        )
    )
    assert proxy["registry_api"]["base_url"] == TARGET["registryApiUrl"]
    assert proxy["registry_api"]["cli_tenant_id"] == "old-tenant"
    assert proxy["registry_api"]["expected_issuer"] == TARGET["issuer"]
    assert proxy["registry_api"]["expected_subject"] == "existing-user"
    assert "old-access" not in json.dumps(proxy)


def test_target_login_help_is_explicit():
    result = CliRunner().invoke(app, ["login", "--help"])
    assert result.exit_code == 0 and "--target" in result.stdout


def test_login_never_starts_independent_update_refresh(manager, monkeypatch, tmp_path):
    refresh = AsyncMock()
    monkeypatch.setattr(updates, "should_check_automatically", lambda: True)
    monkeypatch.setattr(updates, "start_background_refresh", refresh)
    result = CliRunner().invoke(
        app, ["login", "--target", str(tmp_path / "missing.json")]
    )
    assert result.exit_code == 1
    refresh.assert_not_called()
    assert not manager.config_dir.exists()


def test_target_org_selection_and_export_use_selected_apis(manager, monkeypatch):
    seed(manager)
    captured = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        if url.endswith("/tenants"):
            return Response([{"id": "old-tenant", "subdomain": "example"}])
        return Response({"export": {"content": "managed-export"}})

    monkeypatch.setattr(context, "require_access_token", lambda: "old-access")
    monkeypatch.setattr(context, "request_with_refresh", request)
    monkeypatch.setattr(integrations, "request_with_refresh", request)
    assert context.fetch_tenants()[0]["id"] == "old-tenant"
    config = manager.load_config()
    exported = integrations.fetch_export(
        api_url=config.registry_api_url,
        token="old-access",
        tenant=manager.effective_tenant(),
        profile="default",
        client="codex",
        export_format="toml",
        secret_mode="placeholder",
    )
    assert exported == "managed-export"
    assert captured[0][1] == TARGET["adminApiUrl"] + "/tenants"
    assert (
        captured[1][1]
        == TARGET["registryApiUrl"] + "/integrations/profiles/default/export"
    )
    assert captured[1][2]["tenant"] == "old-tenant"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["device", "refresh", "userinfo"])
@pytest.mark.parametrize(
    "invalid",
    ["issuer", "device_authorization_endpoint", "token_endpoint", "userinfo_endpoint"],
)
async def test_untrusted_discovery_cannot_receive_device_codes_or_tokens(
    monkeypatch, operation, invalid
):
    discovery = {
        "issuer": TARGET["issuer"],
        "device_authorization_endpoint": "https://auth.example.test/device",
        "token_endpoint": "https://auth.example.test/token",
        "userinfo_endpoint": "https://auth.example.test/userinfo",
    }
    discovery[invalid] = "http://do-not-echo:credential@example.test/endpoint"
    calls = []

    class DiscoveryResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return discovery

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            assert not kwargs.get(
                "headers"
            ), "No Bearer token may leave discovery validation"
            calls.append(url)
            return DiscoveryResponse()

        async def post(self, *args, **kwargs):
            pytest.fail(
                "No device/refresh credential request may precede valid discovery"
            )

    monkeypatch.setattr(auth.httpx, "AsyncClient", Client)
    with pytest.raises(auth.AuthError) as error:
        if operation == "device":
            await auth.device_code_flow(TARGET["issuer"], TARGET["clientId"])
        elif operation == "refresh":
            await auth.refresh_access_token(
                TARGET["issuer"], TARGET["clientId"], "synthetic-refresh"
            )
        else:
            await auth.fetch_user_info(TARGET["issuer"], "synthetic-access")
    assert calls == [TARGET["issuer"] + "/.well-known/openid-configuration"]
    assert "do-not-echo" not in str(error.value)
    assert "credential@" not in str(error.value)


def shared_manager(manager):
    other = ConfigManager()
    other.config_dir = manager.config_dir
    other.config_file = manager.config_file
    return other


def test_failed_parallel_login_cannot_roll_back_another_successful_login(
    manager, monkeypatch
):
    seed(manager)
    other = shared_manager(manager)
    entered_save, release_failure, second_started = Event(), Event(), Event()

    def fail(config):
        entered_save.set()
        assert release_failure.wait(5)
        raise OSError("Synthetic first-login failure")

    def second():
        second_started.set()
        other.commit_login(
            parse_target({**TARGET, "clientId": "other-client"}),
            "second-access",
            None,
            {"sub": "second-user"},
        )

    monkeypatch.setattr(manager, "save_config", fail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            manager.commit_login,
            parse_target(TARGET),
            "failed-access",
            None,
            {"sub": "first-user"},
        )
        assert entered_save.wait(5)
        successful = pool.submit(second)
        try:
            assert second_started.wait(5)
            assert (
                not successful.done()
            )  # All sessions share the config transaction lock.
        finally:
            release_failure.set()
        with pytest.raises(OSError):
            first.result(timeout=5)
        successful.result(timeout=5)
    current = shared_manager(manager).load_config()
    assert (
        current.client_id == "other-client" and current.access_token == "second-access"
    )
    assert current.user_info == {"sub": "second-user"}
    assert not (
        manager.sessions_dir
        / (manager._session_id(parse_target(TARGET), "first-user") + ".json")
    ).exists()


@pytest.mark.parametrize("operation", ["token", "context", "persist", "save"])
def test_old_cached_manager_cannot_write_after_another_target_login(manager, operation):
    seed(manager)
    old_config = manager.load_config().model_copy(deep=True)
    other = shared_manager(manager)
    other.commit_login(
        parse_target({**TARGET, "adminApiUrl": TARGET["adminApiUrl"] + "/other"}),
        "other-access",
        None,
        {"sub": "other-user"},
    )
    before = snapshot(other)
    with pytest.raises(ValueError, match="connection changed"):
        if operation == "token":
            manager.set_token("stale-refresh")
        elif operation == "context":
            manager.set_tenant_context("stale-tenant", "Stale")
        elif operation == "persist":
            manager.persist_current_session({"sub": "existing-user"})
        else:
            manager.save_config(old_config)
    assert snapshot(other) == before


def test_refresh_response_cannot_cross_a_same_process_target_switch(manager):
    seed(manager)
    prior = manager.load_config().model_copy(deep=True)
    manager.commit_login(
        parse_target({**TARGET, "clientId": "other-client"}),
        "other-access",
        None,
        {"sub": "other-user"},
    )
    before = snapshot(manager)
    with pytest.raises(ValueError, match="connection changed"):
        auth.persist_refreshed_token(prior, {"access_token": "old-target-response"})
    assert snapshot(manager) == before


def test_same_account_login_preserves_context_after_refresh_clears_cached_userinfo(
    manager,
):
    seed(manager)
    manager.set_token("refreshed-access", "refreshed-refresh", None)
    manager.commit_login(
        parse_target(TARGET), "login-access", None, {"sub": "existing-user"}
    )
    assert manager.effective_tenant() == "old-tenant"
