"""Authenticated HTTP helpers for LightNow API calls."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .commands.auth import (
    async_refresh_current_session,
    async_require_access_token,
    refresh_current_session,
    require_access_token,
)


def response_error_message(response: httpx.Response) -> str | None:
    """Extract a safe, structured API error message when one is available."""
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()

    if isinstance(error, str) and error.strip():
        return error.strip()

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()

    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    return None


def authentication_error_from_response(response: httpx.Response) -> ValueError:
    """Build an explicit auth/API error for a post-refresh 401 response."""
    message = response_error_message(response)
    if message:
        return ValueError(f"Authentication failed: {message}")
    return ValueError("Authentication failed: Registry API rejected the session.")


def authenticated_headers(
    token: str,
    *,
    tenant: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Build API headers with bearer auth and optional tenant context."""
    merged = dict(headers or {})
    merged["Authorization"] = f"Bearer {token}"
    if tenant:
        merged["X-Tenant"] = tenant
    return merged


def request_with_refresh(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    tenant: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    allow_refresh: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Execute one authenticated request and retry once after a 401 refresh."""
    current_token = token or require_access_token()
    response = httpx.request(
        method,
        url,
        headers=authenticated_headers(current_token, tenant=tenant, headers=headers),
        **kwargs,
    )
    if response.status_code != 401 or not allow_refresh:
        return response

    current_token = refresh_current_session()
    return httpx.request(
        method,
        url,
        headers=authenticated_headers(current_token, tenant=tenant, headers=headers),
        **kwargs,
    )


async def async_request_with_refresh(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    tenant: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    allow_refresh: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Execute one async authenticated request and retry once after 401 refresh."""
    current_token = token or await async_require_access_token()
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=authenticated_headers(
                current_token, tenant=tenant, headers=headers
            ),
            **kwargs,
        )
        if response.status_code != 401 or not allow_refresh:
            return response

        current_token = await async_refresh_current_session()
        return await client.request(
            method,
            url,
            headers=authenticated_headers(
                current_token, tenant=tenant, headers=headers
            ),
            **kwargs,
        )
