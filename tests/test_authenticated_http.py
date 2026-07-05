"""Tests for authenticated HTTP helpers."""

from __future__ import annotations

import httpx

from lightnow_cli.authenticated_http import (
    authentication_error_from_response,
    response_error_message,
)


def response(payload: object, status_code: int = 400) -> httpx.Response:
    request = httpx.Request("GET", "https://registry-api.lightnow.local/v0.1/settings")
    return httpx.Response(status_code, request=request, json=payload)


def test_response_error_message_prefers_nested_error_message() -> None:
    """Structured API error messages are surfaced without dumping full payloads."""
    message = response_error_message(
        response({"error": {"message": "Session expired"}})
    )

    assert message == "Session expired"


def test_response_error_message_falls_back_to_detail_and_title() -> None:
    """Problem-details responses remain readable in CLI errors."""
    assert (
        response_error_message(response({"detail": "Policy denied"})) == "Policy denied"
    )
    assert response_error_message(response({"title": "Unauthorized"})) == "Unauthorized"


def test_authentication_error_from_response_uses_safe_message() -> None:
    """Authentication errors carry the API reason but not raw response content."""
    error = authentication_error_from_response(
        response({"error": {"code": "invalid_token"}}, status_code=401)
    )

    assert str(error) == "Authentication failed: invalid_token"
