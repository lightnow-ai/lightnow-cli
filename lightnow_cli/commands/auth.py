"""Authentication commands."""

import asyncio
import base64
import hashlib
import secrets
import time
import webbrowser
from typing import Any, Dict, Optional, cast

import httpx
import jwt
import typer
from rich.console import Console
from typing_extensions import Annotated

from ..config import (
    DEFAULT_CLIENT_ID,
    DEFAULT_ISSUER,
    LOCAL_ADMIN_API_URL,
    LOCAL_ISSUER,
    LOCAL_REGISTRY_API_URL,
    config_manager,
)

console = Console()
app = typer.Typer(help="Authentication commands")

AUTH_HTTP_TIMEOUT = 30.0
ACCESS_TOKEN_EXPIRED_MESSAGE = (
    "Access token has expired. Please run 'lightnow login' again."
)
NOT_AUTHENTICATED_MESSAGE = "Not authenticated. Run 'lightnow login' first."


class AuthError(Exception):
    """Authentication error."""

    pass


class AccessTokenExpired(AuthError):
    """The stored access token is expired and cannot be refreshed."""


def describe_http_error(error: httpx.HTTPError) -> str:
    """Return a useful message for httpx errors that may stringify empty."""
    message = str(error).strip()
    if message:
        return message
    return error.__class__.__name__


def open_authorization_url(verification_uri: str) -> bool:
    """Open the device authorization URL in the user's browser."""
    return webbrowser.open(verification_uri, new=2, autoraise=True)


def generate_pkce_verifier() -> str:
    """Generate a PKCE verifier suitable for public-device clients."""
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge(verifier: str) -> str:
    """Return the S256 PKCE challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def discover_oidc_endpoints(issuer: str) -> Dict[str, str]:
    """Discover the OIDC endpoints required by the CLI."""
    async with httpx.AsyncClient(timeout=AUTH_HTTP_TIMEOUT) as client:
        try:
            discovery_response = await client.get(
                f"{issuer}/.well-known/openid-configuration"
            )
            discovery_response.raise_for_status()
            discovery = discovery_response.json()
            if not isinstance(discovery, dict):
                raise AuthError("OIDC discovery response is not a JSON object")
        except httpx.HTTPError as e:
            raise AuthError(
                f"Failed to discover OIDC endpoints: {describe_http_error(e)}"
            ) from e

    device_authorization_endpoint = discovery.get("device_authorization_endpoint")
    token_endpoint = discovery.get("token_endpoint")
    userinfo_endpoint = discovery.get("userinfo_endpoint")

    if not device_authorization_endpoint or not token_endpoint:
        raise AuthError("OIDC provider does not support device code flow")

    endpoints = {
        "device_authorization_endpoint": cast(str, device_authorization_endpoint),
        "token_endpoint": cast(str, token_endpoint),
    }
    if isinstance(userinfo_endpoint, str) and userinfo_endpoint:
        endpoints["userinfo_endpoint"] = userinfo_endpoint
    return endpoints


async def device_code_flow(issuer: str, client_id: str) -> Dict[str, Any]:
    """Perform OIDC Device Code Flow authentication."""
    endpoints = await discover_oidc_endpoints(issuer)
    code_verifier = generate_pkce_verifier()
    async with httpx.AsyncClient(timeout=AUTH_HTTP_TIMEOUT) as client:
        # Start device authorization
        try:
            device_response = await client.post(
                endpoints["device_authorization_endpoint"],
                data={
                    "client_id": client_id,
                    "scope": "openid profile email",
                    "code_challenge": pkce_challenge(code_verifier),
                    "code_challenge_method": "S256",
                },
            )
            device_response.raise_for_status()
            device_data = device_response.json()

        except httpx.HTTPError as e:
            raise AuthError(
                f"Failed to start device authorization: {describe_http_error(e)}"
            )

        # Show user instructions
        console.print("\n[bold blue]Device Authorization Required[/bold blue]")
        verification_uri = (
            device_data.get("verification_uri_complete")
            or device_data["verification_uri"]
        )
        browser_opened = open_authorization_url(verification_uri)
        if browser_opened:
            console.print(f"Opened browser: [link]{verification_uri}[/link]")
        else:
            console.print(f"Please visit: [link]{verification_uri}[/link]")
        if not device_data.get("verification_uri_complete"):
            console.print(
                f"And enter code: [bold green]{device_data['user_code']}[/bold green]"
            )
        console.print("\nWaiting for authorization...")

        # Poll for token
        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 1800)
        start_time = time.time()

        while time.time() - start_time < expires_in:
            await asyncio.sleep(interval)

            try:
                token_response = await client.post(
                    endpoints["token_endpoint"],
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_data["device_code"],
                        "client_id": client_id,
                        "code_verifier": code_verifier,
                    },
                )

                if token_response.status_code == 200:
                    token_data = token_response.json()
                    if not isinstance(token_data, dict):
                        raise AuthError("OIDC token response is not a JSON object")
                    return cast(Dict[str, Any], token_data)
                elif token_response.status_code == 400:
                    error_data = token_response.json()
                    error = error_data.get("error", "unknown_error")

                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        interval += 5
                        continue
                    elif error == "expired_token":
                        raise AuthError("Device code expired")
                    else:
                        raise AuthError(f"Authorization failed: {error}")

            except httpx.HTTPError as e:
                raise AuthError(f"Failed to poll for token: {describe_http_error(e)}")

        raise AuthError("Authorization timed out")


async def refresh_access_token(
    issuer: str, client_id: str, refresh_token: str
) -> Dict[str, Any]:
    """Refresh the access token using the stored refresh token."""
    endpoints = await discover_oidc_endpoints(issuer)
    async with httpx.AsyncClient(timeout=AUTH_HTTP_TIMEOUT) as client:
        try:
            token_response = await client.post(
                endpoints["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                },
            )
        except httpx.HTTPError as e:
            raise AuthError(
                f"Failed to refresh access token: {describe_http_error(e)}"
            ) from e

    if token_response.status_code == 200:
        token_data = token_response.json()
        if not isinstance(token_data, dict):
            raise AuthError("OIDC refresh response is not a JSON object")
        return cast(Dict[str, Any], token_data)

    if token_response.status_code == 400:
        error = token_response.json().get("error")
        if error in {"invalid_grant", "invalid_request"}:
            raise AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE)

    raise AuthError(
        f"Failed to refresh access token: HTTP {token_response.status_code}"
    )


def get_user_info(token: str) -> Optional[Dict[str, Any]]:
    """Extract unverified display information from a JWT token."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
            "preferred_username": payload.get("preferred_username"),
            "exp": payload.get("exp"),
        }
    except jwt.InvalidTokenError:
        return None


