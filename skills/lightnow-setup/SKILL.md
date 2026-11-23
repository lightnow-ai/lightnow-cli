---
name: lightnow-setup
description: Install or configure LightNow CLI and Proxy, connect an AI assistant to a personal or organization Runtime profile, and check a tool call.
---

# Set up LightNow

Use the existing LightNow CLI to connect the requested AI assistant, directly or
through LightNow Proxy as chosen by the user.
The agent running this skill can configure a different application: for example,
Claude Code can set up Claude Desktop. The target application does not need to
support skills itself.

## Establish the target

Identify the requested client, where it starts MCP processes, and the intended
LightNow account, personal or organization context, Runtime profile and connection
mode (Local Proxy or Direct).
Honor choices already stated by the user. Ask one focused question for a missing
choice that would change whose tools or permissions are used.

Use [client details](references/clients.md) for client IDs, scope and reload steps.
For a server-hosted client, perform setup in its execution environment as its
service user. Keep an existing workstation connection outside that task unchanged.

## Install and sign in

Check `lightnow --version`; check `lightnow-proxy --version` for Local Proxy mode.
Reuse a working installation. For missing programs, follow the official
[installation guide](https://docs.lightnow.ai/getting-started/quickstart/), using
Homebrew on macOS or pipx where appropriate. Check executable availability in a
new shell after installation. Use CLI 1.7.0 or newer for LibreChat Local Proxy
and 1.7.1 or newer before importing an existing configuration.

Use `lightnow whoami` to check the existing login. If sign-in is needed, run
`lightnow login` and let the user complete browser authentication and MFA.
Keep credential handling with the CLI; report account/context metadata without
printing tokens, passwords, session files or Authorization headers.

## Connect the chosen profile

Run `lightnow context` and select the requested organization or **Personal**.
For an already explicit organization ID or slug, `lightnow context --tenant VALUE`
is also supported; `lightnow context --personal` explicitly switches back.
Check the stored selection with `lightnow context --show`.

Use an organization profile already shared with the user or their team. Permission
to manage a profile is distinct from permission to use it. Preserve its audience;
report missing access to the responsible administrator.

Use the selected context's `default` profile unless the user chose another.
For another profile, append `--profile NAME` to sync using its actual Runtime
profile name. The CLI has no profile-list command or interactive profile picker.
If the intended profile is unclear, ask for its name or help the user find it in
LightNow Runtime profiles; do not invent a discovery command or silently choose
a different profile. A missing organization or inaccessible profile must be
resolved before claiming the connection works.

If the client already has MCP entries, inspect their scope before replacing them.
Explain the proposed replacement and preserve the user's intended servers.
For supported imports, follow the [import guide](https://docs.lightnow.ai/how-to/import-existing-mcp-config/).
Import preview sends the source file to LightNow, including embedded credentials;
get the user's informed choice before sending a file they have not agreed to import.
Use the CLI's normal confirmation and backup handling rather than automatically
adding `--yes`. LibreChat and Gemini CLI are not import sources.

Run the command for the chosen mode; append `--profile NAME` for an explicitly
chosen profile, including `--profile default` when that choice matters:

```sh
lightnow sync --client CLIENT --local-proxy
```

For Direct, use `lightnow sync --client CLIENT` without `--local-proxy`.
Direct exports server configuration and may write credentials into the client
file; retain the CLI's confirmation rather than adding `--yes`.

Use the target client's actual configuration file. Add `--config-path PATH` when
needed and use that same path for `config-status`. A separate project connection
in Local Proxy mode also needs its own `--local-proxy-config-path` so it does not replace another
connection's generated proxy configuration. See the client details.

Use `--from-settings` only when applying LightNow Config policy is the requested
task, without `--local-proxy`. Settings choose the mode and, when no profile is
explicit, the profile. Before combining it with `--profile`, check the
[CLI compatibility note](references/clients.md#settings-and-profile-compatibility).
Do not let settings silently replace the user's chosen profile or mode.

## Verify in the client

Run `lightnow config-status --client CLIENT` with any selected file paths.
For Local Proxy, use the generated proxy configuration path for
`lightnow-proxy --config PATH --health`. Direct does not require Proxy health;
config-status describes proxy posture, so Direct is not a failed Proxy connection.
Then reload the actual client, inspect its LightNow tools and perform a small
permitted read. Inspect the returned content. These are distinct checks: file
status and health do not establish that the client used a tool.

When activity reporting is enabled, find the matching call under **Control Plane
→ Requests** in the same personal or organization context. Report the configured
client, account/context and profile, the completed checks, and any precise
remaining blocker. Distinguish a generated configuration from a verified client
connection.
