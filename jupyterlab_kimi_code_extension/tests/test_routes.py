"""Tests for the sessions backend (routes.py + sessions.py)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest
import send2trash

from jupyterlab_kimi_code_extension import routes as routes_mod
from jupyterlab_kimi_code_extension import sessions as sessions_mod
# Bound at import time, so it survives the autouse stub that rebinds
# ``sessions_mod._git_branch`` for every other test (see conftest.py).
from jupyterlab_kimi_code_extension.sessions import _git_branch as real_git_branch


# ---------------------------------------------------------------------------
# Fixture data - a minimal ``~/.kimi-code`` tree
# ---------------------------------------------------------------------------

WD_A = "wd_projA_aaaaaaaaaaaa"
WD_B = "wd_projB_bbbbbbbbbbbb"
WD_C = "wd_projC_cccccccccccc"
WD_DEL = "wd_gone_dddddddddddd"
WD_BR = "wd_branchy_eeeeeeeeeeee"

ROOT_A = "/home/user/projA"
ROOT_B = "/home/user/projB"
ROOT_C = "/home/user/projC"
ROOT_BR = "/home/user/branchy"

SID_A = "session_aaaaaaaa-0000-4000-8000-000000000001"
SID_B_OLD = "session_bbbbbbbb-0000-4000-8000-000000000001"
SID_B_NEW = "session_bbbbbbbb-0000-4000-8000-000000000002"
SID_C = "session_cccccccc-0000-4000-8000-000000000001"
SID_DEL = "session_dddddddd-0000-4000-8000-000000000001"
# Valid-charset id that never exists on disk.
SID_GONE = "session_00000000-0000-4000-8000-00000000dead"

# Explicit state.json mtimes (epoch seconds). All larger than the 2020-era
# ISO timestamps recorded inside state.json (~1.578e9 s), so the file mtime
# is what orders sessions and rows - exactly the knob the tests turn.
MT_C = 1_700_000_500
MT_B_OLD = 1_700_001_000
MT_B_NEW = 1_700_002_000
MT_A = 1_700_003_000

CREATED_ISO = "2020-01-01T00:00:00.000Z"
UPDATED_ISO = "2020-01-02T00:00:00.000Z"

ROW_KEYS = {
    "project_path", "encoded_path", "session_id", "name", "name_source",
    "message_count", "file_mtime", "git_branch", "favourite",
    "extra_sessions",
}


def _compact(record: dict) -> str:
    """Wire-log line in Kimi's compact JSON form - the substring scans in
    ``sessions.py`` (``"type":"turn.prompt"`` / ``"type":"context.append_message"``)
    only match separator-free JSON."""
    return json.dumps(record, separators=(",", ":"))


def _make_session(
    wd_dir: Path,
    sid: str,
    *,
    title: str = "",
    custom: bool = False,
    created: str = CREATED_ISO,
    updated: str = UPDATED_ISO,
    work_dir: str = "/home/user/projX",
    prompts: tuple = (),
    messages: dict | None = None,
    mtime: int | None = None,
) -> Path:
    """Create ``wd_dir/<sid>/`` with a state.json and agent wire logs.

    ``messages`` maps agent name -> count of ``context.append_message``
    lines in that agent's ``wire.jsonl``; ``prompts`` become ``turn.prompt``
    lines in the main agent's log.
    """
    sdir = wd_dir / sid
    main_dir = sdir / "agents" / "main"
    main_dir.mkdir(parents=True)
    state = {
        "createdAt": created,
        "updatedAt": updated,
        "title": title,
        "isCustomTitle": custom,
        "workDir": work_dir,
        "lastPrompt": prompts[-1] if prompts else "",
    }
    (sdir / "state.json").write_text(json.dumps(state, indent=2))
    counts = dict(messages or {})
    lines = [_compact({"type": "metadata", "sessionId": sid})]
    for prompt in prompts:
        lines.append(_compact(
            {"type": "turn.prompt", "input": [{"type": "text", "text": prompt}]}
        ))
    for _ in range(counts.pop("main", 0)):
        lines.append(_compact(
            {"type": "context.append_message", "message": {"role": "user"}}
        ))
    (main_dir / "wire.jsonl").write_text("\n".join(lines) + "\n")
    for agent, count in counts.items():
        adir = sdir / "agents" / agent
        adir.mkdir(parents=True)
        agent_lines = [
            _compact({"type": "context.append_message", "message": {}})
        ] * count
        (adir / "wire.jsonl").write_text("\n".join(agent_lines) + "\n")
    if mtime is not None:
        os.utime(sdir / "state.json", (mtime, mtime))
    return sdir


def _append_index_line(root: Path, sid: str, sdir: Path, work_dir: str) -> None:
    with (root / "session_index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(_compact({
            "sessionId": sid, "sessionDir": str(sdir), "workDir": work_dir,
        }) + "\n")


def _register_workspace(root: Path, wd_id: str, project_path: str) -> None:
    wf = root / "workspaces.json"
    data = json.loads(wf.read_text()) if wf.exists() else {"workspaces": {}}
    data.setdefault("workspaces", {})[wd_id] = {
        "root": project_path,
        "name": os.path.basename(project_path),
    }
    wf.write_text(json.dumps(data))


def _index_sids(root: Path) -> list[str]:
    sids = []
    for line in (root / "session_index.jsonl").read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("sessionId"), str):
            sids.append(record["sessionId"])
    return sids


@pytest.fixture
def fake_kimi_home(tmp_path: Path) -> Path:
    """A minimal ``~/.kimi-code`` tree with three live workspaces.

    A: one session with index lines and both prompt and message events.
    B: two sibling sessions at explicit ascending mtimes (the newer wins).
    C: one bare session with no ``session_index.jsonl`` lines.
    Plus one workspace listed in ``deleted_workspace_ids`` whose session dir
    still exists on disk - it must never surface.
    """
    root = tmp_path / ".kimi-code"
    sessions = root / "sessions"
    sessions.mkdir(parents=True)

    (root / "workspaces.json").write_text(json.dumps({
        "workspaces": {
            WD_A: {"root": ROOT_A, "name": "projA"},
            WD_B: {"root": ROOT_B, "name": "projB"},
            WD_C: {"root": ROOT_C, "name": "projC"},
            WD_DEL: {"root": "/home/user/gone", "name": "gone"},
        },
        "deleted_workspace_ids": [WD_DEL],
    }))

    a_dir = _make_session(
        sessions / WD_A, SID_A,
        title="Refactor the parser", work_dir=ROOT_A,
        prompts=("hello A",), messages={"main": 2, "subagent-1": 3},
        mtime=MT_A,
    )
    b_old = _make_session(
        sessions / WD_B, SID_B_OLD,
        title="Older conversation", work_dir=ROOT_B,
        prompts=("old start",), messages={"main": 1}, mtime=MT_B_OLD,
    )
    b_new = _make_session(
        sessions / WD_B, SID_B_NEW,
        title="Newer conversation", work_dir=ROOT_B,
        prompts=("new start",), messages={"main": 9}, mtime=MT_B_NEW,
    )
    _make_session(sessions / WD_C, SID_C, work_dir=ROOT_C, mtime=MT_C)
    _make_session(
        sessions / WD_DEL, SID_DEL, work_dir="/home/user/gone", mtime=MT_A,
    )

    _append_index_line(root, SID_A, a_dir, ROOT_A)
    _append_index_line(root, SID_B_OLD, b_old, ROOT_B)
    _append_index_line(root, SID_B_NEW, b_new, ROOT_B)
    return root


def _bsid(i: int) -> str:
    """Deterministic session id for branch-test session ``i``."""
    return f"session_{i:08x}-0000-4000-8000-000000000000"


def _make_branch_workspace(root: Path, n: int) -> Path:
    """Register a workspace with ``n`` sessions at distinct ascending mtimes.

    ``_bsid(n-1)`` is the newest (the current session); index lines are
    appended for every session so the prune assertions have material.
    """
    _register_workspace(root, WD_BR, ROOT_BR)
    wd_dir = root / "sessions" / WD_BR
    for i in range(n):
        sdir = _make_session(
            wd_dir, _bsid(i), work_dir=ROOT_BR, mtime=1_800_000_000 + i,
        )
        _append_index_line(root, _bsid(i), sdir, ROOT_BR)
    return wd_dir


def _rows_by_path(root: Path) -> dict[str, dict]:
    return {r["project_path"]: r for r in sessions_mod.list_sessions(root)}


# ---------------------------------------------------------------------------
# Home resolution
# ---------------------------------------------------------------------------


def test_kimi_code_home_honours_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "elsewhere"))
    assert sessions_mod.kimi_code_home() == tmp_path / "elsewhere"


def test_kimi_code_home_defaults_to_dot_kimi_code(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_CODE_HOME", raising=False)
    assert sessions_mod.kimi_code_home() == Path.home() / ".kimi-code"
    # An empty override is no override.
    monkeypatch.setenv("KIMI_CODE_HOME", "")
    assert sessions_mod.kimi_code_home() == Path.home() / ".kimi-code"


def test_list_sessions_default_root_reads_env_home(
    fake_kimi_home: Path, monkeypatch
) -> None:
    """``list_sessions()`` with no argument resolves the home itself."""
    monkeypatch.setenv("KIMI_CODE_HOME", str(fake_kimi_home))
    paths = {r["project_path"] for r in sessions_mod.list_sessions()}
    assert paths == {ROOT_A, ROOT_B, ROOT_C}


def test_kimi_binary_available_resolves_from_path(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    kimi = bindir / "kimi"
    kimi.write_text("#!/bin/sh\n")
    kimi.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert sessions_mod.kimi_binary_available() == str(kimi)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert sessions_mod.kimi_binary_available() is None


# ---------------------------------------------------------------------------
# Workspace registry (workspaces.json)
# ---------------------------------------------------------------------------


def test_load_workspaces_maps_ids_to_roots(fake_kimi_home: Path) -> None:
    workspaces = sessions_mod.load_workspaces(fake_kimi_home)
    assert set(workspaces) == {WD_A, WD_B, WD_C}
    assert workspaces[WD_A] == ROOT_A


def test_load_workspaces_skips_deleted_ids(fake_kimi_home: Path) -> None:
    # WD_DEL is registered AND has a live session dir on disk, but sits in
    # deleted_workspace_ids - it must not load and must not surface as a row.
    assert WD_DEL not in sessions_mod.load_workspaces(fake_kimi_home)
    assert "/home/user/gone" not in _rows_by_path(fake_kimi_home)


def test_load_workspaces_tolerates_missing_or_corrupt_file(tmp_path: Path) -> None:
    assert sessions_mod.load_workspaces(tmp_path) == {}
    (tmp_path / "workspaces.json").write_text("{broken")
    assert sessions_mod.load_workspaces(tmp_path) == {}
    (tmp_path / "workspaces.json").write_text("[1, 2]")
    assert sessions_mod.load_workspaces(tmp_path) == {}


def test_load_workspaces_skips_malformed_entries(tmp_path: Path) -> None:
    (tmp_path / "workspaces.json").write_text(json.dumps({
        "workspaces": {
            "wd_ok": {"root": "/r", "name": "r"},
            "wd_no_root": {"name": "x"},
            "wd_empty_root": {"root": ""},
            "wd_not_a_dict": "nope",
        },
    }))
    workspaces = sessions_mod.load_workspaces(tmp_path)
    assert set(workspaces) == {"wd_ok"}
    # An odd name never fails the entry - only the root is surfaced.
    (tmp_path / "workspaces.json").write_text(json.dumps({
        "workspaces": {"wd_ok": {"root": "/r", "name": 5}},
    }))
    assert sessions_mod.load_workspaces(tmp_path)["wd_ok"] == "/r"


def test_workspace_id_for_root_exact_match(fake_kimi_home: Path) -> None:
    assert sessions_mod.workspace_id_for_root(fake_kimi_home, ROOT_B) == WD_B
    assert sessions_mod.workspace_id_for_root(fake_kimi_home, "/no/such") is None


def test_workspace_id_for_root_resolves_symlink(tmp_path: Path) -> None:
    real = tmp_path / "realproj"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    root = tmp_path / ".kimi-code"
    root.mkdir()
    _register_workspace(root, "wd_realproj_000000000000", str(real))
    assert sessions_mod.workspace_id_for_root(root, str(link)) == (
        "wd_realproj_000000000000"
    )


# ---------------------------------------------------------------------------
# list_sessions rows
# ---------------------------------------------------------------------------


def test_list_sessions_empty_when_no_registry(tmp_path: Path) -> None:
    assert sessions_mod.list_sessions(tmp_path) == []


def test_list_sessions_one_row_per_workspace(fake_kimi_home: Path) -> None:
    rows = sessions_mod.list_sessions(fake_kimi_home)
    assert sorted(r["project_path"] for r in rows) == [ROOT_A, ROOT_B, ROOT_C]
    assert {r["encoded_path"] for r in rows} == {WD_A, WD_B, WD_C}


def test_list_sessions_row_shape(fake_kimi_home: Path) -> None:
    for row in sessions_mod.list_sessions(fake_kimi_home):
        assert set(row) == ROW_KEYS


def test_list_sessions_picks_most_recent_sibling(fake_kimi_home: Path) -> None:
    row = _rows_by_path(fake_kimi_home)[ROOT_B]
    assert row["session_id"] == SID_B_NEW
    assert row["file_mtime"] == MT_B_NEW * 1000
    # An auto-derived title is not the display name - that stays the
    # basename until a custom rename.
    assert row["name"] == "projB"
    assert row["extra_sessions"] == 1


def test_list_sessions_auto_title_falls_back_to_basename(
    fake_kimi_home: Path,
) -> None:
    rows = _rows_by_path(fake_kimi_home)
    # A carries an auto-derived title (isCustomTitle false) - basename wins.
    assert rows[ROOT_A]["name"] == "projA"
    assert rows[ROOT_A]["name_source"] == "basename"
    # C has no title at all.
    assert rows[ROOT_C]["name"] == "projC"
    assert rows[ROOT_C]["name_source"] == "basename"


def test_list_sessions_honours_custom_title(fake_kimi_home: Path) -> None:
    _register_workspace(fake_kimi_home, "wd_named_ffffffffffff", "/home/user/named")
    _make_session(
        fake_kimi_home / "sessions" / "wd_named_ffffffffffff",
        _bsid(1), title="my renamed session", custom=True,
        work_dir="/home/user/named", mtime=MT_A,
    )
    row = _rows_by_path(fake_kimi_home)["/home/user/named"]
    assert row["name"] == "my renamed session"
    assert row["name_source"] == "session"


def test_list_sessions_blank_custom_title_falls_back_to_basename(
    fake_kimi_home: Path,
) -> None:
    _register_workspace(fake_kimi_home, "wd_blank_ffffffffffff", "/home/user/blank")
    _make_session(
        fake_kimi_home / "sessions" / "wd_blank_ffffffffffff",
        _bsid(2), title="   ", custom=True,
        work_dir="/home/user/blank", mtime=MT_A,
    )
    row = _rows_by_path(fake_kimi_home)["/home/user/blank"]
    assert row["name"] == "blank"
    assert row["name_source"] == "basename"


def test_message_count_spans_all_agent_wires(fake_kimi_home: Path) -> None:
    # A: 2 events in agents/main + 3 in agents/subagent-1.
    rows = _rows_by_path(fake_kimi_home)
    assert rows[ROOT_A]["message_count"] == 5
    assert rows[ROOT_B]["message_count"] == 9
    assert rows[ROOT_C]["message_count"] == 0


def test_message_count_tolerates_corrupt_bytes(tmp_path: Path) -> None:
    sdir = _make_session(tmp_path, _bsid(5))
    wire = sdir / "agents" / "main" / "wire.jsonl"
    wire.write_bytes(
        b"\xff\xfe not utf8 at all\n"
        + _compact({"type": "context.append_message"}).encode() + b"\n"
    )
    assert sessions_mod._message_count(sdir) == 1


def test_message_count_caches_unchanged_wires(tmp_path: Path, monkeypatch) -> None:
    """An unchanged wire is served from the cache without a re-read; a
    changed one (mtime/size moved) is re-read and the count updates."""
    sdir = _make_session(tmp_path, _bsid(3), messages={"main": 2})
    wire = sdir / "agents" / "main" / "wire.jsonl"
    assert sessions_mod._message_count(sdir) == 2

    reads: list[Path] = []
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self == wire and args[:1] == ("rb",):
            reads.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    # Same mtime and size -> cached count, no re-read.
    assert sessions_mod._message_count(sdir) == 2
    assert reads == []
    # An appended message changes mtime/size -> re-read, count updates.
    with real_open(wire, "a", encoding="utf-8") as fh:
        fh.write(_compact(
            {"type": "context.append_message", "message": {"role": "user"}}
        ) + "\n")
    assert sessions_mod._message_count(sdir) == 3
    assert reads == [wire]


def test_session_activity_prefers_updated_at_when_newer(tmp_path: Path) -> None:
    """``file_mtime`` is max(updatedAt, state.json mtime)."""
    future_iso = "2030-01-01T00:00:00.000Z"
    sdir = _make_session(
        tmp_path, _bsid(6), updated=future_iso, mtime=1_700_000_000,
    )
    state = json.loads((sdir / "state.json").read_text())
    assert sessions_mod._session_activity(sdir, state) == (
        sessions_mod._parse_iso_ms(future_iso)
    )
    # And the mtime side of the max: an old/absent updatedAt yields the mtime.
    sdir2 = _make_session(tmp_path, _bsid(7), updated="", mtime=1_700_000_000)
    state2 = json.loads((sdir2 / "state.json").read_text())
    assert sessions_mod._session_activity(sdir2, state2) == 1_700_000_000_000


def test_parse_iso_ms_valid_and_malformed() -> None:
    assert sessions_mod._parse_iso_ms("1970-01-01T00:00:01.000Z") == 1000
    assert sessions_mod._parse_iso_ms("not a date") == 0
    assert sessions_mod._parse_iso_ms("") == 0
    assert sessions_mod._parse_iso_ms(None) == 0
    assert sessions_mod._parse_iso_ms(12345) == 0


def test_list_sessions_sorted_by_activity_desc(fake_kimi_home: Path) -> None:
    rows = sessions_mod.list_sessions(fake_kimi_home)
    assert [r["project_path"] for r in rows] == [ROOT_A, ROOT_B, ROOT_C]
    mtimes = [r["file_mtime"] for r in rows]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_sessions_skips_corrupt_state_sibling(fake_kimi_home: Path) -> None:
    """A sibling session with a corrupt state.json is skipped, not fatal."""
    broken = fake_kimi_home / "sessions" / WD_B / _bsid(8)
    broken.mkdir()
    (broken / "state.json").write_text("{broken")
    row = _rows_by_path(fake_kimi_home)[ROOT_B]
    assert row["session_id"] == SID_B_NEW
    assert row["extra_sessions"] == 1  # the corrupt dir does not count


def test_list_sessions_skips_workspace_with_only_corrupt_sessions(
    fake_kimi_home: Path,
) -> None:
    _register_workspace(fake_kimi_home, "wd_bad_ffffffffffff", "/home/user/bad")
    broken = fake_kimi_home / "sessions" / "wd_bad_ffffffffffff" / _bsid(9)
    broken.mkdir(parents=True)
    (broken / "state.json").write_text("not json")
    assert "/home/user/bad" not in _rows_by_path(fake_kimi_home)


def test_list_sessions_skips_registered_workspace_without_dir(
    fake_kimi_home: Path,
) -> None:
    _register_workspace(fake_kimi_home, "wd_nodir_fffffffffff0", "/home/user/nodir")
    assert "/home/user/nodir" not in _rows_by_path(fake_kimi_home)


def test_list_sessions_resolves_git_branch_once_per_root(
    fake_kimi_home: Path, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_branch(project_path: str) -> str:
        calls.append(project_path)
        return "feat-x"

    monkeypatch.setattr(sessions_mod, "_git_branch", fake_branch)
    rows = sessions_mod.list_sessions(fake_kimi_home)
    assert all(r["git_branch"] == "feat-x" for r in rows)
    assert sorted(calls) == [ROOT_A, ROOT_B, ROOT_C]  # one call per unique root


def test_git_branch_returns_branch_on_success(monkeypatch) -> None:
    completed = mock.Mock()
    completed.returncode = 0
    completed.stdout = "feature/x\n"
    monkeypatch.setattr(sessions_mod.subprocess, "run", lambda *a, **k: completed)
    assert real_git_branch("/some/repo") == "feature/x"


@pytest.mark.parametrize("stub", [
    # Degrading to "no branch" keeps the row rendering rather than failing
    # the whole listing on one bad repo.
    lambda *a, **k: mock.Mock(returncode=128, stdout=""),
    lambda *a, **k: mock.Mock(returncode=0, stdout="\n"),  # detached HEAD
    lambda *a, **k: (_ for _ in ()).throw(OSError("no git")),
    lambda *a, **k: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("git", sessions_mod.GIT_BRANCH_TIMEOUT_S)
    ),
])
def test_git_branch_none_on_failure(monkeypatch, stub) -> None:
    monkeypatch.setattr(sessions_mod.subprocess, "run", stub)
    assert real_git_branch("/some/repo") is None


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------


def test_toggle_favourite_round_trip(fake_kimi_home: Path) -> None:
    sessions_mod.toggle_favourite(fake_kimi_home, ROOT_A, True)
    assert sessions_mod.load_favourites(fake_kimi_home) == [ROOT_A]

    sessions_mod.toggle_favourite(fake_kimi_home, ROOT_B, True)
    assert sessions_mod.load_favourites(fake_kimi_home) == [ROOT_A, ROOT_B]

    sessions_mod.toggle_favourite(fake_kimi_home, ROOT_A, False)
    assert sessions_mod.load_favourites(fake_kimi_home) == [ROOT_B]


def test_list_sessions_marks_favourites(fake_kimi_home: Path) -> None:
    sessions_mod.toggle_favourite(fake_kimi_home, ROOT_A, True)
    rows = _rows_by_path(fake_kimi_home)
    assert rows[ROOT_A]["favourite"] is True
    assert rows[ROOT_B]["favourite"] is False


def test_load_favourites_dedups_and_tolerates_corruption(tmp_path: Path) -> None:
    (tmp_path / sessions_mod.FAVOURITES_FILENAME).write_text("{broken")
    assert sessions_mod.load_favourites(tmp_path) == []
    (tmp_path / sessions_mod.FAVOURITES_FILENAME).write_text(json.dumps({
        "favourites": ["/a", "/b", "/a", 5, None],
    }))
    assert sessions_mod.load_favourites(tmp_path) == ["/a", "/b"]


def test_save_favourites_is_atomic(tmp_path: Path) -> None:
    sessions_mod.save_favourites(tmp_path, ["/a"])
    target = tmp_path / sessions_mod.FAVOURITES_FILENAME
    assert json.loads(target.read_text()) == {"favourites": ["/a"]}
    # The tmp file used for the atomic replace must not linger.
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Current-session pins
# ---------------------------------------------------------------------------


def test_read_current_pin_rejects_tampered_content(fake_kimi_home: Path) -> None:
    """A corrupt/tampered pin (slash, dots, bad charset, non-UTF-8) is
    ignored, never raising, and resolution falls back to recency."""
    wd_dir = _make_branch_workspace(fake_kimi_home, 2)
    pin = wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    for bad in ("a/b", "..", "", "session_abc", SID_A.upper()):
        pin.write_text(bad)
        assert sessions_mod._read_current_pin(wd_dir) is None
        resolved = sessions_mod._resolve_current(wd_dir)
        assert resolved is not None and resolved[0].name == _bsid(1)
    pin.write_bytes(b"\xff\xfe not utf8")
    assert sessions_mod._read_current_pin(wd_dir) is None
    assert sessions_mod._resolve_current(wd_dir) is not None


def test_pick_current_ignores_dangling_pin(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    sessions_mod._write_current_pin(wd_dir, SID_GONE)
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(2)  # recency resumes


def test_set_current_pin_writes_sidecar(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    sessions_mod.set_current_pin(fake_kimi_home, WD_BR, _bsid(0))
    assert (
        wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    ).read_text().strip() == _bsid(0)


def test_set_current_pin_rejects_invalid_id(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 2)
    sessions_mod.set_current_pin(fake_kimi_home, WD_BR, "not-a-session-id")
    sessions_mod.set_current_pin(fake_kimi_home, WD_BR, None)
    assert not (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).exists()


def test_set_current_pin_safe_when_workspace_odd(fake_kimi_home: Path) -> None:
    # Best-effort: traversal or unknown workspace is a silent no-op.
    sessions_mod.set_current_pin(fake_kimi_home, "../evil", _bsid(0))
    sessions_mod.set_current_pin(fake_kimi_home, "", _bsid(0))


def test_clear_current_pin_safe_when_absent(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 2)
    sessions_mod.clear_current_pin(fake_kimi_home, WD_BR)  # no pin yet
    sessions_mod.clear_current_pin(fake_kimi_home, "wd_new_ffffffffffff")  # no dir
    sessions_mod.clear_current_pin(fake_kimi_home, "../evil")  # traversal


# ---------------------------------------------------------------------------
# Branch listing / switching
# ---------------------------------------------------------------------------


def test_list_branches_excludes_current_newest_first(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 8)
    result = sessions_mod.list_branches(fake_kimi_home, WD_BR)
    assert result["current"] == _bsid(7)
    ids = [b["session_id"] for b in result["branches"]]
    # Newest first, current excluded, ALL returned - the frontend caps the
    # submenu at 5 and offers the full list in the "More..." popup.
    assert ids == [_bsid(i) for i in range(6, -1, -1)]


def test_list_branches_labels_use_state_title(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    state_path = wd_dir / _bsid(1) / "state.json"
    state = json.loads(state_path.read_text())
    state["title"] = "renamed branch"
    state_path.write_text(json.dumps(state))
    os.utime(state_path, (1_800_000_001, 1_800_000_001))  # keep its slot
    result = sessions_mod.list_branches(fake_kimi_home, WD_BR)
    labels = {b["session_id"]: b["label"] for b in result["branches"]}
    assert labels[_bsid(1)] == "renamed branch"


def test_list_branches_untitled_label_is_short_uuid(fake_kimi_home: Path) -> None:
    # DEF-2: the fallback label must be the uuid part, never the constant
    # "session_" prefix every dir shares.
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    state_path = wd_dir / _bsid(1) / "state.json"
    state = json.loads(state_path.read_text())
    state["title"] = ""
    state_path.write_text(json.dumps(state))
    os.utime(state_path, (1_800_000_001, 1_800_000_001))  # keep its slot
    result = sessions_mod.list_branches(fake_kimi_home, WD_BR)
    labels = {b["session_id"]: b["label"] for b in result["branches"]}
    assert labels[_bsid(1)] == _bsid(1)[8:16]
    assert labels[_bsid(1)] != "session_"


def test_list_branches_rejects_traversal(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 2)
    assert sessions_mod.list_branches(fake_kimi_home, "../outside") is None
    assert sessions_mod.list_branches(fake_kimi_home, "") is None
    assert sessions_mod.list_branches(fake_kimi_home, "wd_missing_00000000000") is None


def test_switch_branch_makes_selected_current(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 3)
    result = sessions_mod.switch_branch(fake_kimi_home, WD_BR, _bsid(0))
    assert result == {"requested": _bsid(0), "current": _bsid(0)}
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(0)


def test_switch_branch_already_current_is_noop(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 3)
    result = sessions_mod.switch_branch(fake_kimi_home, WD_BR, _bsid(2))
    assert result == {"requested": _bsid(2), "current": _bsid(2)}


def test_switch_branch_missing_session_reports_not_found(
    fake_kimi_home: Path,
) -> None:
    _make_branch_workspace(fake_kimi_home, 2)
    result = sessions_mod.switch_branch(fake_kimi_home, WD_BR, SID_GONE)
    assert result == {"error": "branch_not_found"}


def test_switch_branch_rejects_invalid_input(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 2)
    assert sessions_mod.switch_branch(fake_kimi_home, "../x", _bsid(0)) is None
    assert sessions_mod.switch_branch(
        fake_kimi_home, WD_BR, "../../etc/passwd"
    ) is None
    assert sessions_mod.switch_branch(fake_kimi_home, WD_BR, "") is None
    assert sessions_mod.switch_branch(fake_kimi_home, WD_BR, None) is None


def test_switch_branch_touches_state_mtime_and_pins(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    state_path = wd_dir / _bsid(0) / "state.json"
    before = state_path.stat().st_mtime
    sessions_mod.switch_branch(fake_kimi_home, WD_BR, _bsid(0))
    after = state_path.stat().st_mtime
    assert after != before
    assert abs(after - time.time()) < 60  # touched to "now"
    assert (
        wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    ).read_text().strip() == _bsid(0)


def test_switch_branch_pin_survives_later_activity(fake_kimi_home: Path) -> None:
    """After switching to a branch, continuing to work in another
    conversation (its state.json mtime overtakes) must NOT drag the row's
    current back - the durable pin holds the switched branch as current."""
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)  # _bsid(2) newest
    sessions_mod.switch_branch(fake_kimi_home, WD_BR, _bsid(0))
    os.utime(wd_dir / _bsid(2) / "state.json", (1_900_000_000, 1_900_000_000))
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(0)


def test_new_session_clears_prior_switch_pin(fake_kimi_home: Path) -> None:
    """Starting a new session clears a prior switch pin so the new
    conversation (newest by recency) becomes current rather than staying
    behind the switched-to branch."""
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    sessions_mod.switch_branch(fake_kimi_home, WD_BR, _bsid(0))
    assert (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).exists()
    sessions_mod.clear_current_pin(fake_kimi_home, WD_BR)
    assert not (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).exists()
    # The new conversation lands newest -> recency makes it current.
    _make_session(wd_dir, _bsid(9), work_dir=ROOT_BR, mtime=1_950_000_000)
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(9)


def test_removed_current_falls_back_to_next_most_recent(
    fake_kimi_home: Path,
) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    shutil.rmtree(wd_dir / _bsid(2))
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(1)


# ---------------------------------------------------------------------------
# delete_branches / cleanup_parallel_sessions / remove_workspace
# ---------------------------------------------------------------------------


def test_delete_branches_removes_dirs_and_prunes_index(
    fake_kimi_home: Path,
) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 4)
    removed = sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, [_bsid(0), _bsid(1)]
    )
    assert removed == 2
    assert not (wd_dir / _bsid(0)).exists()
    assert not (wd_dir / _bsid(1)).exists()
    assert (wd_dir / _bsid(2)).is_dir()
    assert (wd_dir / _bsid(3)).is_dir()
    sids = _index_sids(fake_kimi_home)
    assert _bsid(0) not in sids
    assert _bsid(1) not in sids
    assert _bsid(2) in sids and _bsid(3) in sids
    assert SID_A in sids  # other workspaces' lines untouched


def test_delete_branches_never_deletes_current(fake_kimi_home: Path) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    removed = sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, [_bsid(2), _bsid(0)]
    )
    assert removed == 1
    assert (wd_dir / _bsid(2)).is_dir()
    assert not (wd_dir / _bsid(0)).exists()
    assert _bsid(2) in _index_sids(fake_kimi_home)


def test_delete_branches_skips_missing_silently(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 3)
    removed = sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, [_bsid(0), SID_GONE]
    )
    assert removed == 1


def test_delete_branches_rejects_invalid_input(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 3)
    assert sessions_mod.delete_branches(fake_kimi_home, "../x", [_bsid(0)]) is None
    assert sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, ["../../etc/passwd"]
    ) is None
    assert sessions_mod.delete_branches(fake_kimi_home, WD_BR, []) is None
    assert sessions_mod.delete_branches(fake_kimi_home, WD_BR, _bsid(0)) is None
    assert sessions_mod.delete_branches(fake_kimi_home, WD_BR, [_bsid(0), 5]) is None


def test_delete_all_extras_leaves_only_current(fake_kimi_home: Path) -> None:
    _make_branch_workspace(fake_kimi_home, 4)
    removed = sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, [_bsid(0), _bsid(1), _bsid(2)]
    )
    assert removed == 3
    row = _rows_by_path(fake_kimi_home)[ROOT_BR]
    assert row["session_id"] == _bsid(3)
    assert row["extra_sessions"] == 0


def test_delete_branches_to_trash_uses_send2trash(
    fake_kimi_home: Path, monkeypatch
) -> None:
    wd_dir = _make_branch_workspace(fake_kimi_home, 3)
    calls: list[str] = []
    monkeypatch.setattr(send2trash, "send2trash", calls.append)
    removed = sessions_mod.delete_branches(
        fake_kimi_home, WD_BR, [_bsid(0)], to_trash=True
    )
    assert removed == 1
    assert calls == [str(wd_dir / _bsid(0))]
    # send2trash was stubbed - the dir is untouched, but the call happened.
    assert (wd_dir / _bsid(0)).is_dir()


def test_prune_keeps_unparseable_index_lines(fake_kimi_home: Path) -> None:
    """Pruning must never destroy index content it does not understand."""
    _make_branch_workspace(fake_kimi_home, 2)
    index = fake_kimi_home / "session_index.jsonl"
    with index.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    sessions_mod.delete_branches(fake_kimi_home, WD_BR, [_bsid(0)])
    assert "this is not json" in index.read_text()
    assert _bsid(0) not in _index_sids(fake_kimi_home)


def test_cleanup_parallel_sessions_keeps_only_current(
    fake_kimi_home: Path,
) -> None:
    wd_dir = fake_kimi_home / "sessions" / WD_B
    # The pin sidecar must survive the sweep.
    (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).write_text(SID_B_NEW)
    workspaces_before = (fake_kimi_home / "workspaces.json").read_bytes()
    removed = sessions_mod.cleanup_parallel_sessions(fake_kimi_home, WD_B)
    assert removed == 1
    assert not (wd_dir / SID_B_OLD).exists()
    assert (wd_dir / SID_B_NEW).is_dir()
    assert (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).exists()
    sids = _index_sids(fake_kimi_home)
    assert SID_B_OLD not in sids
    assert SID_B_NEW in sids and SID_A in sids
    # The workspace registry is Kimi's own - byte-identical after cleanup.
    assert (fake_kimi_home / "workspaces.json").read_bytes() == workspaces_before


def test_cleanup_parallel_sessions_noop_when_single_session(
    fake_kimi_home: Path,
) -> None:
    removed = sessions_mod.cleanup_parallel_sessions(fake_kimi_home, WD_A)
    assert removed == 0
    assert (fake_kimi_home / "sessions" / WD_A / SID_A).is_dir()


def test_cleanup_parallel_sessions_rejects_traversal(fake_kimi_home: Path) -> None:
    assert sessions_mod.cleanup_parallel_sessions(fake_kimi_home, "../../etc") is None
    assert sessions_mod.cleanup_parallel_sessions(fake_kimi_home, "..") is None
    assert sessions_mod.cleanup_parallel_sessions(fake_kimi_home, "") is None
    assert sessions_mod.cleanup_parallel_sessions(
        fake_kimi_home, "wd_nosuch_00000000000"
    ) is None


def test_cleanup_parallel_sessions_to_trash_uses_send2trash(
    fake_kimi_home: Path, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(send2trash, "send2trash", calls.append)
    removed = sessions_mod.cleanup_parallel_sessions(
        fake_kimi_home, WD_B, to_trash=True
    )
    assert removed == 1
    assert calls == [str(fake_kimi_home / "sessions" / WD_B / SID_B_OLD)]
    assert (fake_kimi_home / "sessions" / WD_B / SID_B_OLD).is_dir()


def test_cleanup_trash_failure_falls_back_to_permanent_delete(
    fake_kimi_home: Path, monkeypatch
) -> None:
    def boom(_path: str) -> None:
        raise OSError("no trash backend on this platform")

    monkeypatch.setattr(send2trash, "send2trash", boom)
    removed = sessions_mod.cleanup_parallel_sessions(
        fake_kimi_home, WD_B, to_trash=True
    )
    assert removed == 1
    assert not (fake_kimi_home / "sessions" / WD_B / SID_B_OLD).exists()


def test_remove_workspace_deletes_dir_and_prunes_index(
    fake_kimi_home: Path,
) -> None:
    target = fake_kimi_home / "sessions" / WD_B
    workspaces_before = (fake_kimi_home / "workspaces.json").read_bytes()
    ok = sessions_mod.remove_workspace(fake_kimi_home, WD_B)
    assert ok is True
    assert not target.exists()
    sids = _index_sids(fake_kimi_home)
    assert SID_B_OLD not in sids and SID_B_NEW not in sids
    assert SID_A in sids  # other workspaces' lines survive
    # workspaces.json is deliberately untouched (Kimi's own registry).
    assert (fake_kimi_home / "workspaces.json").read_bytes() == workspaces_before


def test_remove_workspace_prunes_stale_index_lines(fake_kimi_home: Path) -> None:
    """Index lines whose session dir is already gone are pruned too."""
    wd_dir = fake_kimi_home / "sessions" / WD_A
    stale = "session_ffffffff-0000-4000-8000-000000000001"
    _append_index_line(fake_kimi_home, stale, wd_dir / stale, ROOT_A)
    ok = sessions_mod.remove_workspace(fake_kimi_home, WD_A)
    assert ok is True
    sids = _index_sids(fake_kimi_home)
    assert stale not in sids and SID_A not in sids


def test_remove_workspace_rejects_traversal(fake_kimi_home: Path) -> None:
    assert sessions_mod.remove_workspace(fake_kimi_home, "../../etc") is False
    assert sessions_mod.remove_workspace(fake_kimi_home, "..") is False
    assert sessions_mod.remove_workspace(fake_kimi_home, "") is False
    assert sessions_mod.remove_workspace(fake_kimi_home, "a/b") is False


def test_remove_workspace_false_when_nothing_to_remove(
    fake_kimi_home: Path,
) -> None:
    assert sessions_mod.remove_workspace(
        fake_kimi_home, "wd_unknown_000000000000"
    ) is False


def test_remove_workspace_to_trash_uses_send2trash(
    fake_kimi_home: Path, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(send2trash, "send2trash", calls.append)
    target = fake_kimi_home / "sessions" / WD_A
    ok = sessions_mod.remove_workspace(fake_kimi_home, WD_A, to_trash=True)
    assert ok is True
    assert calls == [str(target)]
    assert target.is_dir()  # stub recorded the call without deleting


def test_remove_workspace_trash_failure_falls_back(
    fake_kimi_home: Path, monkeypatch
) -> None:
    def boom(_path: str) -> None:
        raise OSError("no trash backend")

    monkeypatch.setattr(send2trash, "send2trash", boom)
    target = fake_kimi_home / "sessions" / WD_A
    ok = sessions_mod.remove_workspace(fake_kimi_home, WD_A, to_trash=True)
    assert ok is True
    assert not target.exists()


# ---------------------------------------------------------------------------
# fork_session
# ---------------------------------------------------------------------------


def test_fork_creates_copy_with_fork_title(fake_kimi_home: Path) -> None:
    wd_dir = fake_kimi_home / "sessions" / WD_B
    src_wire = (wd_dir / SID_B_NEW / "agents" / "main" / "wire.jsonl").read_text()
    before = time.time() * 1000
    result = sessions_mod.fork_session(fake_kimi_home, WD_B, SID_B_NEW)
    assert result["forked_from"] == SID_B_NEW
    new_id = result["session_id"]
    assert sessions_mod.SESSION_ID_RE.fullmatch(new_id)
    assert new_id != SID_B_NEW
    dst = wd_dir / new_id
    state = json.loads((dst / "state.json").read_text())
    assert state["title"] == "Fork of Newer conversation"
    assert state["isCustomTitle"] is True
    assert state["workDir"] == ROOT_B  # carried from the source
    # Fresh created/updated stamps: identical, parseable, and "now".
    assert state["createdAt"] == state["updatedAt"]
    created_ms = sessions_mod._parse_iso_ms(state["createdAt"])
    assert before - 1000 <= created_ms <= time.time() * 1000 + 1000
    # The conversation transcript is copied verbatim; the source survives.
    assert (dst / "agents" / "main" / "wire.jsonl").read_text() == src_wire
    assert (wd_dir / SID_B_NEW / "state.json").is_file()


def test_fork_named_uses_exact_title(fake_kimi_home: Path) -> None:
    result = sessions_mod.fork_session(
        fake_kimi_home, WD_B, SID_B_NEW, name="  my branch  "
    )
    dst = fake_kimi_home / "sessions" / WD_B / result["session_id"]
    state = json.loads((dst / "state.json").read_text())
    assert state["title"] == "my branch"
    assert state["isCustomTitle"] is True


def test_fork_of_untitled_session_falls_back_to_session_id(
    fake_kimi_home: Path,
) -> None:
    result = sessions_mod.fork_session(fake_kimi_home, WD_C, SID_C)
    dst = fake_kimi_home / "sessions" / WD_C / result["session_id"]
    state = json.loads((dst / "state.json").read_text())
    assert state["title"] == f"Fork of {SID_C}"


def test_fork_appends_index_line(fake_kimi_home: Path) -> None:
    result = sessions_mod.fork_session(fake_kimi_home, WD_B, SID_B_NEW)
    new_id = result["session_id"]
    last = (fake_kimi_home / "session_index.jsonl").read_text().splitlines()[-1]
    record = json.loads(last)
    assert record == {
        "sessionId": new_id,
        "sessionDir": str(fake_kimi_home / "sessions" / WD_B / new_id),
        "workDir": ROOT_B,
    }


def test_fork_pins_fork_as_current(fake_kimi_home: Path) -> None:
    """The fork becomes the row's current session the moment it exists, even
    though the parent stays the most-recently-active conversation - the pin
    overrides recency (intended branch behaviour)."""
    wd_dir = fake_kimi_home / "sessions" / WD_B
    result = sessions_mod.fork_session(fake_kimi_home, WD_B, SID_B_NEW)
    new_id = result["session_id"]
    assert (
        wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    ).read_text().strip() == new_id
    # Parent keeps being written (mtime bumped far above the fork's) - the
    # pinned fork must still win the row.
    os.utime(wd_dir / SID_B_NEW / "state.json", (1_990_000_000, 1_990_000_000))
    row = _rows_by_path(fake_kimi_home)[ROOT_B]
    assert row["session_id"] == new_id
    assert row["extra_sessions"] == 2


def test_fork_missing_source_reports_not_found(fake_kimi_home: Path) -> None:
    result = sessions_mod.fork_session(fake_kimi_home, WD_B, SID_GONE)
    assert result == {"error": "session_not_found"}


def test_fork_rejects_invalid_input(fake_kimi_home: Path) -> None:
    assert sessions_mod.fork_session(fake_kimi_home, WD_B, "not-an-id") is None
    assert sessions_mod.fork_session(fake_kimi_home, WD_B, "") is None
    assert sessions_mod.fork_session(fake_kimi_home, "../x", SID_B_NEW) is None
    assert sessions_mod.fork_session(fake_kimi_home, "", SID_B_NEW) is None
    assert sessions_mod.fork_session(
        fake_kimi_home, WD_B, SID_B_NEW, name=5
    ) is None


# ---------------------------------------------------------------------------
# /proc helpers (routes.py)
# ---------------------------------------------------------------------------


def _cmdline(*args: str) -> bytes:
    return b"\x00".join(a.encode() for a in args) + b"\x00"


def test_parse_resume_id_reads_short_flag() -> None:
    assert routes_mod._parse_resume_id(_cmdline("kimi", "-S", "abc-123")) == "abc-123"


def test_parse_resume_id_reads_long_and_equals_forms() -> None:
    assert routes_mod._parse_resume_id(
        _cmdline("kimi", "--session", "def-456")
    ) == "def-456"
    assert routes_mod._parse_resume_id(
        _cmdline("kimi", "--session=ghi-789")
    ) == "ghi-789"
    # An equals form with an empty value carries no id.
    assert routes_mod._parse_resume_id(_cmdline("kimi", "--session=")) is None


def test_parse_resume_id_none_for_new_or_continue_session() -> None:
    # A brand-new session is a bare ``kimi``; ``-c`` continues-for-cwd; a
    # bare ``-S`` opens the interactive picker. None of them carry an id, so
    # such a terminal is never claimed for a conversation (DEF-4).
    assert routes_mod._parse_resume_id(_cmdline("kimi")) is None
    assert routes_mod._parse_resume_id(_cmdline("kimi", "-c")) is None
    assert routes_mod._parse_resume_id(_cmdline("kimi", "-S")) is None


def test_parse_resume_id_does_not_swallow_a_following_flag() -> None:
    # A malformed ``-S`` with no value must not grab the next flag as the id.
    assert routes_mod._parse_resume_id(_cmdline("kimi", "-S", "--yolo")) is None
    # ...and it falls through to a usable ``--session``.
    assert routes_mod._parse_resume_id(
        _cmdline("kimi", "-S", "--session", "real-id")
    ) == "real-id"


def test_parse_resume_id_ignores_unrelated_args() -> None:
    assert routes_mod._parse_resume_id(
        _cmdline("kimi", "--yolo", "--auto")
    ) is None


def test_normalize_session_id() -> None:
    bare = "aaaaaaaa-0000-4000-8000-000000000001"
    assert routes_mod._normalize_session_id("session_" + bare) == "session_" + bare
    assert routes_mod._normalize_session_id(bare) == "session_" + bare
    assert routes_mod._normalize_session_id("some-branch-name") is None
    assert routes_mod._normalize_session_id("") is None
    assert routes_mod._normalize_session_id(None) is None


def test_kimi_session_id_walks_tree_and_normalizes(monkeypatch) -> None:
    # The pty root is a shell (init waiter); kimi is a child. The walk finds
    # the kimi pid and normalizes the bare uuid its argv carries.
    bare = "aaaaaaaa-0000-4000-8000-000000000001"
    monkeypatch.setattr(
        routes_mod, "_process_children",
        lambda pid: [4242] if pid == 1000 else [],
    )
    monkeypatch.setattr(
        routes_mod, "_process_comm",
        lambda pid: "kimi" if pid == 4242 else "bash",
    )
    monkeypatch.setattr(
        routes_mod, "_resume_id_from_cmdline",
        lambda pid: bare if pid == 4242 else None,
    )
    assert routes_mod._kimi_session_id(1000) == "session_" + bare


def test_kimi_session_id_none_without_kimi_in_tree(monkeypatch) -> None:
    monkeypatch.setattr(routes_mod, "_process_children", lambda pid: [])
    monkeypatch.setattr(routes_mod, "_process_comm", lambda pid: "bash")
    assert routes_mod._kimi_session_id(1000) is None


def test_kimi_session_id_none_for_idless_kimi(monkeypatch) -> None:
    # ``-c`` and bare ``kimi`` read back as None - an unknown id is never
    # reused, so the terminal is never focused for a row it may not run.
    monkeypatch.setattr(routes_mod, "_process_children", lambda pid: [])
    monkeypatch.setattr(routes_mod, "_process_comm", lambda pid: "kimi")
    monkeypatch.setattr(routes_mod, "_resume_id_from_cmdline", lambda pid: None)
    assert routes_mod._kimi_session_id(1000) is None


def test_tree_has_kimi_finds_nested_process(monkeypatch) -> None:
    children = {1: [2, 3], 2: [], 3: [4], 4: []}
    monkeypatch.setattr(routes_mod, "_process_children", lambda pid: children.get(pid, []))
    monkeypatch.setattr(
        routes_mod, "_process_comm",
        lambda pid: "kimi" if pid == 4 else "bash",
    )
    assert routes_mod._tree_has_kimi(1) is True


def test_tree_has_kimi_false_for_plain_shell(monkeypatch) -> None:
    monkeypatch.setattr(routes_mod, "_process_children", lambda pid: [])
    monkeypatch.setattr(routes_mod, "_process_comm", lambda pid: "bash")
    assert routes_mod._tree_has_kimi(1) is False


def test_terminal_cwds_only_reports_live_proc_cwd() -> None:
    """``_terminal_cwds`` reports a process's *live* cwd from
    ``/proc/<pid>/cwd`` only - never the frozen ``PWD`` env, which is the
    reason every pty used to also report the server's launch directory."""
    assert not hasattr(routes_mod, "_process_pwd_env")
    if not os.path.isdir("/proc/self"):
        pytest.skip("needs Linux /proc")
    cwds = routes_mod._terminal_cwds(os.getpid())
    assert os.path.realpath(os.getcwd()) in cwds