def is_token_expired(token: str) -> bool:
    """Check the current access token's own exp claim.

    The exp value is used only as a local refresh hint. Identity data is not
    trusted from the unsigned local decode.
    """
    claims = get_user_info(token)
    exp = claims.get("exp") if claims else None
    if exp is None:
        return False
    return time.time() >= float(exp)


async def fetch_user_info(issuer: str, token: str) -> Dict[str, Any]:
    """Fetch verified user information from the OIDC userinfo endpoint."""
    endpoints = await discover_oidc_endpoints(issuer)
    userinfo_endpoint = endpoints.get("userinfo_endpoint")
    if not userinfo_endpoint:
        raise AuthError("OIDC provider does not expose a userinfo endpoint.")

    async with httpx.AsyncClient(timeout=AUTH_HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthError(
                f"Failed to fetch user information: {describe_http_error(exc)}"
            ) from exc

    if response.status_code == 401:
        raise AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE)
    if response.status_code >= 400:
        raise AuthError(
            f"Failed to fetch user information: HTTP {response.status_code}"
        )

    payload = response.json()
    if not isinstance(payload, dict):
        raise AuthError("OIDC userinfo response is not a JSON object")
    return cast(Dict[str, Any], payload)


def persist_refreshed_token(config: Any, token_data: Dict[str, Any]) -> str:
    """Persist a refreshed token response and return the new access token."""
    refreshed_token = token_data.get("access_token")
    if not isinstance(refreshed_token, str) or refreshed_token == "":
        raise AuthError("OIDC refresh response did not include an access token.")

    refreshed_refresh_token = token_data.get("refresh_token")
    config_manager.set_token(
        refreshed_token,
        (
            refreshed_refresh_token
            if isinstance(refreshed_refresh_token, str)
            else config.refresh_token
        ),
        None,
    )
    return refreshed_token


def refresh_current_session() -> str:
    """Refresh and persist the current CLI session access token."""
    config = config_manager.load_config()
    if not config.refresh_token:
        raise AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE)

    token_data = asyncio.run(
        refresh_access_token(
            config.issuer or DEFAULT_ISSUER,
            config.client_id or DEFAULT_CLIENT_ID,
            config.refresh_token,
        )
    )
    return persist_refreshed_token(config, token_data)


async def async_refresh_current_session() -> str:
    """Async variant of refresh_current_session for request paths in an event loop."""
    config = config_manager.load_config()
    if not config.refresh_token:
        raise AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE)

    token_data = await refresh_access_token(
        config.issuer or DEFAULT_ISSUER,
        config.client_id or DEFAULT_CLIENT_ID,
        config.refresh_token,
    )
    return persist_refreshed_token(config, token_data)


def require_access_token() -> str:
    """Return a valid access token, refreshing it when possible."""
    config = config_manager.load_config()
    token = config.access_token
    if not token:
        raise AuthError(NOT_AUTHENTICATED_MESSAGE)
    if is_token_expired(token):
        return refresh_current_session()

    return token


