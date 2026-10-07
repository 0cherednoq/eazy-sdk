# Contributing

Eazy SDK requires Python 3.13 or newer and [uv](https://docs.astral.sh/uv/). Browser tests need
Chromium: `uv run playwright install chromium`.

Install the workspace:

```bash
uv sync --all-extras
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

A release is one annotated tag. `release.yml` builds the single distribution `eazy-sdk-core`, which
ships the core and every integration under `plugins/`, audits it, uploads it to PyPI and attaches
the same files to a GitHub release.

1. Set the version in `pyproject.toml`, `eazy_sdk/__init__.py` and
   `plugins/browser/eazy_sdk_browser/__init__.py`, then update `CHANGELOG.md`.
2. Run the gates listed above and merge to `master`.
3. Create an annotated tag whose body is the release notes, and push it:

   ```bash
   git tag -a v0.2.0a9 -m "Eazy SDK 0.2.0a9" -m "Release notes go here."
   git push origin v0.2.0a9
   ```

The tag must equal `v` plus the project version, otherwise the build job stops.

### One-time PyPI setup

Publishing uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so the repository
stores no PyPI token.

1. In the repository, create the environment `pypi` (Settings -> Environments). Adding required
   reviewers there makes every upload wait for a manual approval.
2. On PyPI, open Account -> Publishing and add a pending publisher for the project
   `eazy-sdk-core`: owner `0cherednoq`, repository `eazy-sdk`, workflow `release.yml`,
   environment `pypi`.

The distribution is `eazy-sdk-core` because PyPI rejects `eazy-sdk` as too similar to the unrelated
`eazysdk` project; the import package stays `eazy_sdk`. Integrations are not separate PyPI
projects: an extra such as `eazy-sdk-core[html]` installs only the third-party libraries the
integration needs.

The first tagged release creates the project. The `pypi` and `github-release` jobs are
independent: if PyPI rejects the upload, the GitHub release is still created, and the `pypi` job
can be rerun because it skips files that are already uploaded.
