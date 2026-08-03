# Changelog

<!-- <START NEW CHANGELOG ENTRY> -->

## [0.7.8] - 2026-08-03

First public release. A JupyterLab 4 side panel for the Kimi Code CLI
(Moonshot AI): browse, resume and manage conversations without leaving the
notebook interface.

### Added

- Sessions panel listing every Kimi workspace from `~/.kimi-code` under Favorites, Recent and All, with live activity indicators, message counts and git branch
- Resume in a terminal, reusing an existing tab when it is already running that conversation - matched on session identity, never on working directory
- Branch (fork) a conversation: kimi has no fork flag, so the server copies the session directory, appends `session_index.jsonl` and launches `kimi -S` on the new id
- Switch and Manage Sessions submenus over sibling conversations, plus a popup managing the full list with multi-select delete
- YOLO launches mapping to kimi's `--yolo`, offered alongside every normal launch
- Favourites, per-workspace current-conversation pins, trash-backed removal and parallel-session cleanup
- Coloured terminal tabs, tinted by a deterministic hash of the session id
- Bundled statusline installer (`jupyterlab_kimi_code`) that merges a `status_line` command into kimi's `tui.toml`
- Settings for presentation mode, recent limit, YOLO default, coloured tabs and sidebar placement

### Fixed

- Branch submenu labels are capped at 60 display columns, so a long auto-generated title cannot stretch the menu across the window; wide scripts and emoji count double, and the cut lands on a code-point boundary
- Manage Sessions popup is width-bounded so its label ellipsis engages instead of the dialog growing past the viewport
- Terminal reuse, focus restore, refresh deferral and cleanup/delete status reporting all hardened across five rounds of adversarial review

<!-- <END NEW CHANGELOG ENTRY> -->
