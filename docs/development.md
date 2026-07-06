# Local Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
lightnow --help
```

For local editable installs through `pipx`:

```bash
pipx install --force --editable .
```

## Checks

Run all checks:

```bash
make all
```

Run targeted checks:

```bash
make lint
make format-check
make isort-check
make type-check
make test
make cli-check
make integration
make package
```

The tests cover command parsing, validation logic, token refresh, registry
queries, sync patching, local runner behavior, and secret redaction.

## Local Proxy Config Posture

`lightnow sync --client <client> --local-proxy` should leave the target MCP
client with one LightNow entry and no unmanaged MCP servers unless the customer
explicitly keeps a mixed setup.

Use the redacted status command while testing client integrations:

```bash
lightnow config-status --client codex
lightnow config-status --client claude-desktop
lightnow config-status --client antigravity --json
```

Expected states:

- `managed`: the LightNow Local Proxy entry is configured and no unmanaged
  user MCP servers are present. Client-internal MCP entries may be reported
  separately as `internal_servers`.
- `mixed`: LightNow Local Proxy exists, but unmanaged MCP entries are also
  present.
- `unmanaged`: MCP entries exist, but none point at the LightNow Local Proxy.
- `legacy_runner`: older per-server `lightnow run` wrappers are still present.
- `missing`, `invalid` or `unreadable`: the client config cannot be used as-is.

The command must not print secrets, headers or payloads. It is the local building
block for future UI-led enablement and enterprise posture reporting.

## Local LightNow Environment

Use the local profile when working against the LightNow local stack:

```bash
lightnow login --local
```

Local HTTPS verification depends on the workspace local CA. Run the LightNow
local stack certificate setup before using `*.lightnow.local` endpoints.
