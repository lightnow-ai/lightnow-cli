"""Credential-free connection targets for explicitly selected environments."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ConnectionTarget:
    """The complete identity and API boundary selected for a login."""

    issuer: str
    client_id: str
    registry_api_url: str
    admin_api_url: str

    @property
    def environment(self) -> tuple[str, str, str, str]:
        """Compare complete targets without insignificant trailing slashes."""
        return (
            self.issuer.rstrip("/"),
            self.client_id,
            self.registry_api_url.rstrip("/"),
            self.admin_api_url.rstrip("/"),
        )


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Connection target contains duplicate fields.")
        result[key] = value
    return result


def validate_https_url(value: Any) -> str:
    # Never include input values in errors: rejected URLs may contain credentials.
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise ValueError("Connection target URLs must be absolute HTTPS URLs.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Connection target URLs require HTTPS without credentials, query or fragment."
        ) from None
    return value.rstrip("/")


def parse_target(payload: Any) -> ConnectionTarget:
    """Reject partial or credential-bearing targets before authentication starts."""
    fields = {"schemaVersion", "issuer", "clientId", "registryApiUrl", "adminApiUrl"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError(
            "Connection target requires exactly the documented five fields."
        )
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1:
        raise ValueError("Unsupported connection target schemaVersion; expected 1.")
    client_id = payload["clientId"]
    if (
        not isinstance(client_id, str)
        or not client_id
        or any(character.isspace() or ord(character) < 32 for character in client_id)
    ):
        raise ValueError("Connection target clientId must be a nonempty identifier.")
    return ConnectionTarget(
        issuer=validate_https_url(payload["issuer"]),
        client_id=client_id,
        registry_api_url=validate_https_url(payload["registryApiUrl"]),
        admin_api_url=validate_https_url(payload["adminApiUrl"]),
    )


def load_target(path: Path) -> ConnectionTarget:
    """Read strict JSON without echoing rejected data or credentials."""
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Cannot read a valid JSON connection target.") from None
    return parse_target(payload)