def test_terminal_cwds_tracks_chdir(tmp_path: Path, monkeypatch) -> None:
    if not os.path.isdir("/proc/self"):
        pytest.skip("needs Linux /proc")
    target = tmp_path / "elsewhere"
    target.mkdir()
    monkeypatch.chdir(target)
    cwds = routes_mod._terminal_cwds(os.getpid())
    assert os.path.realpath(str(target)) in cwds


def test_resume_id_from_cmdline_reads_proc() -> None:
    # Spawn a real long-lived process carrying -S in its argv and read the id
    # back from /proc. python3 -c ignores trailing args (so it stays alive);
    # poll until the kernel has populated cmdline with the exec'd argv.
    if not os.path.isdir("/proc/self"):
        pytest.skip("needs Linux /proc")
    proc = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(30)", "-S", "watched-id"],
    )
    try:
        got = None
        for _ in range(100):  # up to ~1s
            got = routes_mod._resume_id_from_cmdline(proc.pid)
            if got is not None:
                break
            time.sleep(0.01)
        assert got == "watched-id"
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Tornado handler tests
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_kimi_home(fake_kimi_home: Path):
    with mock.patch.object(
        sessions_mod, "kimi_code_home", return_value=fake_kimi_home
    ):
        yield fake_kimi_home


