# LightNow CLI

[![PyPI](https://img.shields.io/pypi/v/lightnow-cli.svg)](https://pypi.org/project/lightnow-cli/)

The **LightNow CLI** connects your local MCP clients with LightNow.

Use it to sign in, discover MCP servers, configure local AI clients such as
Codex, Claude Desktop, Cursor, VS Code and Google Antigravity, and keep MCP
server configuration and secrets managed by LightNow.

## Install

Requirements for Python-based installs:

- Python 3.11 or higher
- `pipx`
- a [LightNow account](https://www.lightnow.ai/)

Install the CLI with Homebrew:

```bash
brew tap lightnow-ai/tap
brew install lightnow-cli
```

Install the CLI with `pipx`:

```bash
pipx install lightnow-cli
```

Or install it with `uv`:

```bash
uv tool install lightnow-cli
```

If you prefer installing directly from the public repository:

```bash
pipx install git+https://github.com/lightnow-ai/lightnow-cli.git
```

## Update

The CLI manages supported installations of both the CLI and Local Proxy:

```bash
lightnow update --check
lightnow update
```

Use `lightnow update --yes` for an approved non-interactive update or
`lightnow update --check --json` for automation. Managed updates support
Homebrew, pipx and uv. Editable, plain pip and unknown virtual-environment
installs are reported but never changed automatically.

Interactive CLI sessions check the release catalog at most once per day and
show a short notice when an update is ready across all supported channels. Set
`LIGHTNOW_NO_UPDATE_CHECK=1` to disable this background check. The Local Proxy
never installs updates itself.

## Sign In

```bash
lightnow login
```

The CLI opens a browser window for LightNow authentication. After sign-in, check
your session with:

```bash
lightnow status
```

For an isolated LightNow environment, obtain a credential-free connection target
from its operator and select it explicitly:

```bash
lightnow login --target ./connection-target.json
```

The complete JSON schema is:

```json
{
  "schemaVersion": 1,
  "issuer": "https://auth.example.test/realms/example",
  "clientId": "lightnow-cli",
  "registryApiUrl": "https://registry.example.test/v0.1",
  "adminApiUrl": "https://admin.example.test/v0/portal"
}
```

All five fields are required; additional fields and embedded credentials are
rejected. URLs must use HTTPS without credentials, query strings or fragments.
The environment operator provisions the public OAuth device-flow client; the CLI
does not register clients. Authentication remains at the product issuer, which
may broker an external identity provider through its browser flow.
The device request includes `openid profile email organization:*`; the operator
must assign the standard `organization` client scope so existing organization
memberships are available to the scoped APIs.

`--target` cannot be combined with `--local`, `--issuer` or `--client-id`. Custom
issuers require a complete target and cannot silently use production API defaults.
Normal `lightnow login` and `lightnow login --local` remain available.

The selected target and named session are saved only after device authorization
and userinfo verification succeed. OIDC discovery must identify the selected
issuer and expose credential-free HTTPS endpoints. Failed login preserves the previous connection.
A different environment or account returns to personal context; logging into the
same target and account preserves the selected organization. Organization selection,
profile requests and generated Local Proxy connections use the selected API URLs.
For acceptance runs, use a separate container user/home and explicit client and
proxy configuration paths so existing desktop connections are not modified.

## Find MCP Servers

Search the LightNow Registry:

```bash
lightnow search sonarqube
```

Show details for one server:

```bash
lightnow info io.github.SonarSource/sonarqube-mcp-server
```

Show your favorites:

```bash
lightnow favorites
```

## Configure an MCP Client

The recommended setup is Local Proxy mode: your MCP client gets one `LightNow`
entry and the local proxy resolves the configured MCP servers and secrets at
runtime.

Install the Local Proxy executable first with Homebrew:

```bash
brew tap lightnow-ai/tap
brew install lightnow-proxy
```

Or install it with `pipx`:

```bash
pipx install lightnow-proxy
```

Or install it with `uv`:

```bash
uv tool install lightnow-proxy
```

The proxy package installs the `lightnow-proxy` command used in MCP client configs.

For local development from a checkout:

```bash
uv tool install --from /path/to/lightnow-proxy lightnow-proxy
```

Then sync your client:

```bash
lightnow sync --client codex --local-proxy
```

### Multiple accounts and organizations

Each Local Proxy connection can have its own client alias, LightNow login,
profile and personal or tenant scope. The sync command binds the connection to
the account that is signed in at that moment; signing in with another account
later does not silently move an existing connection.
Connection aliases are either `lightnow` or start with `lightnow-` or
`lightnow_`, so they remain TOML-safe and distinguishable from unmanaged local
MCP servers.

For example, configure personal and organization connections side by side:

```bash
lightnow login
lightnow sync --client codex --local-proxy \
  --connection lightnow-personal --profile default

# Sign in with the organization account before creating this connection.
lightnow login
lightnow sync --client codex --local-proxy \
  --connection lightnow-acme --tenant <tenant-id> --profile engineering
```

Codex then exposes both `lightnow-personal` and `lightnow-acme`. Re-run sync
with the same `--connection` value to update that connection. Account-bound
sessions are stored separately under `~/.lightnow/sessions/`; proxy YAML files
contain only their path and expected identity, never access or refresh tokens.

Use `lightnow config-status --client codex --json` to inspect all connections,
including their non-secret account, scope, profile and session-binding status.
Signing in to another account preserves existing bound sessions. An explicit
`lightnow logout` removes the active account's named session, so proxies bound
to that account stop authenticating until it is signed in and synced again.

Other common clients:

```bash
lightnow sync --client claude-desktop --local-proxy
lightnow sync --client antigravity --local-proxy
lightnow sync --client cursor --local-proxy
lightnow sync --client vscode --local-proxy
```

Let LightNow choose the configured organization policy for a client:

```bash
lightnow sync --client codex --from-settings
```

When LightNow Config blocks unmanaged MCP servers, managed clients keep only the
`LightNow` entry. When unmanaged servers are allowed, existing user-managed MCP
entries stay in the client config and `config-status` reports `mixed`.

Check whether a client is configured as expected:

```bash
lightnow config-status --client codex
lightnow config-status --client claude-desktop --json
lightnow config-status --client cursor --json
lightnow config-status --client vscode --json
```

With metadata telemetry enabled, each Local Proxy sync also registers the
machine and its client-specific Runtime Profile in the LightNow Control Plane.
The CLI stores a stable installation UUID in its protected configuration and a
separate stable client-instance UUID in each generated proxy configuration. It
does not report IP or MAC addresses, serial numbers, local paths, or secrets.
The Control Plane can additionally show the observed CLI and Proxy versions,
their supported installation channel and whether an update is available.

For details, examples, diagrams and troubleshooting, see
[Connect MCP clients](https://docs.lightnow.ai/getting-started/sync-mcp-clients).

### Direct Sync

Sync your default LightNow runtime profile into Codex:

```bash
lightnow sync --client codex
```

Preview changes before writing:

```bash
lightnow sync --client codex --dry-run
```

Use runner mode when you do not want secrets written into client configuration
files:

```bash
lightnow sync --client codex --runner
```

Local Proxy Mode always starts `lightnow-proxy` over stdio. To configure a
separately hosted proxy for Codex, pass its HTTPS MCP endpoint explicitly:

```bash
lightnow sync --client codex --remote-proxy-url https://proxy.example.com/mcp
```

Note: Google has moved individual Gemini CLI users to Antigravity. LightNow
keeps `gemini-cli` support for Enterprise/API-key environments, but
`antigravity` is the recommended Google local client target for individuals.

## Run One Server

Run a configured MCP server through LightNow:

```bash
lightnow run --profile default --server sonarqube
```

## More Documentation

- [CLI usage guide](https://docs.lightnow.ai/getting-started/sync-mcp-clients)
- [CLI reference](https://docs.lightnow.ai/reference/cli)
- [Local development](docs/development.md)
- [Release process](docs/release.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
