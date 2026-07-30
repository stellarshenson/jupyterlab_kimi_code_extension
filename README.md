# jupyterlab_kimi_code_extension

[![GitHub Actions](https://github.com/stellarshenson/jupyterlab_kimi_code_extension/actions/workflows/build.yml/badge.svg)](https://github.com/stellarshenson/jupyterlab_kimi_code_extension/actions/workflows/build.yml)
[![npm version](https://img.shields.io/npm/v/jupyterlab_kimi_code_extension.svg)](https://www.npmjs.com/package/jupyterlab_kimi_code_extension)
[![PyPI version](https://img.shields.io/pypi/v/jupyterlab_kimi_code_extension.svg)](https://pypi.org/project/jupyterlab_kimi_code_extension/)
[![Total PyPI downloads](https://static.pepy.tech/badge/jupyterlab_kimi_code_extension)](https://pepy.tech/project/jupyterlab_kimi_code_extension)
[![JupyterLab 4](https://img.shields.io/badge/JupyterLab-4-orange.svg)](https://jupyterlab.readthedocs.io/en/stable/)
[![Brought To You By KOLOMOLO](https://img.shields.io/badge/Brought%20To%20You%20By-KOLOMOLO-00ffff?style=flat)](https://kolomolo.com)
[![Donate PayPal](https://img.shields.io/badge/Donate-PayPal-blue?style=flat)](https://www.paypal.com/donate/?hosted_button_id=B4KPBJDLLXTSA)

A Kimi Code launcher and manager for JupyterLab. Start, resume, and switch Kimi Code CLI sessions from a side panel - one click lands you in the right terminal with Kimi already running, no duplicate tabs, no session-id hunting, with a live indicator showing which sessions are active right now. Companion to [jupyterlab_claude_code_extension](https://github.com/stellarshenson/jupyterlab_claude_code_extension), built to the same design.

## Features

- **Side panel** - every Kimi Code project in one view: favourites, search, live activity
- **One-click resume** - click a row to jump back into that session in a terminal; an already-open terminal for the project is reused instead of duplicated
- **Session management** - switch, fork, and clean up parallel conversations without `--resume` pickers or raw session ids
- **Activity at a glance** - last-activity column and a live indicator marking sessions running right now
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

## Uninstall

```bash
pip uninstall jupyterlab_kimi_code_extension
```
