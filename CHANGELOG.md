# Changelog

All notable changes to the LightNow CLI are documented here.

This project follows semantic versioning.

## [1.5.0](https://github.com/lightnow-ai/lightnow-cli/compare/v1.4.0...v1.5.0) (2026-07-18)


### Features

* **#15:** configure runtime tool argument capture ([c83ff54](https://github.com/lightnow-ai/lightnow-cli/commit/c83ff54d4373bde152cca68a40f1c4e802e79e86))
* **sync:** configure tool argument capture ([09f591a](https://github.com/lightnow-ai/lightnow-cli/commit/09f591a94cb014998681223147a79ffcaff062e8))

## [1.4.0](https://github.com/lightnow-ai/lightnow-cli/compare/v1.3.1...v1.4.0) (2026-07-17)


### Features

* **#13:** add managed updates ([844e9c5](https://github.com/lightnow-ai/lightnow-cli/commit/844e9c5cb0fb2134f9e6014f02a0a4ca49db019c))
* add managed updates ([275387e](https://github.com/lightnow-ai/lightnow-cli/commit/275387e05fd40f5b0564d9001acf9d3ed19ced5a))


### Bug Fixes

* harden managed update checks ([0c3a285](https://github.com/lightnow-ai/lightnow-cli/commit/0c3a285ccdfab4b39e60f905c9274e242245b2d8))

## [1.3.1](https://github.com/lightnow-ai/lightnow-cli/compare/v1.3.0...v1.3.1) (2026-07-17)


### Bug Fixes

* **sync:** replace existing codex proxy connection ([c09b7f7](https://github.com/lightnow-ai/lightnow-cli/commit/c09b7f75f9d78c943ed7556b10b10ff99dbb2008))

## [1.3.0](https://github.com/lightnow-ai/lightnow-cli/compare/v1.2.0...v1.3.0) (2026-07-15)


### Features

* **#7:** support account-bound proxy connections ([11b2f5a](https://github.com/lightnow-ai/lightnow-cli/commit/11b2f5a3806e2b9f67637e231783692aacdb78de))
* **#9:** device control plane ([0d1db03](https://github.com/lightnow-ai/lightnow-cli/commit/0d1db033be28b23e45d9ae38bc0c4754a0cd7380))
* **devices:** register local proxy client inventory ([ebcbe95](https://github.com/lightnow-ai/lightnow-cli/commit/ebcbe95a099d7b203a89c5886a5d90b300a47e38))
* support account-bound proxy connections ([6d9f3ad](https://github.com/lightnow-ai/lightnow-cli/commit/6d9f3ad8f633182e57ca8291a9eb297a6330fd72))


### Bug Fixes

* address account-bound session review ([6e41fd8](https://github.com/lightnow-ai/lightnow-cli/commit/6e41fd881789a0f4ac287fd4abece0c69a964093))


### Documentation

* **devices:** explain control plane registration ([f4329f5](https://github.com/lightnow-ai/lightnow-cli/commit/f4329f559ff43f4b501b500b19c3b40f57ad94bf))

## [1.2.0](https://github.com/lightnow-ai/lightnow-cli/compare/v1.1.0...v1.2.0) (2026-07-12)


### Features

* **#5:** support runtime Vault bindings ([7750c27](https://github.com/lightnow-ai/lightnow-cli/commit/7750c27b87567012eadb6e96a934a25d5d205b0e))
* **secrets:** support runtime Vault bindings ([7cacff6](https://github.com/lightnow-ai/lightnow-cli/commit/7cacff66c162c1c320fef5195c6acd0f3a77ca45))


### Bug Fixes

* **#4:** fix/empty mcp server sync ([a2865bf](https://github.com/lightnow-ai/lightnow-cli/commit/a2865bf21ba7fba7b7bb7115388ad45f153c3280))
* **sync:** handle empty MCP server profiles ([065d940](https://github.com/lightnow-ai/lightnow-cli/commit/065d940ea0d7a4016705a8fb124ebdc07d204a7f))

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
