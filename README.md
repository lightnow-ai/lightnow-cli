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

This writes a Codex entry that starts `mcp-proxy` over stdio plus a
`~/.lightnow/mcp-proxy.yaml` config for the Local Proxy. The Local Proxy then
uses the existing LightNow CLI login session to fetch the selected runtime
profile and resolve server config plus secrets at runtime. For daemon-style
testing, pass `--local-proxy-transport http` to write a localhost Streamable
HTTP entry instead. The older `--runner` flag remains a compatibility path that
writes one `lightnow run` wrapper per profile server.

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