async def async_require_access_token() -> str:
    """Async variant of require_access_token for API calls inside an event loop."""
    config = config_manager.load_config()
    token = config.access_token
    if not token:
        raise AuthError(NOT_AUTHENTICATED_MESSAGE)
    if is_token_expired(token):
        return await async_refresh_current_session()
    return token


def login(
    local: Annotated[
        bool,
        typer.Option(
            "--local",
            help="Use the local LightNow stack at auth.lightnow.local.",
        ),
    ] = False,
    issuer: Annotated[
        Optional[str],
        typer.Option(
            "--issuer",
            help="Override the LightNow OIDC issuer. Intended for development only.",
            hidden=True,
        ),
    ] = None,
    client_id: Annotated[
        Optional[str],
        typer.Option(
            "--client-id",
            help="Override the LightNow OIDC client. Intended for development only.",
            hidden=True,
        ),
    ] = None,
) -> None:
    """Authenticate with LightNow."""
    try:
        resolved_issuer = issuer or (LOCAL_ISSUER if local else DEFAULT_ISSUER)
        resolved_client_id = client_id or DEFAULT_CLIENT_ID
        resolved_registry_api_url = LOCAL_REGISTRY_API_URL if local else None
        resolved_admin_api_url = LOCAL_ADMIN_API_URL if local else None

        # Store auth config
        config_manager.set_auth_config(
            resolved_issuer,
            resolved_client_id,
            resolved_registry_api_url,
            resolved_admin_api_url,
        )

        # Perform device code flow
        if local:
            console.print(
                "[bold blue]Starting local LightNow authentication...[/bold blue]"
            )
        else:
            console.print("[bold blue]Starting LightNow authentication...[/bold blue]")
        token_data = asyncio.run(device_code_flow(resolved_issuer, resolved_client_id))
        token = token_data.get("access_token")
        if not isinstance(token, str) or token == "":
            raise AuthError("OIDC token response did not include an access token.")
        refresh_token = token_data.get("refresh_token")

        # Fetch user info from the issuer instead of trusting local JWT claims.
        user_info = asyncio.run(fetch_user_info(resolved_issuer, token))

        # Store token and user info
        config_manager.set_token(
            token,
            refresh_token if isinstance(refresh_token, str) else None,
            None,
        )

        console.print("[bold green]✓ Authentication successful![/bold green]")
        if user_info:
            name = (
                user_info.get("name")
                or user_info.get("preferred_username")
                or user_info.get("email")
            )
            if name:
                console.print(f"Logged in as: [bold]{name}[/bold]")

    except AuthError as e:
        console.print(f"[bold red]Authentication failed:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        raise typer.Exit(1)


def status() -> None:
    """Show current authentication status."""
    user_info = current_user_info()

    console.print("[bold green]Authenticated with LightNow[/bold green]")
    name = (
        user_info.get("name")
        or user_info.get("preferred_username")
        or user_info.get("email")
    )
    if name:
        console.print(f"Account: [bold]{name}[/bold]")

    email = user_info.get("email")
    if email:
        console.print(f"Email: [bold]{email}[/bold]")

    console.print(f"Context: [bold]{config_manager.context_display_name()}[/bold]")

    exp = user_info.get("exp")
    if exp:
        exp_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(exp))
        console.print(f"Token expires: [dim]{exp_time}[/dim]")


def whoami() -> None:
    """Show current user information."""
    user_info = current_user_info()

    # Display user information
    console.print("[bold blue]Current User Information:[/bold blue]")

    name = user_info.get("name")
    if name:
        console.print(f"Name: [bold]{name}[/bold]")

    email = user_info.get("email")
    if email:
        console.print(f"Email: [bold]{email}[/bold]")

    username = user_info.get("preferred_username")
    if username:
        console.print(f"Username: [bold]{username}[/bold]")

    sub = user_info.get("sub")
    if sub:
        console.print(f"Subject: [dim]{sub}[/dim]")

    exp = user_info.get("exp")
    if exp:
        exp_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(exp))
        console.print(f"Token expires: [dim]{exp_time}[/dim]")


def current_user_info() -> Dict[str, Any]:
    """Return current user info after verifying or refreshing the access token."""
    try:
        token = require_access_token()
    except AccessTokenExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except AuthError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc

    config = config_manager.load_config()
    try:
        return asyncio.run(fetch_user_info(config.issuer or DEFAULT_ISSUER, token))
    except AccessTokenExpired:
        try:
            token = refresh_current_session()
            config = config_manager.load_config()
            return asyncio.run(fetch_user_info(config.issuer or DEFAULT_ISSUER, token))
        except AccessTokenExpired as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        except AuthError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    except AuthError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def logout() -> None:
    """Clear stored authentication token."""
    config_manager.clear_token()
    console.print("[green]✓ Logged out successfully[/green]")
