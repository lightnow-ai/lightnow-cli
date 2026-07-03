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