@pytest.fixture
def kimi_binary_available(monkeypatch):
    """Pretend the ``kimi`` binary is installed, so status reports enabled
    and launch-terminal gets past the binary gate."""
    monkeypatch.setattr(
        sessions_mod, "kimi_binary_available", lambda: "/usr/bin/kimi"
    )


async def test_status_endpoint_reports_binary_and_root_dir(
    jp_fetch, patched_kimi_home, kimi_binary_available
) -> None:
    response = await jp_fetch("jupyterlab-kimi-code-extension", "status")
    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["enabled"] is True
    assert payload["kimi_path"] == "/usr/bin/kimi"
    # root_dir must be an expanded absolute path - a leading "~" never
    # matches the absolute session paths the frontend compares it against.
    assert isinstance(payload["root_dir"], str) and payload["root_dir"]
    assert not payload["root_dir"].startswith("~")
    assert os.path.isabs(payload["root_dir"])


async def test_status_endpoint_disabled_without_binary(
    jp_fetch, patched_kimi_home, monkeypatch
) -> None:
    monkeypatch.setattr(sessions_mod, "kimi_binary_available", lambda: None)
    payload = json.loads(
        (await jp_fetch("jupyterlab-kimi-code-extension", "status")).body
    )
    assert payload["enabled"] is False
    assert payload["kimi_path"] is None


