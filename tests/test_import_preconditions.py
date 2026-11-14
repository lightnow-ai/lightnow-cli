"""Import writes retain the API's optimistic concurrency and permission checks."""

import json
from unittest.mock import patch

import httpx
import pytest

from lightnow_cli.commands.integrations import import_profile_config

API = "https://registry.example.test/v0.1"
CONTENT = '[mcp_servers.example]\ncommand = "example-mcp"\n'
ETAG = '"owner-snapshot-1"'


def import_config(
    *, dry_run=False, tenant="organization-id", profile="team", replace=False
):
    return import_profile_config(
        api_url=API,
        token="test-access-token",
        tenant=tenant,
        source="codex",
        content=CONTENT,
        profile=profile,
        dry_run=dry_run,
        replace=replace,
    )


@pytest.mark.parametrize(
    "tenant,profile", [(None, "default"), ("organization-id", "team")]
)
@pytest.mark.parametrize("replace", [False, True])
def test_apply_reads_owner_snapshot_and_preserves_import_scope(
    tenant, profile, replace
):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-access-token"
        assert request.headers.get("X-Tenant") == tenant
        if request.method == "GET":
            assert request.url == f"{API}/integrations/profiles"
            assert not request.content
            assert "If-Match" not in request.headers
            return httpx.Response(200, headers={"ETag": ETAG}, json={"profiles": []})
        assert request.method == "POST"
        assert request.url == f"{API}/integrations/import?dry_run=false"
        assert request.headers["If-Match"] == ETAG
        assert json.loads(request.content) == {
            "source": "codex",
            "content": CONTENT,
            "profile": {"name": profile},
            "replace": replace,
        }
        return httpx.Response(200, json={"dry_run": False})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
    ):
        assert import_config(tenant=tenant, profile=profile, replace=replace) == {
            "dry_run": False
        }
    assert [request.method for request in requests] == ["GET", "POST"]


def test_preview_does_not_fetch_or_send_a_write_precondition():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.params["dry_run"] == "true"
        assert "If-Match" not in request.headers
        return httpx.Response(200, json={"dry_run": True})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
    ):
        assert import_config(dry_run=True) == {"dry_run": True}
    assert len(requests) == 1


@pytest.mark.parametrize(
    "etag", [None, "", "*", 'W/"weak"', "unquoted", '"one", "two"']
)
def test_missing_or_invalid_snapshot_never_sends_import(etag):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(
            200,
            headers={"ETag": etag} if etag is not None else {},
            json={"profiles": []},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
        pytest.raises(ValueError, match="current profile version"),
    ):
        import_config()
    assert len(requests) == 1


@pytest.mark.parametrize("status", [302, 403, 503])
def test_failed_snapshot_read_never_sends_import(status):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(
            status,
            headers={"ETag": ETAG},
            json={"error": {"message": "Cannot read profiles"}},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
        pytest.raises(ValueError, match=f"HTTP {status}"),
    ):
        import_config()
    assert len(requests) == 1


def test_conflict_stops_without_refetch_or_reapply():
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": ETAG}, json={"profiles": []})
        assert request.headers["If-Match"] == ETAG
        return httpx.Response(412, json={"message": "Profile changed"})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
        pytest.raises(ValueError, match="HTTP 412"),
    ):
        import_config()
    assert [request.method for request in requests] == ["GET", "POST"]


@pytest.mark.parametrize("expired_method", ["GET", "POST"])
def test_existing_session_refresh_preserves_scope_and_write_snapshot(expired_method):
    requests = []
    expired = False

    def handle(request):
        nonlocal expired
        requests.append(request)
        assert request.headers["X-Tenant"] == "organization-id"
        if request.method == "POST":
            assert request.headers["If-Match"] == ETAG
        if request.method == expired_method and not expired:
            expired = True
            return httpx.Response(401, json={"error": "token_expired"})
        if expired:
            assert request.headers["Authorization"] == "Bearer refreshed-test-token"
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": ETAG}, json={"profiles": []})
        return httpx.Response(200, json={"dry_run": False})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=client.request
        ),
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="refreshed-test-token",
        ) as refresh,
    ):
        assert import_config() == {"dry_run": False}
    assert refresh.call_count == 1
    assert [request.method for request in requests] == (
        ["GET", "GET", "POST"] if expired_method == "GET" else ["GET", "POST", "POST"]
    )
