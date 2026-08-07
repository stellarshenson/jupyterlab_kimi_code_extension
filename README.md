# jupyterlab_kimi_code_extension

> [!IMPORTANT]
> **Superseded.** This extension is retired and no longer maintained. Its functionality lives on in
> [jupyterlab_ai_code_assistants_extension](https://github.com/stellarshenson/jupyterlab_ai_code_assistants_extension),
> which consolidates the Claude Code, Codex, Kimi and Gemini side panels behind one provider registry
> and migrates settings and favourites from this extension automatically.

[![GitHub Actions](https://github.com/stellarshenson/jupyterlab_kimi_code_extension/actions/workflows/build.yml/badge.svg)](https://github.com/stellarshenson/jupyterlab_kimi_code_extension/actions/workflows/build.yml)
[![npm version](https://img.shields.io/npm/v/jupyterlab_kimi_code_extension.svg)](https://www.npmjs.com/package/jupyterlab_kimi_code_extension)
[![PyPI version](https://img.shields.io/pypi/v/jupyterlab_kimi_code_extension.svg)](https://pypi.org/project/jupyterlab_kimi_code_extension/)
[![Total PyPI downloads](https://static.pepy.tech/badge/jupyterlab_kimi_code_extension)](https://pepy.tech/project/jupyterlab_kimi_code_extension)
[![JupyterLab 4](https://img.shields.io/badge/JupyterLab-4-orange.svg)](https://jupyterlab.readthedocs.io/en/stable/)
[![Brought To You By KOLOMOLO](https://img.shields.io/badge/Brought%20To%20You%20By-KOLOMOLO-00ffff?style=flat)](https://kolomolo.com)
[![Donate PayPal](https://img.shields.io/badge/Donate-PayPal-blue?style=flat)](https://www.paypal.com/donate/?hosted_button_id=B4KPBJDLLXTSA)

A Kimi Code launcher and manager for JupyterLab. Start, resume, fork, switch, and clean up Kimi Code CLI sessions from a side panel - one click lands you in the right terminal with Kimi already running, no duplicate tabs, no session-id hunting. Companion to [jupyterlab_claude_code_extension](https://github.com/stellarshenson/jupyterlab_claude_code_extension), built to the same design.

## Why this extension

One principle: **Moonshot knows best how to build the agent harness; we know best how to make it work in JupyterLab.**

Chat-panel extensions re-implement the agent loop and trail the real tool. This one runs the genuine, unmodified Kimi Code CLI in JupyterLab terminals - skills, subagents, MCP, every release the day it lands. The extension owns the JupyterLab side:

- **Launching** - new or resumed sessions, normal or YOLO mode, no wrapper shell, correctly sized before Kimi draws its first frame
- **Finding** - every Kimi project in one panel: favourites, search, live activity
- **Reusing** - clicking a session focuses its existing terminal, never a duplicate
- **Managing** - parallel conversations: switch, fork with a name, delete - no `-S` pickers, no raw UUIDs

## Features

- **Three-section side panel** - Favorites, Recent, and All projects, each scrolling independently
- **One-click resume** - click a row to jump back into that session in a terminal (`kimi -S <session-id>`). If a terminal already runs that exact conversation, it's reused instead of duplicated
- **YOLO mode** - every launch action has a `(YOLO)` variant that starts Kimi with `--yolo`, auto-approving regular tool calls; a `yoloMode` setting makes it the default
- **Branch session** - fork the current conversation into a new named session via the right-click menu (normal or YOLO). Kimi has no fork CLI flag, so the extension forks server-side: the session directory is copied with a fresh id and title, and the fork opens in a new terminal with `kimi -S`
- **Conversation switcher** - a right-click "Switch and Manage Sessions" submenu lists a project's parallel conversations by title and short session id with last-activity time; pick one and it becomes the row's current conversation. "Manage Sessions..." opens a searchable popup - scrollable table, current conversation pinned at top, multi-select delete to trash
- **Open branched conversation** - open any parallel conversation directly in its own terminal, so several branches of one project run side by side
- **Copy session id** - right-click copies the row's current conversation id to the clipboard; the Manage Sessions popup adds a copy button on every row
- **Favorites** - star projects you keep coming back to via the right-click menu
- **Remove and clean up** - drop a project's Kimi history (moved to trash, honouring JupyterLab's "move files to trash" setting), or clean up parallel sessions keeping only the main one; both ask for confirmation first
- **Coloured terminal tabs** - each conversation's terminal tab is tinted with a colour derived deterministically from its session id (Kimi has no `/color` command, so the tint is stable per conversation instead of user-set); needs the companion `jupyterlab_colourful_tab_extension` (installed automatically). On by default; turn it off with the "Coloured terminal tabs" setting
- **Activity at a glance** - each row shows its last activity (`now`, `5m ago`, `2h ago`, `3d ago`) in an aligned column; rows active within the last minute light up in the theme's brand colour, rows idle for over a week dim
- **Search** - fuzzy filter toggled by the funnel button next to refresh, with a clear button
- **Presentation modes** - label rows by name (the session title Kimi records, falling back to the folder name) or by path relative to the JupyterLab root
- **Hover tooltip** with project path, last activity, message count, conversation count, git branch, and session id
- **Auto-disabled** when the Kimi Code CLI is not installed

## Requirements

- JupyterLab >= 4.0.0
- Python >= 3.10
- `kimi` CLI on `PATH`

## Install

Developers must install via the project `Makefile` (which orchestrates clean, build, and pip install of the resulting wheel):

```bash
make install
```

End-users can install the published package from PyPI:

```bash
pip install jupyterlab_kimi_code_extension
```

> [!WARNING]
> `package.json` pins `webpack: 5.106.0` and `chalk: 4.1.2` in both `resolutions` and `overrides`. Do not remove these. webpack `>= 5.106.1` changed its module-federation share identifier format and crashes the unmaintained `license-webpack-plugin` (`split('=')[1].trim()`) that `@jupyterlab/builder` injects into every production build; the duplicate `chalk@2.4.2` pulled by `duplicate-package-checker-webpack-plugin` crashes on Node 24+ in the build-isolation install. Without the pins, `make publish` and CI fail on `python -m build`.

## Kimi statusline

The package ships a companion CLI that installs a powerline-style status line (context %, model, effort, git, env, pwd) into `~/.kimi-code` and points `status_line.command` in `tui.toml` at it - after asking for confirmation:

```bash
jupyterlab_kimi_code install-kimi-statusline
```

## Uninstall

```bash
pip uninstall jupyterlab_kimi_code_extension
```