async def test_sessions_endpoint_returns_rows(jp_fetch, patched_kimi_home) -> None:
    response = await jp_fetch("jupyterlab-kimi-code-extension", "sessions")
    assert response.code == 200
    payload = json.loads(response.body)
    assert isinstance(payload["sessions"], list)
    assert {r["project_path"] for r in payload["sessions"]} == {
        ROOT_A, ROOT_B, ROOT_C,
    }


async def test_favourite_endpoint_persists(jp_fetch, patched_kimi_home) -> None:
    body = json.dumps({"project_path": ROOT_A, "favourite": True})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "favourite",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body)["favourites"] == [ROOT_A]
    assert sessions_mod.load_favourites(patched_kimi_home) == [ROOT_A]


async def test_favourite_rejects_bad_body(jp_fetch, patched_kimi_home) -> None:
    for body in (
        json.dumps({"project_path": "/x"}),  # missing 'favourite'
        json.dumps({"project_path": "/x", "favourite": "yes"}),  # non-bool
        "{broken",
    ):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "favourite",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


async def test_remove_endpoint(jp_fetch, patched_kimi_home, monkeypatch) -> None:
    # The handler honours ContentsManager.delete_to_trash (default on); stub
    # send2trash so the test doesn't move anything into the real trash.
    seen: list[str] = []
    monkeypatch.setattr(
        send2trash, "send2trash", lambda p: (seen.append(p), shutil.rmtree(p))
    )
    body = json.dumps({"encoded_path": WD_A})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "remove",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body) == {"removed": WD_A}
    assert seen  # routed through the trash path
    assert not (patched_kimi_home / "sessions" / WD_A).exists()
    assert SID_A not in _index_sids(patched_kimi_home)


