"""MCP Registry API client."""

from typing import Any, Dict, Optional, cast
from urllib.parse import quote

import httpx
from rich.console import Console

from . import __version__
from .authenticated_http import (
    async_request_with_refresh,
    authentication_error_from_response,
)
from .commands.auth import (
    AccessTokenExpired,
    AuthError,
)
from .config import config_manager

console = Console()


def validation_error_from_response(response: httpx.Response) -> ValueError:
    """Build a validation error while preserving API details when present."""
    try:
        payload = response.json()
    except ValueError:
        return ValueError("Validation failed")
    if not isinstance(payload, dict):
        return ValueError("Validation failed")

    detail = payload.get("detail")
    errors = payload.get("errors")
    messages: list[str] = []
    if isinstance(errors, list):
        for error in errors[:3]:
            if not isinstance(error, dict):
                continue
            location = error.get("location")
            message = error.get("message")
            if isinstance(location, str) and isinstance(message, str):
                messages.append(f"{location}: {message}")
            elif isinstance(message, str):
                messages.append(message)

    if isinstance(detail, str) and detail:
        if messages:
            return ValueError(f"Validation failed: {detail} ({'; '.join(messages)})")
        return ValueError(f"Validation failed: {detail}")

    if messages:
        return ValueError(f"Validation failed: {'; '.join(messages)}")
    return ValueError("Validation failed")


class MCPRegistryClient:
    """Client for interacting with MCP Registry API."""

    def __init__(
        self, base_url: Optional[str] = None, token: Optional[str] = None
    ) -> None:
        config = config_manager.load_config()
        self.base_url = base_url or config.registry_api_url
        self.token = token
        self._refresh_on_unauthorized = token is None

        if not self.base_url:
            raise ValueError("Registry API URL not configured")

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for API requests."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"lightnow-cli/{__version__}",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        return headers

    async def publish_server(
        self,
        server_json: Dict[str, Any],
        docs_content: Optional[str] = None,
        spec_content: Optional[str] = None,
        tenant: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Publish an MCP server to the registry."""
        payload: Dict[str, Any] = dict(server_json)

        if docs_content:
            payload["_meta"] = {
                **cast(Dict[str, Any], payload.get("_meta", {})),
                "ai.lightnow/docs": docs_content,
            }

        if spec_content:
            payload["_meta"] = {
                **cast(Dict[str, Any], payload.get("_meta", {})),
                "ai.lightnow/openapi": spec_content,
            }

        try:
            response = await async_request_with_refresh(
                "POST",
                f"{self.base_url}/publish",
                json=payload,
                headers=self._get_headers(),
                token=self.token,
                tenant=tenant,
                allow_refresh=self._refresh_on_unauthorized,
                timeout=30.0,
            )

            if response.status_code == 401:
                raise authentication_error_from_response(response)
            elif response.status_code == 422:
                raise validation_error_from_response(response)

            response.raise_for_status()
            return cast(Dict[str, Any], response.json())

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise authentication_error_from_response(e.response)
            elif e.response.status_code == 422:
                raise validation_error_from_response(e.response)
            else:
                raise ValueError(f"API request failed: {e.response.status_code}")
        except httpx.RequestError as e:
            raise ValueError(f"Network error: {e}")
        except (AccessTokenExpired, AuthError) as e:
            raise ValueError(str(e)) from e

    async def list_servers(
        self,
        search: Optional[str] = None,
        favorites: Optional[str] = None,
        sort: Optional[str] = None,
        tenant: Optional[str] = None,
        limit: int = 10,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query MCP servers from the registry."""
        params: Dict[str, Any] = {"limit": limit}

        if search:
            params["search"] = search
        if favorites:
            params["favorites"] = favorites
        if sort:
            params["sort"] = sort
        if tenant:
            params["tenant"] = tenant
        if cursor:
            params["cursor"] = cursor

        try:
            response = await async_request_with_refresh(
                "GET",
                f"{self.base_url}/servers",
                params=params,
                headers=self._get_headers(),
                token=self.token,
                allow_refresh=self._refresh_on_unauthorized,
                timeout=30.0,
            )

            if response.status_code == 401:
                raise authentication_error_from_response(response)

            response.raise_for_status()
            return cast(Dict[str, Any], response.json())

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise authentication_error_from_response(e.response)
            else:
                raise ValueError(f"API request failed: {e.response.status_code}")
        except httpx.RequestError as e:
            raise ValueError(f"Network error: {e}")
        except (AccessTokenExpired, AuthError) as e:
            raise ValueError(str(e)) from e

    async def get_server_info(
        self,
        server_id: str,
        version: Optional[str] = None,
        tenant: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get detailed information about a specific server."""
        params: Dict[str, Any] = {}
        if tenant:
            params["tenant"] = tenant

        encoded_server_id = quote(server_id, safe="")
        if version:
            encoded_version = quote(version, safe="")
            url = f"{self.base_url}/servers/{encoded_server_id}/versions/{encoded_version}"
        else:
            url = f"{self.base_url}/servers/{encoded_server_id}/versions"
            params["limit"] = 1

        try:
            response = await async_request_with_refresh(
                "GET",
                url,
                params=params,
                headers=self._get_headers(),
                token=self.token,
                allow_refresh=self._refresh_on_unauthorized,
                timeout=30.0,
            )

            if response.status_code == 401:
                raise authentication_error_from_response(response)
            elif response.status_code == 404:
                raise ValueError(f"Server '{server_id}' not found")

            response.raise_for_status()
            payload = cast(Dict[str, Any], response.json())
            if version:
                return payload

            servers = payload.get("servers")
            if not isinstance(servers, list) or not servers:
                raise ValueError(f"Server '{server_id}' not found")
            first = servers[0]
            if not isinstance(first, dict):
                raise ValueError("Registry API returned an invalid server version.")
            return first

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise authentication_error_from_response(e.response)
            elif e.response.status_code == 404:
                raise ValueError(f"Server '{server_id}' not found")
            else:
                raise ValueError(f"API request failed: {e.response.status_code}")
        except httpx.RequestError as e:
            raise ValueError(f"Network error: {e}")
        except (AccessTokenExpired, AuthError) as e:
            raise ValueError(str(e)) from e


# Global client instance
def get_client() -> MCPRegistryClient:
    """Get configured MCP Registry client."""
    return MCPRegistryClient()
