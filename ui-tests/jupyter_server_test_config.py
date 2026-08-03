"""Server configuration for integration tests.

!! Never use this configuration in production because it
opens the server to the world and provide access to JupyterLab
JavaScript objects through the global window variable.
"""
import json
import os
import stat
import tempfile
import time
import uuid
from datetime import datetime, timezone

from jupyterlab.galata import configure_jupyter_server

# Isolated HOME so the panel reads a seeded ``~/.kimi-code`` instead of the
# developer's real one. Set before anything reads HOME. ``KIMI_CODE_HOME``
# pins the storage root explicitly as well (sessions.py honours it first).
_home = tempfile.mkdtemp(prefix="fake-home-")
os.environ["HOME"] = _home
_kimi_root = os.path.join(_home, ".kimi-code")
os.environ["KIMI_CODE_HOME"] = _kimi_root

# Provide a fake ``kimi`` binary on PATH so the status endpoint enables the
# panel (CI runners do not have the real CLI) and the launch-terminal flow
# can spawn a real pty. The long sleep keeps every launched terminal alive
# for the whole suite - a terminal exiting mid-run would make the
# ``/api/terminals`` count assertions flake.
_fake_bin = tempfile.mkdtemp(prefix="fake-kimi-")
_kimi_bin = os.path.join(_fake_bin, "kimi")
with open(_kimi_bin, "w") as f:
    f.write("#!/bin/sh\necho fake kimi running\nsleep 6000\n")
os.chmod(
    _kimi_bin, os.stat(_kimi_bin).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
)
os.environ["PATH"] = _fake_bin + os.pathsep + os.environ.get("PATH", "")

# Seed one workspace ("kimiproj") with three parallel conversations so the
# branch UI (Open Branched Conversation / Switch and Manage Sessions / fork)
# has something to act on, using the server's own storage layout (see
# sessions.py): ``workspaces.json`` is the canonical registry mapping a
# ``wd_id`` to its root folder, and ``sessions/<wd_id>/session_<uuid>/``
# holds one conversation each. The workspace root is a REAL directory under
# the served root so launch-terminal's root-containment check passes.
_project_root = os.path.join(_home, "kimiproj")
os.makedirs(_project_root, exist_ok=True)
_wd_id = "wd_test_" + uuid.uuid4().hex[:12]

os.makedirs(_kimi_root, exist_ok=True)
with open(os.path.join(_kimi_root, "workspaces.json"), "w") as f:
    json.dump(
        {
            "workspaces": {_wd_id: {"root": _project_root, "name": "kimiproj"}},
            "deleted_workspace_ids": [],
        },
        f,
    )


def _iso_z(epoch: float) -> str:
    """Kimi's own state.json timestamp format (ISO-8601 ms, Z suffix)."""
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


_wd_dir = os.path.join(_kimi_root, "sessions", _wd_id)
_now = time.time()
_index_lines = []
for _i in range(3):
    _sid = f"session_{uuid.uuid4()}"
    _sdir = os.path.join(_wd_dir, _sid)
    _agents = os.path.join(_sdir, "agents", "main")
    os.makedirs(_agents, exist_ok=True)
    # Ascending recent times: the newest session resolves as the workspace's
    # current conversation (recency - no pin is seeded), the other two are
    # its branches.
    _t = _now - 30 + _i * 10
    # Session 0 carries a long auto-generated title. Kimi titles a session
    # from its first prompt whenever the user has not renamed it, so a title
    # running to a full paragraph is the norm rather than an edge case -
    # seeding only short ones let DEF-18 (uncapped menu labels stretching the
    # branch submenu across the window) pass nine flows unnoticed.
    if _i == 0:
        _title = (
            "List ONLY the names of the User-scope skills available to you "
            "(from the skills section of your system context), one per line, "
            "no other text."
        )
    elif _i == 1:
        # Wide-script title. Kimi is Moonshot AI's CLI, so a Chinese auto-title
        # is an expected case - and a character-counting cap does not bound it:
        # 60 Han glyphs measured 851-862px, at or above the 850px that filed
        # DEF-18. Without this fixture both DEF-18 tests stay green while the
        # panel is broken, because an untruncated title emits no ellipsis.
        _title = "请仔细阅读并总结这个项目的架构设计文档" * 4
    else:
        _title = f"Conversation {_i}"
    _state_path = os.path.join(_sdir, "state.json")
    with open(_state_path, "w") as f:
        json.dump(
            {
                "title": _title,
                "isCustomTitle": False,
                "workDir": _project_root,
                "createdAt": _iso_z(_t - 60),
                "updatedAt": _iso_z(_t),
                "lastPrompt": f"prompt {_i} for kimiproj",
            },
            f,
            indent=2,
        )
    # wire.jsonl: the compact separators are load-bearing - the server
    # matches '"type":"turn.prompt"' and '"type":"context.append_message"'
    # as raw substrings, which json.dumps' default ", " / ": " separators
    # never produce.
    _events = [
        {"type": "metadata", "sessionId": _sid, "workDir": _project_root},
        {
            "type": "turn.prompt",
            "input": [{"type": "text", "text": f"prompt {_i} for kimiproj"}],
        },
        {
            "type": "context.append_message",
            "message": {"role": "user", "content": f"prompt {_i} for kimiproj"},
        },
        {
            "type": "context.append_message",
            "message": {"role": "assistant", "content": "ack"},
        },
    ]
    with open(os.path.join(_agents, "wire.jsonl"), "w") as f:
        for _ev in _events:
            f.write(json.dumps(_ev, separators=(",", ":")) + "\n")
    # Align the state.json mtime with updatedAt so the activity ordering
    # (_session_activity takes the max of the two) is deterministic.
    os.utime(_state_path, (_t, _t))
    _index_lines.append(
        {"sessionId": _sid, "sessionDir": _sdir, "workDir": _project_root}
    )

with open(os.path.join(_kimi_root, "session_index.jsonl"), "w") as f:
    for _line in _index_lines:
        f.write(json.dumps(_line, separators=(",", ":")) + "\n")

configure_jupyter_server(c)

# Serve the fake home instead of galata's own mkdtemp root: launch-terminal
# refuses to spawn outside the served root, so the seeded workspace root
# must sit under it.
c.ServerApp.root_dir = _home

# Uncomment to set server log level to debug level
# c.ServerApp.log_level = "DEBUG"
