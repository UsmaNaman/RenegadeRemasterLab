# Contributing to RRL

Thanks for helping. Read [LEGAL.md](LEGAL.md) and the [Code of Conduct](CODE_OF_CONDUCT.md) first.

## The absolute rule

Never commit, attach, paste or link to copyrighted game data, keys, BIOS/firmware dumps,
or sites that host them. That includes test fixtures: tests build tiny synthetic byte blobs
at run time. Pull requests or issues that break this rule are closed, and the content removed.
A pre-commit hook and CI (`scripts/check_no_game_data.py`) block disc images, console
executables, known magic bytes and files over 1 MiB.

## Development setup

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.13 if needed.

```sh
uv sync                        # .venv with RRL and dev tools
uv run pre-commit install      # run the hooks on every commit
```

## Checks (CI runs all of these)

```sh
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
uv run pre-commit run --all-files
```

## Sign your commits (DCO)

RRL uses the [Developer Certificate of Origin](https://developercertificate.org/), not a CLA.
Sign off every commit to certify you wrote it or have the right to submit it:

```sh
git commit -s -m "Add XDVDFS directory walker"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line. Use your real name.

## Writing an adapter

Adapters teach RRL about a platform or game. Import only from `rrl.api`; everything else is
internal. An adapter is a class with `id`, `name`, `version`, `api_version` (= `rrl.api.API_VERSION`)
and `capabilities` class attributes, plus `identify(source) -> Match | None`. Declare only
capabilities you implement: RRL checks them at load time and disables adapters that lie.

Register it in your package's `pyproject.toml` (RRL instantiates the class with no arguments):

```toml
[project.entry-points."rrl.adapters"]
my_adapter = "my_package.adapter:MyAdapter"
```

`tests/contract` checks every installed adapter. Test `identify` with synthetic headers only.

## Publishing

Set `git config rrl.publishIdentity 'Your Name <your-id@users.noreply.github.com>'`.
From a clean checkout, run `python scripts/publish.py --dry-run` to preview the squash and audit.
Run again with `--message "Release summary"` to prepare the local `release` branch.
Add `--push` to update public `main`. With `--tag vX.Y.Z`, the script also creates an
annotated tag and includes that tag in the same push. Keep the private history on this machine.

To enable the tracked identity guard, run `git config core.hooksPath .githooks`. This selects
that directory for **all** hooks. If you use pre-commit, run `uv run pre-commit run --all-files`
before committing or arrange for both hooks to run.
