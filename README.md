# LightNow CLI

[![PyPI](https://img.shields.io/pypi/v/lightnow-cli.svg)](https://pypi.org/project/lightnow-cli/)

The **LightNow CLI** connects your local MCP clients with LightNow.

Use it to discover MCP servers, sync client configurations, and run
MCP servers with LightNow-managed secrets.

## Install

Requirements:

- Python 3.11 or higher
- `pipx`
- a [LightNow account](https://www.lightnow.ai/)

Install the CLI with `pipx`:

```bash
pipx install lightnow-cli
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

## Sync a Client Configuration

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

For the M2 Local Proxy flow, write a single Codex MCP entry that points to the
local LightNow proxy instead of writing one entry per MCP server:

```bash
lightnow sync --client codex --local-proxy
```

The same Local Proxy mode is available for local clients with JSON MCP config
files, including Claude Desktop and Google Antigravity:

```bash
lightnow sync --client claude-desktop --local-proxy
lightnow sync --client antigravity --local-proxy
```

This writes one client entry that starts `mcp-proxy` over stdio plus a
per-client config under `~/.lightnow/mcp-proxy/`. The Local Proxy then
uses the existing LightNow CLI login session to fetch the selected runtime
profile and resolve server config plus secrets at runtime. For daemon-style
testing, pass `--local-proxy-transport http` to write a localhost Streamable
HTTP entry instead. The older `--runner` flag remains a compatibility path that
writes one `lightnow run` wrapper per profile server.

Note: Google has moved individual Gemini CLI users to Antigravity. LightNow
keeps `gemini-cli` support for Enterprise/API-key environments, but
`antigravity` is the recommended Google local client target for individuals.

Check whether a client is still in the expected Local Proxy posture:

```bash
lightnow config-status --client codex
lightnow config-status --client antigravity --json
```

This reports whether the LightNow proxy entry is present, whether unmanaged MCP
servers are still configured next to it, and whether an older per-server runner
wrapper is still in use. The status output is redacted and intended for the
LightNow UI, installers and enterprise rollout checks as well as local
troubleshooting.

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
