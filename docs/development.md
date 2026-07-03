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

## Local LightNow Environment

Use the local profile when working against the LightNow local stack:

```bash
lightnow login --local
```

Local HTTPS verification depends on the workspace local CA. Run the LightNow
local stack certificate setup before using `*.lightnow.local` endpoints.
