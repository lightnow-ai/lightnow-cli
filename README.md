# LightNow CLI

[![PyPI](https://img.shields.io/pypi/v/lightnow-cli.svg)](https://pypi.org/project/lightnow-cli/)

Find MCP servers and connect your AI assistants to the tools you manage in LightNow.

The CLI signs you in and configures your clients. [LightNow Proxy](https://github.com/lightnow-ai/lightnow-proxy)
runs their connections to the servers in your Runtime profiles.

## Get started

You need a [LightNow account](https://lightnow.ai/), an AI client and a Runtime
profile with the server connections you want to use.

### 1. Install the CLI and proxy

With Homebrew:

```sh
brew tap lightnow-ai/tap
brew install lightnow-cli lightnow-proxy
```

Or with [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11 or later:

```sh
uv tool install lightnow-cli
uv tool install lightnow-proxy
```

[pipx](https://pipx.pypa.io/stable/installation/) is also supported.

### 2. Sign in and connect a client

If your client already has MCP servers, [review the import options](https://docs.lightnow.ai/how-to/import-existing-mcp-config)
before syncing.

```sh
lightnow login
lightnow sync --client codex --profile default --local-proxy
```

Replace `default` with your profile's name. Restart the client and check its MCP
tools list. The CLI supports Codex, Claude Code, Claude Desktop, Cursor, VS Code,
Antigravity, Gemini CLI and LibreChat.

See [Connect AI assistants](https://docs.lightnow.ai/getting-started/sync-mcp-clients)
for each client's setup and configuration paths.

For LibreChat, run the CLI and proxy on its backend and select its configuration:

```sh
lightnow sync --client librechat --profile default --local-proxy --config-path /path/to/librechat.yaml
```

LibreChat users with access to this connection share the backend account's
LightNow permissions. Reload LibreChat after changing its configuration.

### 3. Check the connection

```sh
lightnow status
lightnow config-status --client codex
lightnow-proxy --config ~/.lightnow/lightnow-proxy/codex.yaml --health
```

For help, see [Troubleshoot the connection](https://docs.lightnow.ai/how-to/debug-local-proxy).

## Useful commands

| Task | Command |
| --- | --- |
| Find servers | `lightnow search sonarqube` |
| View your favorites | `lightnow favorites` |
| Use organization-managed client settings | `lightnow sync --client codex --from-settings` |
| Check for updates | `lightnow update --check` |
| Update the CLI and proxy | `lightnow update` |

Use `lightnow --help` or `lightnow <command> --help` for available options.
For profiles, organization connections and other workflows, see the
[CLI reference](https://docs.lightnow.ai/reference/cli).

## Contributing

See [development setup](docs/development.md), [contributing](CONTRIBUTING.md),
[release process](docs/release.md) and [security policy](SECURITY.md).

## License

[Apache License 2.0](LICENSE).
