# LightNow Memory MCP Server

This example shows the metadata shape for a local stdio MCP server that can be
published to LightNow and synced into local MCP clients.

## Features

- Simple stdio transport configuration
- Client-ready launch command
- Metadata, docs, and optional OpenAPI artifacts

## Installation

The example server is launched with `npx`:

```bash
npx -y @modelcontextprotocol/server-memory
```

## Usage

Validate the artifacts:

```bash
lightnow validate --server examples/server.json --docs examples/docs.md --spec examples/openapi.json
```

Publish the server after changing `server.json` to a namespace you own:

```bash
lightnow publish --server examples/server.json --docs examples/docs.md --spec examples/openapi.json
```

## Configuration

After publishing, add the server to a runtime profile in LightNow, save the
configuration, and sync the profile into a local MCP client:

```bash
lightnow sync --client codex --profile default
```

## License

This example metadata is licensed under MIT.
