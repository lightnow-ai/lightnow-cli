# Contributing to LightNow CLI

Thanks for improving the LightNow CLI.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
lightnow --help
```

## Checks

Run the full local check suite before opening a pull request:

```bash
make all
```

For smaller iterations:

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

## Pull Requests

Pull requests should:

- keep command behavior explicit and predictable,
- include tests for command-line behavior and API error handling,
- avoid silent fallbacks,
- avoid logging or snapshotting secrets,
- update README or examples when user-facing behavior changes.

## Security

Do not include real tokens, API keys, passwords, `.env` files, local LightNow
config files, or customer data in issues, pull requests, tests, or fixtures.

Report vulnerabilities privately using the instructions in [SECURITY.md](SECURITY.md).
