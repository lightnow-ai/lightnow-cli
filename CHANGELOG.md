# Changelog

All notable changes to the LightNow CLI are documented here.

This project follows semantic versioning.

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
