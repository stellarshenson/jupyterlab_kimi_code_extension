<!-- @import /home/lab/.claude/CLAUDE.md -->
<!-- @import /home/lab/workspace/.claude/CLAUDE.md -->

# Project-Specific Configuration

This file is a thin overlay. It imports both configuration layers by reference and duplicates none of their content:

- User-level (global, applies to every project on this machine): `/home/lab/.claude/CLAUDE.md`
- Workspace-level (applies under `/home/lab/workspace`): `/home/lab/workspace/.claude/CLAUDE.md`

All rules from both layers apply. Project-specific rules below strengthen or extend them.

## Mandatory Bans (Reinforced)

The following workspace rules are STRICTLY ENFORCED for this project:

- **No automatic git tags** - only create tags when user explicitly requests
- **No automatic version changes** - only modify version in package.json/pyproject.toml/etc. when user explicitly requests
- **No automatic publishing** - never run `make publish`, `npm publish`, `twine upload`, or similar without explicit user request
- **No manual package installs if Makefile exists** - use `make install` or equivalent Makefile targets, not direct `pip install`/`uv install`/`npm install`
- **No automatic git commits or pushes** - only when user explicitly requests

## Project Context

- JupyterLab 4 frontend-and-server extension for the Kimi Code CLI (Moonshot AI) - session management, resume, and navigation, companion to `jupyterlab_claude_code_extension`
- Python package `jupyterlab_kimi_code_extension` (server extension) + npm package `jupyterlab_kimi_code_extension` (frontend extension), generated from the JupyterLab copier extension template v4.6.3
- Stack: TypeScript frontend (`src/`), Python server routes (`jupyterlab_kimi_code_extension/`), jest + pytest unit tests, Playwright ui-tests, jupyter-releaser CI/CD in `.github/workflows/`
- Status: template scaffold only - extension features are not implemented yet and land in future sessions on explicit order

## Build Lifecycle (Makefile Only)

**MANDATORY**: Always use the project Makefile for the entire build lifecycle - never run pip/jlpm/yarn/npm, build, publish, or clean commands directly:

- `make install` to build and install
- `make publish` to release
- `make clean` to clean build artefacts
- `make mrproper` to remove all build and venv artefacts

**MANDATORY**: Always check the local Makefile version against `private/jupyterlab/@utils/jupyterlab-extensions/Makefile` and update the local Makefile as soon as a newer version is found. Local and canonical are both at version 1.36 as of 2026-07-30.

## Git Rules (Project-Specific)

- Always commit both `package.json` and `package-lock.json` together

## Feature Quality Tracking

- Create and maintain acceptance criteria for every feature using the `/acceptance-criteria` skill (`docs/acc-crit-*.md`)
- Track defects using the `/defects-tracking` skill (`docs/defects.md`)

## Journal Rules (Project-Specific)

- **APPEND ONLY**: New journal entries MUST be appended at the end of the file, never inserted between existing entries
- Entries maintain strict chronological order by position - the last entry in the file is always the most recent work
- Never reorder, move, or insert entries out of sequence
- The Stellars **journal plugin** is the canonical tool for this file: create via `/journal:create`, append via `/journal:update`, archive via `/journal:archive`. The `journal:journal` skill auto-triggers on any mention of "journal" and runs `journal-tools check` after every write
- Direct edits to `JOURNAL.md` are a last resort - prefer the plugin so modus secundis format, continuous numbering and append-only order are enforced automatically

## Required Workspace Skills

Skills that MUST be used when working on this project:

- **jupyterlab-extension** - extension development guidelines, CI/CD, caveats (resolves at user level: `/home/lab/.claude/skills/jupyterlab-extension`)
- **playwright** - browser automation for screenshots and UI verification (not present under `/home/lab/workspace/.claude/skills/` as of 2026-07-30; browser automation is currently provided by the user-level `my-browser` skill, which drives the Playwright MCP)
