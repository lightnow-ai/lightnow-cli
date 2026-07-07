# Changelog

All notable changes to the LightNow CLI are documented here.

This project follows semantic versioning.

## [1.1.0](https://github.com/lightnow-ai/lightnow-cli/compare/v1.0.5...v1.1.0) (2026-07-07)


### Features

* add whoami json output ([9eab480](https://github.com/lightnow-ai/lightnow-cli/commit/9eab480b85765fa50506f4d7126790c2a3ebd094))


### Bug Fixes

* **#2:** fix import wizard client configs ([ae0f0b4](https://github.com/lightnow-ai/lightnow-cli/commit/ae0f0b4931dcb1516be65473df3ca73397d6c16e))
* support wizard client config imports ([35a7347](https://github.com/lightnow-ai/lightnow-cli/commit/35a7347fa438af6e4d9fb754f87a7a5d6a709548))
* write default local proxy config alias ([47223f6](https://github.com/lightnow-ai/lightnow-cli/commit/47223f62b96ef0790d027ada11abf84459eb63fb))

## 1.0.0 - Unreleased

Initial stable release candidate.

### Added

- OIDC Device Code login for LightNow.
- Automatic access-token refresh using the stored refresh token.
- `status`, `whoami`, and `logout` account commands.
- MCP registry discovery with `search`, `favorites`, and `info`.
- MCP publishing and local validation commands.
- Integration profile sync for Codex, Claude, Cursor, Windsurf, Continue,
  Gemini CLI, LibreChat, VS Code, and MCP Inspector targets.
- Client-side sync with explicit plaintext-secret confirmation.
- Placeholder sync for secret-safe client configs.
- Local runner mode for resolving LightNow-managed secrets at process runtime.
- Atomic client-config writes with restrictive permissions.
- Secret redaction for dry-run previews, errors, and terminal output.

### Security

- Device authorization uses PKCE.
- API calls use shared 401 handling and refresh retry behavior.
- Expired sessions use consistent messaging across commands.
