# Release Process

## Public Repository Metadata

Use these settings for the public repository:

- Repository name: `lightnow-cli`
- Description: `Official LightNow CLI for MCP registry discovery, client config sync, and local MCP runner workflows.`
- License: `Apache-2.0`
- Topics: `lightnow`, `mcp`, `model-context-protocol`, `cli`

## Checklist

Release Please owns normal version bumps, changelog updates, Git tags, and
GitHub Releases from Conventional Commits.

Required GitHub setup:

- Repository secret: `RELEASE_PLEASE_TOKEN`
- Token permissions: contents, pull requests, and issues read/write for this
  repository
- Repository secret: `HOMEBREW_TAP_TOKEN`
- Token permissions: contents read/write for `lightnow-ai/homebrew-tap`; the
  token is used only to send the post-PyPI repository dispatch
- Repository Actions setting: allow GitHub Actions to create pull requests

Use a real PAT or GitHub App token for `RELEASE_PLEASE_TOKEN`, not the default
`GITHUB_TOKEN`. Tags created with the default token do not trigger the existing
tag-based `Release` workflow, so PyPI publishing would not run.

Normal release flow:

1. Merge Conventional Commit changes to `main`.
2. Release Please opens or updates a release PR.
3. Merge the release PR when ready.
4. Release Please creates the `vX.Y.Z` tag and GitHub Release.
5. The existing tag-based `Release` workflow builds and publishes PyPI
   distributions through Trusted Publishing.
6. After PyPI succeeds, the release workflow dispatches the exact published
   version to `lightnow-ai/homebrew-tap`. The tap regenerates, audits, installs,
   and tests the formula before committing it.

Manual release checklist for fallback or first-time setup:

1. Update `pyproject.toml` and `lightnow_cli/__init__.py` to the same
   semantic version.
2. Run a repository history and working tree scan before making the repository
   public. No tokens, private service URLs with credentials, `.env` files,
   local config files, or generated artifacts may be present.
3. Run:
   ```bash
   make all
   ```
4. Verify the GitHub Actions `CI` workflow is green on `main`.
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
   - a GitHub Release is created with generated release notes and distribution artifacts,
   - PyPI publish succeeds through Trusted Publishing.
8. Verify the `Update formula` workflow in `lightnow-ai/homebrew-tap`. For a
   recovery run, dispatch it manually with formula `lightnow-cli` and the exact
   already-published version; it uses the same generation and verification path
   as the automatic release dispatch.

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
