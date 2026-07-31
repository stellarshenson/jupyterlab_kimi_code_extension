# Acceptance Criteria - jupyterlab_kimi_code_extension

Kimi Code session manager for JupyterLab: side panel over `~/.kimi-code` storage, launching and managing real `kimi` CLI sessions in JupyterLab terminals. Port of `jupyterlab_claude_code_extension` adapted to kimi storage and CLI semantics.

## Contents

- [Session Discovery](#session-discovery)
- [Side Panel](#side-panel)
- [Launch and Resume](#launch-and-resume)
- [Terminal Reuse](#terminal-reuse)
- [YOLO Mode](#yolo-mode)
- [Branch Session (Fork)](#branch-session-fork)
- [Conversation Switcher and Manage Sessions](#conversation-switcher-and-manage-sessions)
- [Favorites](#favorites)
- [Remove and Cleanup](#remove-and-cleanup)
- [Coloured Terminal Tabs](#coloured-terminal-tabs)
- [Settings](#settings)
- [Statusline CLI](#statusline-cli)
- [Status and Auto-disable](#status-and-auto-disable)
- [Documented Deviations](#documented-deviations)
- [API](#api)

## Session Discovery

Server enumerates `~/.kimi-code` (override `KIMI_CODE_HOME`): `workspaces.json` gives canonical roots, session dirs `sessions/<wd_id>/session_<uuid>/` carry `state.json` + `agents/*/wire.jsonl`, `session_index.jsonl` maps ids.

- [x] **Workspace roots** - project_path taken from `workspaces.json` root, never decoded from dir name
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Deleted workspaces** - ids in `deleted_workspace_ids` never listed
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Corrupt state.json** - unreadable or invalid JSON session dir skipped, others still listed
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Row shape** - 10 fields: project_path, encoded_path, session_id, name, name_source, message_count, file_mtime, git_branch, favourite, extra_sessions
  - log: 2026-07-31 implemented (v0.1.1)
  - log: 2026-07-31 corrected after adversarial review (v0.1.3)
- [x] **Name source** - name_source `session` when isCustomTitle true, else `basename` of project folder
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Message count** - `context.append_message` lines counted across all `agents/*/wire.jsonl`
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Last activity** - file_mtime = max(parsed updatedAt, state.json st_mtime) in epoch ms; rows sorted desc
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Current session** - `.jl-current` pin wins when valid (charset + dir exists), else newest sibling by activity
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Git branch** - `git -C <root> branch --show-current` per unique root, 2s timeout, None on any failure
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Executor offload** - session scan runs off the IOLoop via run_in_executor
  - log: 2026-07-31 implemented (v0.1.1)

## Side Panel

Lumino widget docked via ILabShell, three sections, 30s poll, imperative DOM.

- [x] **Sections** - Favorites (only when non-empty), Recent (activity desc, capped by recentLimit), All (display name asc)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Collapse persist** - section expand state in localStorage `jupyterlab_kimi_code_extension:expanded`
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Scroll restore** - per-section scrollTop captured and restored across re-renders
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Disambiguation** - colliding folder names walked up the path until unique
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Poll** - refresh every 30s only while panel visible, skipped while context menu attached
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Spinner** - minimum 500 ms on manual refresh; launch spinner dismissed via dispose(), never resolve()
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fuzzy search** - funnel toggle, NFD + diacritic strip + case/space normalise, substring then Levenshtein; clear button appears once typing
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Activity column** - relative time (`now`, `Nm ago`, `Nh ago`, `Nd ago`); <60s emphasised in brand colour, >7d dimmed
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Tooltip** - name, path, last activity, message count, conversation count, git branch, session id
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Presentation modes** - `name` (session title or disambiguated folder) vs `path` (relative to server root)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Activation log** - exact string `JupyterLab extension jupyterlab_kimi_code_extension is activated!`
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: panel renders** - sidebar contains the panel with all three section headers after activation
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Launch and Resume

Server builds argv and spawns via terminado with a bash init-waiter that blocks kimi until the pty is sized.

- [x] **Resume argv** - `kimi -S session_<uuid>` with full prefixed id
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **New argv** - bare `kimi` (kimi assigns the id; session surfaces on next refresh)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **YOLO argv** - `--yolo` appended exactly once when requested
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Init waiter** - `_INIT_WAITER` waits for SIGWINCH/size change then execs kimi; terminal correctly sized at first frame
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Root containment** - project_path must resolve under realpath(root_dir) and exist, else 400 invalid_project_path
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Unknown keys** - launch body with keys outside {project_path, session_id, yolo} -> 400 invalid_body
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Session id charset** - launch session_id must match `^session_[0-9a-f-]{36}$`, else 400 invalid_session_id
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Pin clear on new** - new-session launch clears the workspace `.jl-current` pin
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Launch pin untouched on resume** - resume/fork launches do not modify pins
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Errors** - 503 kimi_not_found when CLI absent, 503 terminal_service_unavailable
  - log: 2026-07-31 implemented (v0.1.1)
  - log: 2026-07-31 corrected after adversarial review (v0.1.3)
- [x] **Galata: new session** - plus-menu item opens a terminal and `/api/terminals` lists it
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Terminal Reuse

Reuse only on positive session-id identity probed from the live pty process tree (DEF-4 port).

- [x] **Probe parse** - kimi argv parsed for `-S <id>`, `--session <id>`, `--session=<id>`; bare uuid normalised to `session_` prefix
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Unknown ids** - `-c` and bare kimi resolve to null session id and are never reused
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Flag swallowing** - `-S` followed by another flag yields null, never the flag as id
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Identity gate** - terminal reused only when probed session id equals the clicked row's session id; cwd never sufficient
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **In-memory cache** - per-project_path terminal cache reused only when cached sessionId matches the row
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Click coalescing** - concurrent clicks on one conversation coalesce into one launch/reuse
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Focus** - after launch/reuse the terminal tab activates and the term receives focus next frame
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: resume reuse** - clicking a resumed row twice yields one terminal (`/api/terminals` delta 0)
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## YOLO Mode

Claude's skip-permissions maps to kimi `--yolo` per design ruling.

- [x] **Menu variants** - header plus-menu: `New Kimi Session` and `New Kimi Session (YOLO)`; context menu: `Resume` and `Resume (YOLO)` with shield icon
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Setting default** - yoloMode true makes every launch carry `--yolo` without menu choice
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Branch YOLO** - branch-session has normal and YOLO variants
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: plus menu** - menu shows both New Kimi Session items exactly once each
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Branch Session (Fork)

Kimi has no fork CLI flag; the extension forks server-side by copying the session directory.

- [x] **Fork copy** - new `session_<uuid4>` dir is a full copy of the source session dir
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fork title** - named fork gets the exact name; unnamed gets `Fork of <source title>`; isCustomTitle true both ways
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fork timestamps** - copied state.json createdAt/updatedAt restamped to now; workDir and agents preserved
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Index append** - one line {sessionId, sessionDir, workDir} appended to session_index.jsonl
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fork pin** - `.jl-current` set to the new fork id
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fork errors** - 404 session_not_found, 400 invalid_encoded_path/invalid_body, 500 fork_failed; name capped at 120 chars, control chars rejected
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Fork launch** - frontend launches returned id with `kimi -S` immediately; no watcher/polling (fork is synchronous)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: branch flow** - branch menu item produces a second independent terminal (`/api/terminals` delta >= 2)
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Conversation Switcher and Manage Sessions

Right-click submenus over sibling sessions of one workspace; popup manages the full list.

- [x] **Switch submenu** - up to 5 most recent siblings, label = title (short id) - relative time, plus `Manage Sessions... (n)`
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Switch persist** - switch touches target state.json mtime and writes the pin; row updates on refresh
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Switch 404** - switching a vanished branch returns 404 branch_not_found, panel refreshes
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Open branch** - any sibling opens directly in its own terminal via `kimi -S <id>`
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Popup layout** - filter input, select-all checkbox, scrollable list, current pinned on top (badged, unselectable), footer selection counter
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Popup actions** - per-row Open button and copy-id button; delete moves selected to trash, announces count, re-syncs list
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Branch badge** - rows with siblings show branch icon + conversation count after the name
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: popup** - Manage Sessions popup lists >= 2 Open buttons and dismisses after opening one
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Favorites

Per-project star toggling persisted server-side.

- [x] **Toggle** - context menu label flips Add/Remove from Favorites; star icon shows on favourited rows (suppressed inside Favorites section)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Persistence** - favourites stored in `~/.kimi-code/jupyterlab_kimi_code_extension.json`, atomic tmp+replace write
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Optimistic update** - panel updates before the POST resolves, rolls back on failure
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Errors** - 400 invalid_json / invalid_body on malformed POST
  - log: 2026-07-31 implemented (v0.1.1)

## Remove and Cleanup

History disposal honours JupyterLab's delete_to_trash setting with send2trash and rmtree fallback.

- [x] **Remove** - disposes every session dir of the workspace and prunes its session_index.jsonl lines; workspaces.json untouched
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Remove confirm** - dialog names the project and warns it removes all its conversations before POST
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Cleanup** - keeps the current session, disposes siblings + their index lines; menu item visible only when extras exist, count in brackets
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Cleanup confirm** - dialog names project and count before POST; progress dialog during
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Delete branches** - explicit id list disposed, current always skipped, missing files skipped, count returned
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Trash fallback** - send2trash failure falls back to shutil.rmtree; delete_to_trash false deletes directly
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Traversal guards** - encoded_path containing `/`, `.`, `..` or escaping the sessions root -> 400
  - log: 2026-07-31 implemented (v0.1.1)

## Coloured Terminal Tabs

Tint derived deterministically from session id (kimi has no /color); companion extension supplies setColour.

- [x] **Hash palette** - FNV-1a 32-bit of session id mod 6 -> rose/peach/lemon/mint/sky/lavender; stable across calls
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Own conversation first** - tint comes from the terminal's own probed session id, never the clicked row (DEF-11 port)
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Cwd fallback** - null probed id falls back to longest-prefix project_path match among listed sessions
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Setting toggle** - colouredTabs false clears existing tints and stops applying new ones; default true
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Companion absent** - missing IColourfulTabs token makes all tint calls no-op, never throws
  - log: 2026-07-31 implemented (v0.1.1)

## Settings

Schema `jupyterlab_kimi_code_extension:plugin`, applied live.

- [x] **Five settings** - presentationMode, recentLimit, yoloMode, colouredTabs, sidebar; defaults name/10/false/true/right
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Live apply** - settings changes re-render or re-dock without reload
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Sidebar move** - sidebar change re-docks panel to the other area
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Restorer** - panel visibility persists across JupyterLab reloads
  - log: 2026-07-31 implemented (v0.1.1)

## Statusline CLI

`jupyterlab_kimi_code install-kimi-statusline` installs the bundled script and wires tui.toml.

- [x] **Install** - bundled statusline-command.sh copied to ~/.kimi-code, made executable, shebang verified before install
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **tui.toml merge** - `[status_line]` `command = "bash <dest>"` written preserving all other keys and comments
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Idempotent** - reinstall replaces the existing status_line command in place
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Prompt** - confirmation prompt by default; `--yes` skips; abort leaves files untouched
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Errors** - unreadable tui.toml or missing asset -> message + exit code 1
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Script segments** - context %, model, effort, git, env, pwd; never fails hard (set +e, `?` fallbacks, exit 0)
  - log: 2026-07-31 implemented (v0.1.1)

## Status and Auto-disable

- [x] **Status route** - {enabled, kimi_path, root_dir}; enabled = kimi found on PATH
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Gate** - enabled false -> panel never registered, extension activates silently
  - log: 2026-07-31 implemented (v0.1.1)
- [x] **Galata: activation** - activation console message present (panel registered or kimi-not-found path)
  - log: 2026-07-31 criterion added, ui-tests in flight
  - log: 2026-07-31 closed: ui-tests implemented and green (9 passed, failure capability proven by mutation)

## Documented Deviations

Kimi CLI lacks the claude primitives these reference features build on; the deviations are deliberate and covered by design ruling.

- [x] **No remote-control dot** - kimi has no remote-control bridge; indicator dropped, activity emphasis kept
  - log: 2026-07-31 decided (v0.1.1)
- [x] **No bg chip, no attach** - kimi background agents live inside sessions; resume never becomes Attach
  - log: 2026-07-31 decided (v0.1.1)
- [x] **Fork by copy** - coupled to kimi 0.31.0 session-dir format; format drift risk logged in defects.md watch items
  - log: 2026-07-31 decided (v0.1.1)
- [x] **Hash colours** - tint derived from session id, not user-set; stable per conversation
  - log: 2026-07-31 decided (v0.1.1)
- [x] **Skip-permissions = --yolo** - Colonel's ruling over --auto; `--yolo` auto-approves regular tool calls
  - log: 2026-07-31 decided (v0.1.1)

## API

All routes under `<baseUrl>/jupyterlab-kimi-code-extension/`, `@authenticated`.

- `GET status` -> `{enabled, kimi_path, root_dir}`
- `GET sessions` -> `{sessions: [ISession]}` (executor)
- `POST sessions/favourite` body `{project_path, favourite}` -> `{favourites: [paths]}`; 400 invalid_json/invalid_body
- `POST sessions/remove` body `{encoded_path}` -> `{removed}`; 400 invalid_json/invalid_body/remove_failed
- `POST sessions/cleanup` body `{encoded_path}` -> `{removed_count}`; 400 invalid_json/invalid_body/cleanup_failed
- `GET sessions/branches?encoded_path=` -> `{current, branches: [{session_id, file_mtime, label}]}`; 400 invalid_encoded_path
- `POST sessions/switch` body `{encoded_path, session_id}` -> `{requested, current}`; 404 branch_not_found, 400 invalid_body
- `POST sessions/delete-branches` body `{encoded_path, session_ids: []}` -> `{removed_count}`; 400 invalid_json/invalid_body
- `POST sessions/fork` body `{encoded_path, session_id, name?}` -> `{session_id, forked_from}`; 404 session_not_found, 400 invalid_encoded_path/invalid_body, 500 fork_failed
- `GET terminal-cwd/<name>` -> `{terminal_name, cwds, has_kimi, session_id|null}`; 503 no terminal service, 404 not found, 500 no pty
- `POST launch-terminal` body `{project_path, session_id?, yolo?}` -> `{terminal_name}`; 400 invalid_project_path/invalid_session_id/invalid_body, 503 kimi_not_found/terminal_service_unavailable
