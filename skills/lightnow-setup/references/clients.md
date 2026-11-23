# Client details

Use the [client guides](https://docs.lightnow.ai/getting-started/sync-mcp-clients/)
for current provider UI labels and configuration scope. Paths below are defaults;
use the actual path printed by sync for diagnostics.

| Client | CLI ID | Configuration and reload |
| --- | --- | --- |
| Codex | `codex` | User file `~/.codex/config.toml`; account for `CODEX_HOME`, project files and plugins. Start a new session and inspect `/mcp`. |
| Claude Code | `claude-code` | `~/.claude.json`; project-local and project-scoped definitions can take precedence. Start a new session and inspect `/mcp`. |
| Claude Desktop | `claude-desktop` | Default CLI destination is the macOS file below. Quit Desktop completely, reopen it and inspect the LightNow connector. |
| Cursor | `cursor` | `~/.cursor/mcp.json`; project files and plugins are additional sources. Restart and inspect LightNow in Customize. |
| VS Code | `vscode` | Editor-managed User `mcp.json`; remote workspaces execute on their own host. Native Copilot Global is a separate format. Reload and check MCP in the intended agent session. |
| Antigravity | `antigravity` | `~/.gemini/config/mcp_config.json`; workspace `.agents/mcp_config.json` and plugins are additional sources. Check `/mcp` in a new CLI session or MCP Servers in the IDE. |
| Gemini CLI | `gemini-cli` | `~/.gemini/settings.json`; review existing allowed/excluded lists and workspace trust. Start a new session and inspect `/mcp`. |
| LibreChat | `librechat` | Active backend `librechat.yaml`, defaulting to the current directory. Restart the backend, add LightNow tools to an agent and use that agent in a chat. |

## Claude Desktop paths

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`.
- Windows PowerShell: pass `--config-path "$env:APPDATA\Claude\claude_desktop_config.json"`.
- Linux: pass `--config-path ~/.config/Claude/claude_desktop_config.json`, or the
  actual Claude path under a custom `XDG_CONFIG_HOME`.

Use the same explicit file path for import, sync and config-status. Desktop
extensions and remote connectors are separate from this local MCP file.

## LibreChat backend

Install both programs inside the backend environment and run setup as the
LibreChat service user. Persist that user's private `.lightnow` state. Run from
the active `librechat.yaml` directory or pass its actual `--config-path`.
The configuration directory must be writable for file replacement.

This connection uses the backend's LightNow account and selected context for
all LibreChat users allowed to reach it. Choose permissions suitable for those
users and use LibreChat's agent access controls. It does not establish a separate
LightNow session per browser user. A workstation's login or executables do not
become available inside a remote backend automatically.

## Settings and profile compatibility

CLI 1.7.1 can replace an explicit `--profile` when `--from-settings` is used.
The corrected CLI preserves it: `sync --help` describes `--profile` as overriding
the settings profile. Use that combination only with a verified supporting
installation. Otherwise use the explicit Direct or Local Proxy command and
`--profile NAME`; if the task requires applying policy as well, report the
version blocker rather than switching profiles. Without an explicit profile,
`--from-settings` uses the configured profile. Neither form changes the selected
personal or organization context.
