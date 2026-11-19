# AGENTS.md: instructions for coding agents other than Claude (e.g. Codex)

Read `CLAUDE.md` first: its architecture, coding conventions and data-protection rules apply to you in
full. This file adds the limits for agents that work on code only.

## Hard limits
- **Never read game data.** Do not open, list, hash or run RRL against anything outside this repository:
  in particular `~/RRL-Workspace`, `~/Coding/halo2`, any `.iso`/`.xiso`/`.map`/`.xbe`, emulators in
  `~/Applications`, or the xemu config. Proprietary data must never reach an AI service (CLAUDE.md rule 7).
- **Tests use synthetic fixtures only** (`tests/fixtures/`). Never add binary game data, extracted
  assets, BIOS/firmware or keys. The `scripts/check_no_game_data.py` hook must pass.
- **Work on your own branch** named `codex/<task-id>` (see `documentation/HANDOFF.md`). Never commit to
  `master`, never push, never rewrite history. The lead (Claude) reviews and merges.
- Touch only the files your task brief lists. If something else needs changing, say so in your report.
- Do not install system software. Python dependencies only through `uv` and `pyproject.toml`, and only
  when the brief allows it.

## Before you finish
Run and report the results of:
`uv sync --all-extras`, `uv run pytest -q`, `uv run ruff check src tests scripts`,
`uv run ruff format --check src tests scripts`, `uv run mypy`,
`uv run python scripts/check_no_game_data.py $(git ls-files -mo --exclude-standard)`.

Then add a short entry at the top of `documentation/DEVELOPMENT_LOG.md` (newest first, same style)
naming the task id, what changed, the test results and open problems, and end your reply with:
changed files, decisions, tests run, anything you could not verify.
