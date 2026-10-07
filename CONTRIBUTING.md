# Contributing

Eazy SDK requires Python 3.13 or newer and [uv](https://docs.astral.sh/uv/). Browser tests need
Chromium: `uv run playwright install chromium`.

Install the workspace:

```bash
uv sync --all-packages --all-extras
```

Before opening a pull request, run:

```bash
uv run ruff check
uv run mypy
uv run pytest -q
uv run python scripts/docs_freshness.py check
```

If documentation changed, also validate the pages and build the site as described in
[Documentation site](#documentation-site):

```bash
uv run --group docs python docs-site/scripts/validate_docs.py
```

Architecture changes must follow `docs/implementation/README.md`. Update
`docs/implementation/STATUS.md` with the commands actually run and their results. Add tests with
behavior changes, keep commits focused, and never include credentials, local databases, generated
build output, or virtual environments.

## Documentation site

`docs.yml` builds the Sphinx site with warnings as errors on every push to `master` and deploys it
to GitHub Pages at <https://0cherednoq.github.io/eazy-sdk/>. One-time setup: repository
Settings -> Pages -> Source: **GitHub Actions**.

Build it locally the same way:

```bash
uv run --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs docs-site/_build/html
```

The site lives under the `/eazy-sdk/` path, so pages must not link from the domain root. Use
`{doc}` roles, `:link-type: doc` on cards, or relative `href` values.

## Releasing

A release is one annotated tag. `release.yml` builds all ten workspace packages once, audits them,
uploads them to PyPI and attaches the same files to a GitHub release.

1. Set the same version in the root `pyproject.toml`, every `plugins/*/pyproject.toml` and the
   version bounds between them, then update `CHANGELOG.md`.
2. Run the gates listed above and merge to `master`.
3. Create an annotated tag whose body is the release notes, and push it:

   ```bash
   git tag -a v0.2.0a8 -m "Eazy SDK 0.2.0a8" -m "Release notes go here."
   git push origin v0.2.0a8
   ```

The tag must equal `v` plus the project version, otherwise the build job stops.

### One-time PyPI setup

Publishing uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so the repository
stores no PyPI token.

1. In the repository, create the environment `pypi` (Settings -> Environments). Adding required
   reviewers there makes every upload wait for a manual approval.
2. On PyPI, open Account -> Publishing and add a pending publisher for each of the ten projects:
   `eazy-sdk-core`, `eazy-sdk-accounts`, `eazy-sdk-adaptix`, `eazy-sdk-asyncapi`, `eazy-sdk-browser`,
   `eazy-sdk-html`, `eazy-sdk-openapi`, `eazy-sdk-presets`, `eazy-sdk-sqlmodel`, `eazy-sdk-xml`.
   Use the same values every time:

   | Field | Value |
   |---|---|
   | Owner | `0cherednoq` |
   | Repository name | `eazy-sdk` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

The core distribution is `eazy-sdk-core` because PyPI rejects `eazy-sdk` as too similar to the
unrelated `eazysdk` project; the import package stays `eazy_sdk`.

The first tagged release then creates the projects. The `pypi` and `github-release` jobs are
independent: if PyPI rejects an upload, the GitHub release is still created, and the `pypi` job can
be rerun because it skips files that are already uploaded.