async def test_remove_endpoint_rejects_traversal(
    jp_fetch, patched_kimi_home
) -> None:
    for body in (json.dumps({"encoded_path": "../etc"}), json.dumps({})):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "remove",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


async def test_post_endpoints_reject_non_dict_body(
    jp_fetch, patched_kimi_home
) -> None:
    """Valid JSON that is not an object must 400, not blow up on ``.get``."""
    for route in ("favourite", "remove", "cleanup", "switch", "delete-branches"):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", route,
                method="POST", body=json.dumps([1, 2]),
            )
        assert "400" in str(exc.value)


async def test_cleanup_endpoint(jp_fetch, patched_kimi_home, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        send2trash, "send2trash", lambda p: (seen.append(p), shutil.rmtree(p))
    )
    body = json.dumps({"encoded_path": WD_B})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "cleanup",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body) == {"removed_count": 1}
    assert seen
    wd_dir = patched_kimi_home / "sessions" / WD_B
    assert not (wd_dir / SID_B_OLD).exists()
    assert (wd_dir / SID_B_NEW).is_dir()


async def test_cleanup_endpoint_rejects_traversal(
    jp_fetch, patched_kimi_home
) -> None:
    body = json.dumps({"encoded_path": "../etc"})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "sessions", "cleanup",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)


