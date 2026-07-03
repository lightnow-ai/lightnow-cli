# LightNow CLI Examples

This directory contains a small MCP server metadata example that can be
validated with the LightNow CLI and used as the starting point for publishing
your own server.

## Files

- `server.json` - Example MCP server metadata file
- `docs.md` - Example documentation file
- `openapi.json` - Example OpenAPI specification file

## Usage

### Validate the example files

```bash
lightnow validate --server examples/server.json --docs examples/docs.md --spec examples/openapi.json
```

### Publish the example server (requires authentication)

```bash
# First, authenticate with LightNow
lightnow login

# Then publish the server metadata, docs, and optional API spec
lightnow publish --server examples/server.json --docs examples/docs.md --spec examples/openapi.json
```

Before publishing, change `name` in `server.json` to a namespace you own, for
example `your-domain.example/memory` or the namespace assigned to your
LightNow account. LightNow rejects unverified namespaces explicitly.

### Test validation only (without publishing)

```bash
lightnow publish --server examples/server.json --docs examples/docs.md --spec examples/openapi.json --validate-only
```

### Configure and sync after publishing

After the server is published:

1. Open **Integrations** in LightNow.
2. Add the published server to your runtime profile.
3. Save the profile configuration.
4. Sync the profile into an MCP client:

```bash
lightnow sync --client codex --profile default
```

If your own server uses secrets, store them in LightNow and use placeholders
instead of writing secret values into the client config:

```bash
lightnow sync --client codex --profile default --secret-mode placeholder
```

Use the Local Runner to keep secrets out of the client config:

```bash
lightnow sync --client codex --profile default --runner
```

## Customizing for Your Server

1. Copy the example files to your project directory
2. Update `server.json` with your server's metadata:
   - Change `name` to `namespace/name` using a namespace you own
   - Update `version` to your server's version (semantic versioning)
   - Modify `description`, `author`, `license`, etc.
   - Configure the `transport` section for your server
   - Declare required secrets in `packages[].environmentVariables` when your server needs them
3. Update `docs.md` with your server's documentation
4. Update `openapi.json` with your server's API specification
5. Validate your files: `lightnow validate --server server.json --docs docs.md --spec openapi.json`
6. Publish to registry: `lightnow publish --server server.json --docs docs.md --spec openapi.json`

## Server.json Requirements

The `server.json` file must include:

- `name`: Server ID in `namespace/name` format
- `version`: Semantic version (e.g., "1.0.0")
- `description`: Server description (10-500 chars)
- `transport`: Transport configuration with a `type` field

Optional fields include `author`, `license`, `homepage`, `repository`,
`keywords`, `packages`, `remotes`, and `_meta`.

## OpenAPI Requirements

The OpenAPI specification must be version 3.x and include:

- `openapi`: Version string (e.g., "3.0.0")
- `info`: Information object with `title` and `version`
- `paths`: API paths object

Both JSON and YAML formats are supported.
