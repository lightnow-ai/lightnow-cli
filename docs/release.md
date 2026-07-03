# Release Process

## Public Repository Metadata

Use these settings for the public repository:

- Repository name: `lightnow-cli`
- Description: `Official LightNow CLI for MCP registry discovery, client config sync, and local MCP runner workflows.`
- License: `Apache-2.0`
- Topics: `lightnow`, `mcp`, `model-context-protocol`, `cli`

## Checklist

Before publishing a release:

1. Update `pyproject.toml` and `lightnow_cli/__init__.py` to the same
   semantic version.
2. Run a repository history and working tree scan before making the repository
   public. No tokens, private service URLs with credentials, `.env` files,
   local config files, or generated artifacts may be present.
3. Run:
   ```bash
   make all
   ```
4. Verify the GitHub Actions `CI` workflow is green on `master`.
5. Configure PyPI Trusted Publishing before the first PyPI release:
   - PyPI project name: `lightnow-cli`
   - Owner: `lightnow-ai`
   - Repository: `lightnow-cli`
   - Workflow: `release.yml`
   - Environment: `pypi`
6. Create a signed or otherwise traceable Git tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
7. Verify the GitHub Actions `Release` workflow:
   - tests pass,
   - source distribution and wheel are built,
   - `twine check` passes,
   - PyPI publish succeeds through Trusted Publishing.

Do not publish with long-lived local PyPI tokens.

## PyPI Account Setup

A PyPI account is required to create or administer the `lightnow-cli` project.
The GitHub workflow should not store a PyPI password or API token. Use PyPI
Trusted Publishing so PyPI accepts releases from the tagged GitHub Actions
workflow via OIDC.

For a first release, create the Trusted Publisher on PyPI before pushing the
tag. If the project does not exist yet, use PyPI's pending publisher flow for
`lightnow-cli`.

## Package Validation

`make package` builds the source distribution and wheel, then runs:

```bash
python -m twine check dist/*
```