async def test_branches_endpoint_lists_branches(
    jp_fetch, patched_kimi_home
) -> None:
    _make_branch_workspace(patched_kimi_home, 3)
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "branches",
        params={"encoded_path": WD_BR},
    )
    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["current"] == _bsid(2)
    assert [b["session_id"] for b in payload["branches"]] == [_bsid(1), _bsid(0)]


async def test_branches_endpoint_rejects_bad_path(
    jp_fetch, patched_kimi_home
) -> None:
    for params in ({"encoded_path": "../x"}, {}):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "branches",
                params=params,
            )
        assert "400" in str(exc.value)


async def test_switch_endpoint_switches_and_404s_on_missing(
    jp_fetch, patched_kimi_home
) -> None:
    _make_branch_workspace(patched_kimi_home, 3)
    body = json.dumps({"encoded_path": WD_BR, "session_id": _bsid(0)})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "switch",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body) == {
        "requested": _bsid(0), "current": _bsid(0),
    }

    body = json.dumps({"encoded_path": WD_BR, "session_id": SID_GONE})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "sessions", "switch",
            method="POST", body=body,
        )
    assert "404" in str(exc.value)


async def test_switch_endpoint_rejects_bad_body(
    jp_fetch, patched_kimi_home
) -> None:
    _make_branch_workspace(patched_kimi_home, 2)
    for body in (
        json.dumps({"encoded_path": WD_BR, "session_id": 5}),
        json.dumps({"encoded_path": WD_BR, "session_id": "not-an-id"}),
        json.dumps({"encoded_path": WD_BR}),
    ):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "switch",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


