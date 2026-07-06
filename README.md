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

## Sign In

```bash
lightnow login
```

The CLI opens a browser window for LightNow authentication. After sign-in, check
your session with:

```bash
lightnow status
```

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

Install the Local Proxy executable first:

```bash
pipx install lightnow-proxy
```

Or with `uv`:

```bash
uv tool install lightnow-proxy
```

The proxy package installs the `lightnow-proxy` command used in MCP client configs.

Then sync your client:

```bash
lightnow sync --client codex --local-proxy
```

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

Check whether a client is configured as expected:

```bash
lightnow config-status --client codex
lightnow config-status --client claude-desktop --json
lightnow config-status --client cursor --json
lightnow config-status --client vscode --json
```

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

For daemon-style local testing, `--local-proxy-transport http` writes a
localhost Streamable HTTP entry instead of a stdio auto-start entry.

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
