# Security Policy

## Supported Versions

Security updates are provided for the latest released `1.x` version of the
LightNow CLI.

## Reporting a Vulnerability

Please report security issues privately. Do not open a public GitHub issue for
vulnerabilities, leaked credentials, token handling issues, or secret exposure.

Use one of these channels:

- Email: security@lightnow.ai
- GitHub private vulnerability reporting, if enabled on the repository

Include:

- affected command or workflow,
- operating system and Python version,
- exact CLI version from `lightnow --version`,
- reproduction steps,
- whether any token, secret, or client configuration may have been exposed.

Do not include real secrets, access tokens, refresh tokens, API keys, or
customer data in the report. Replace sensitive values with `[REDACTED]`.

## Secret Handling Expectations

The CLI may intentionally write resolved MCP server secrets into local client
configuration files when the user explicitly chooses client-side sync. It must
not print those values in terminal output, logs, dry-run previews, test
snapshots, or error messages.

For users who do not want secrets stored in client configuration files, use:

```bash
lightnow sync --client codex --runner
```

Runner mode writes wrapper commands and resolves secrets only for child MCP
server processes at runtime.