async def test_delete_branches_endpoint(jp_fetch, patched_kimi_home) -> None:
    wd_dir = _make_branch_workspace(patched_kimi_home, 4)
    body = json.dumps({
        "encoded_path": WD_BR, "session_ids": [_bsid(0), _bsid(1)],
    })
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "delete-branches",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body) == {"removed_count": 2}
    assert not (wd_dir / _bsid(0)).exists()
    assert (wd_dir / _bsid(3)).is_dir()


async def test_delete_branches_endpoint_rejects_bad_body(
    jp_fetch, patched_kimi_home
) -> None:
    _make_branch_workspace(patched_kimi_home, 3)
    for body in (
        json.dumps({"encoded_path": WD_BR, "session_ids": _bsid(0)}),
        json.dumps({"encoded_path": WD_BR, "session_ids": ["../../etc"]}),
        json.dumps({"encoded_path": WD_BR}),
    ):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "delete-branches",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


# ---------------------------------------------------------------------------
# fork endpoint
# ---------------------------------------------------------------------------


async def test_fork_endpoint_creates_branch(jp_fetch, patched_kimi_home) -> None:
    body = json.dumps({"encoded_path": WD_B, "session_id": SID_B_NEW})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "fork",
        method="POST", body=body,
    )
    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["forked_from"] == SID_B_NEW
    new_id = payload["session_id"]
    assert sessions_mod.SESSION_ID_RE.fullmatch(new_id)
    wd_dir = patched_kimi_home / "sessions" / WD_B
    assert (wd_dir / new_id / "state.json").is_file()
    # The fork is pinned as current so the panel row flips to it immediately.
    assert (
        wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    ).read_text().strip() == new_id


async def test_fork_endpoint_honours_name(jp_fetch, patched_kimi_home) -> None:
    body = json.dumps({
        "encoded_path": WD_B, "session_id": SID_B_NEW, "name": "  my fork  ",
    })
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "sessions", "fork",
        method="POST", body=body,
    )
    assert response.code == 200
    new_id = json.loads(response.body)["session_id"]
    state = json.loads(
        (patched_kimi_home / "sessions" / WD_B / new_id / "state.json").read_text()
    )
    assert state["title"] == "my fork"
    assert state["isCustomTitle"] is True


async def test_fork_endpoint_404_when_source_missing(
    jp_fetch, patched_kimi_home
) -> None:
    body = json.dumps({"encoded_path": WD_B, "session_id": SID_GONE})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "sessions", "fork",
            method="POST", body=body,
        )
    assert "404" in str(exc.value)


async def test_fork_endpoint_rejects_unknown_keys(
    jp_fetch, patched_kimi_home
) -> None:
    # DEF-3: fork validates its body as strictly as launch-terminal does.
    body = json.dumps({
        "encoded_path": WD_B, "session_id": SID_B_NEW, "surprise": True,
    })
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "sessions", "fork",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)


async def test_fork_endpoint_rejects_invalid_name(
    jp_fetch, patched_kimi_home
) -> None:
    for name in ("   ", "x" * 121, "bad\nname", 5):
        body = json.dumps({
            "encoded_path": WD_B, "session_id": SID_B_NEW, "name": name,
        })
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "fork",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


async def test_fork_endpoint_rejects_invalid_body(
    jp_fetch, patched_kimi_home
) -> None:
    for body in (
        json.dumps({"encoded_path": WD_B}),  # missing session_id
        json.dumps({"encoded_path": WD_B, "session_id": "not-an-id"}),
        json.dumps({"encoded_path": "../x", "session_id": SID_B_NEW}),
        "{broken",
    ):
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "sessions", "fork",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)


# ---------------------------------------------------------------------------
# launch-terminal endpoint
# ---------------------------------------------------------------------------


class _FakeTerminalManager:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return {"name": "term-x"}


@pytest.fixture
def fake_terminal_manager(jp_serverapp, kimi_binary_available):
    manager = _FakeTerminalManager()
    jp_serverapp.web_app.settings["terminal_manager"] = manager
    return manager


