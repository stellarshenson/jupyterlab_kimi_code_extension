"""Shared pytest fixtures for the backend tests."""
from __future__ import annotations

import pytest

from jupyterlab_kimi_code_extension import sessions as sessions_mod


@pytest.fixture(autouse=True)
def no_git_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to "no git branch is resolvable".

    ``list_sessions`` shells out to ``git -C <root> branch --show-current``
    once per unique workspace root. Left unstubbed, every listing test would
    spawn a real git subprocess against fixture paths that do not exist -
    slow, and noisy. Tests that care about the git-branch path override this
    by patching ``sessions_mod._git_branch`` themselves, and the direct tests
    of ``_git_branch`` hold the original function via an import binding.
    """
    monkeypatch.setattr(sessions_mod, "_git_branch", lambda project_path: None)