@pytest.fixture
def project_dir(jp_root_dir: Path) -> Path:
    """A real project folder under the served root - launch-terminal rejects
    anything outside ``server_root_dir``."""
    proj = jp_root_dir / "proj"
    proj.mkdir(exist_ok=True)
    return proj


def _launch_argv(manager: _FakeTerminalManager) -> list[str]:
    """The real argv behind the bash init-waiter wrapper."""
    cmd = manager.kwargs["shell_command"]
    assert cmd[:2] == ["/bin/bash", "-c"]
    assert cmd[3] == "kimi-terminal-init"
    return cmd[4:]


async def test_launch_terminal_new_session_is_bare_kimi(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    body = json.dumps({"project_path": str(project_dir)})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert response.code == 200
    assert json.loads(response.body) == {"terminal_name": "term-x"}
    # A new session has no pre-assignable id in kimi: bare ``kimi``, no -S.
    assert _launch_argv(fake_terminal_manager) == ["/usr/bin/kimi"]
    assert fake_terminal_manager.kwargs["cwd"] == str(project_dir)


async def test_launch_terminal_resume_builds_dash_s_argv(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    body = json.dumps({"project_path": str(project_dir), "session_id": SID_A})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert response.code == 200
    assert _launch_argv(fake_terminal_manager) == ["/usr/bin/kimi", "-S", SID_A]


async def test_launch_terminal_yolo_appended_exactly_once(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    # Resume + yolo.
    body = json.dumps({
        "project_path": str(project_dir), "session_id": SID_A, "yolo": True,
    })
    await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    argv = _launch_argv(fake_terminal_manager)
    assert argv == ["/usr/bin/kimi", "-S", SID_A, "--yolo"]
    assert argv.count("--yolo") == 1
    # New + yolo.
    body = json.dumps({"project_path": str(project_dir), "yolo": True})
    await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    argv = _launch_argv(fake_terminal_manager)
    assert argv == ["/usr/bin/kimi", "--yolo"]
    # yolo false leaves the flag off entirely.
    body = json.dumps({"project_path": str(project_dir), "yolo": False})
    await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert "--yolo" not in _launch_argv(fake_terminal_manager)


async def test_launch_terminal_rejects_unknown_body_keys(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    body = json.dumps({"project_path": str(project_dir), "verbose": True})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "launch-terminal",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)
    assert fake_terminal_manager.kwargs is None  # nothing was spawned


async def test_launch_terminal_rejects_non_bool_yolo(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    # yolo is a strict bool - a truthy string must never coerce into --yolo.
    body = json.dumps({"project_path": str(project_dir), "yolo": "false"})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "launch-terminal",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)
    assert fake_terminal_manager.kwargs is None  # nothing was spawned


async def test_launch_terminal_rejects_project_path_outside_root(
    jp_fetch, fake_terminal_manager, tmp_path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    for path in (str(outside), "/etc", ""):
        body = json.dumps({"project_path": path})
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "launch-terminal",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)
    assert fake_terminal_manager.kwargs is None


async def test_launch_terminal_rejects_missing_dir_under_root(
    jp_fetch, fake_terminal_manager, jp_root_dir
) -> None:
    body = json.dumps({"project_path": str(jp_root_dir / "no-such-dir")})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "launch-terminal",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)


async def test_launch_terminal_accepts_project_under_root_of_slash(
    jp_fetch, jp_serverapp, fake_terminal_manager, project_dir
) -> None:
    """A served root of "/" must still accept real dirs under it: the prefix
    form (``project_real.startswith(root_dir + os.sep)``) compares against
    "//", which no realpath ever starts with, so it rejected everything."""
    jp_serverapp.web_app.settings["server_root_dir"] = "/"
    body = json.dumps({"project_path": str(project_dir)})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert response.code == 200
    assert fake_terminal_manager.kwargs["cwd"] == str(project_dir)
    # Containment is relaxed, existence is not.
    body = json.dumps({"project_path": str(project_dir / "no-such-dir")})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "launch-terminal",
            method="POST", body=body,
        )
    assert "400" in str(exc.value)


async def test_launch_terminal_rejects_invalid_session_id(
    jp_fetch, fake_terminal_manager, project_dir
) -> None:
    bare_uuid = "aaaaaaaa-0000-4000-8000-000000000001"  # missing prefix
    for sid in ("not-an-id", bare_uuid, "", "session_../../x"):
        body = json.dumps({"project_path": str(project_dir), "session_id": sid})
        with pytest.raises(Exception) as exc:
            await jp_fetch(
                "jupyterlab-kimi-code-extension", "launch-terminal",
                method="POST", body=body,
            )
        assert "400" in str(exc.value)
    assert fake_terminal_manager.kwargs is None


async def test_launch_terminal_503_when_kimi_missing(
    jp_fetch, fake_terminal_manager, project_dir, monkeypatch
) -> None:
    monkeypatch.setattr(sessions_mod, "kimi_binary_available", lambda: None)
    body = json.dumps({"project_path": str(project_dir)})
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "launch-terminal",
            method="POST", body=body,
        )
    assert "503" in str(exc.value)


async def test_launch_terminal_new_session_clears_pin(
    jp_fetch, fake_terminal_manager, patched_kimi_home, project_dir
) -> None:
    """A new session supersedes a prior switch: the workspace pin is cleared
    so the new conversation becomes current by recency once it lands."""
    wd_id = "wd_proj_ffffffffffff"
    _register_workspace(patched_kimi_home, wd_id, str(project_dir))
    wd_dir = patched_kimi_home / "sessions" / wd_id
    wd_dir.mkdir(parents=True)
    (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).write_text(SID_A)
    body = json.dumps({"project_path": str(project_dir)})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert response.code == 200
    assert not (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).exists()


async def test_launch_terminal_resume_leaves_pin(
    jp_fetch, fake_terminal_manager, patched_kimi_home, project_dir
) -> None:
    """A resume launch never touches pins - the fork endpoint already pinned
    a freshly forked branch itself."""
    wd_id = "wd_proj_ffffffffffff"
    _register_workspace(patched_kimi_home, wd_id, str(project_dir))
    wd_dir = patched_kimi_home / "sessions" / wd_id
    wd_dir.mkdir(parents=True)
    (wd_dir / sessions_mod.CURRENT_PIN_FILENAME).write_text(SID_A)
    body = json.dumps({"project_path": str(project_dir), "session_id": SID_A})
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "launch-terminal",
        method="POST", body=body,
    )
    assert response.code == 200
    assert (
        wd_dir / sessions_mod.CURRENT_PIN_FILENAME
    ).read_text().strip() == SID_A


# ---------------------------------------------------------------------------
# terminal-cwd endpoint
# ---------------------------------------------------------------------------


class _FakePty:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class _FakeTerminal:
    def __init__(self, pid: int) -> None:
        self.ptyproc = _FakePty(pid)


class _LookupTerminalManager:
    """Mirrors the live manager's shape: a ``terminals`` dict the handler
    probes with a non-creating ``.get`` (the real ``get_terminal`` is
    get-or-create and must never be used for the lookup)."""

    def __init__(self) -> None:
        self.terminals = {"t1": _FakeTerminal(4242)}


async def test_terminal_cwd_endpoint_reports_cwds_kimi_and_session(
    jp_fetch, jp_serverapp, monkeypatch
) -> None:
    jp_serverapp.web_app.settings["terminal_manager"] = _LookupTerminalManager()
    monkeypatch.setattr(routes_mod, "_terminal_cwds", lambda pid: ["/w/proj"])
    monkeypatch.setattr(routes_mod, "_tree_has_kimi", lambda pid: True)
    monkeypatch.setattr(routes_mod, "_kimi_session_id", lambda pid: SID_A)
    response = await jp_fetch(
        "jupyterlab-kimi-code-extension", "terminal-cwd", "t1"
    )
    assert response.code == 200
    assert json.loads(response.body) == {
        "terminal_name": "t1",
        "cwds": ["/w/proj"],
        "has_kimi": True,
        "session_id": SID_A,
    }


async def test_terminal_cwd_endpoint_404_for_unknown_terminal(
    jp_fetch, jp_serverapp
) -> None:
    jp_serverapp.web_app.settings["terminal_manager"] = _LookupTerminalManager()
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "terminal-cwd", "nope"
        )
    assert "404" in str(exc.value)


async def test_terminal_cwd_endpoint_503_without_terminal_service(
    jp_fetch, jp_serverapp
) -> None:
    jp_serverapp.web_app.settings["terminal_manager"] = None
    with pytest.raises(Exception) as exc:
        await jp_fetch(
            "jupyterlab-kimi-code-extension", "terminal-cwd", "t1"
        )
    assert "503" in str(exc.value)
