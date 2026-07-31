"""Pure-Python session enumeration for Kimi Code workspaces.

Reads from ``~/.kimi-code/workspaces.json`` (Kimi's own workspace registry),
enumerates ``sessions/<wd_id>/session_*/`` directories, and surfaces one row
per workspace - the workspace's current session - decorated with favourite
flags. Sibling session dirs under one workspace dir are parallel
conversations ("branches").
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSIONS_DIRNAME = "sessions"
WORKSPACES_FILENAME = "workspaces.json"
INDEX_FILENAME = "session_index.jsonl"
FAVOURITES_FILENAME = "jupyterlab_kimi_code_extension.json"
STATE_FILENAME = "state.json"
# Sidecar in a workspace's session dir holding the session id a "switch"
# pinned as the workspace's current conversation. A dotfile so it never
# collides with Kimi's own ``session_*`` dirs. See ``switch_branch`` /
# ``_pick_current``.
CURRENT_PIN_FILENAME = ".jl-current"
# Kimi session ids are ``session_`` + uuid4 (hex + hyphen, 36 chars). The
# restricted charset keeps a tampered or corrupt id (slash, control bytes,
# "."/"..") from ever reaching a path join.
SESSION_ID_RE = re.compile(r"session_[0-9a-f-]{36}")
# Ceiling on the per-workspace ``git branch --show-current`` call. A sessions
# poll must never hang on a wedged repo - on timeout the row degrades to "no
# branch" rather than stalling.
GIT_BRANCH_TIMEOUT_S = 2.0
# Byte pattern identifying a message event in a wire.jsonl line. Counted as a
# substring, not parsed - counting must stay cheap on multi-MB transcripts.
_MESSAGE_PATTERN = b'"type":"context.append_message"'
# Per-wire-file message-count cache: path -> (st_mtime_ns, st_size, count).
# A wire log is re-read only when its mtime or size changed, so the 30s
# sessions poll stops re-scanning multi-MB transcripts that did not move.
_message_count_cache: dict[str, tuple[int, int, int]] = {}


def kimi_code_home() -> Path:
    """Return the Kimi Code storage root: ``$KIMI_CODE_HOME`` when set, else
    ``~/.kimi-code``."""
    override = os.environ.get("KIMI_CODE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kimi-code"


def kimi_binary_available() -> str | None:
    """Return the resolved path of the ``kimi`` binary or ``None``."""
    return shutil.which("kimi")


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _load_state(session_dir: Path) -> dict | None:
    """Read a session's ``state.json``; a missing or corrupt file skips the
    session rather than failing the whole listing."""
    data = _load_json(session_dir / STATE_FILENAME)
    return data if isinstance(data, dict) else None


def _parse_iso_ms(value: Any) -> int:
    """Parse an ISO-8601 ``...Z`` timestamp to ms-epoch; 0 when malformed."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1000)


def _now_iso_z() -> str:
    """Current UTC time in Kimi's own ``state.json`` timestamp format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def load_workspaces(kimi_root: Path) -> dict[str, str]:
    """Map ``wd_id`` -> workspace root from ``workspaces.json``.

    Entries listed in ``deleted_workspace_ids`` and entries without a usable
    ``root`` are skipped.
    """
    data = _load_json(kimi_root / WORKSPACES_FILENAME)
    if not isinstance(data, dict):
        return {}
    deleted_raw = data.get("deleted_workspace_ids")
    deleted = (
        {item for item in deleted_raw if isinstance(item, str)}
        if isinstance(deleted_raw, list)
        else set()
    )
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, dict):
        return {}
    result: dict[str, str] = {}
    for wd_id, entry in workspaces.items():
        if not isinstance(wd_id, str) or wd_id in deleted:
            continue
        if not isinstance(entry, dict):
            continue
        root = entry.get("root")
        if not isinstance(root, str) or not root:
            continue
        result[wd_id] = root
    return result


def workspace_id_for_root(kimi_root: Path, project_path: str) -> str | None:
    """The ``wd_id`` whose registered root is ``project_path``, or None.

    Exact match first, then a realpath comparison so a symlinked launch path
    still finds its workspace.
    """
    workspaces = load_workspaces(kimi_root)
    for wd_id, root in workspaces.items():
        if root == project_path:
            return wd_id
    try:
        wanted = os.path.realpath(project_path)
    except OSError:
        return None
    for wd_id, root in workspaces.items():
        try:
            if os.path.realpath(root) == wanted:
                return wd_id
        except OSError:
            continue
    return None


def load_favourites(kimi_root: Path) -> list[str]:
    """Return the list of favourite project paths (deduplicated, order-preserved)."""
    data = _load_json(kimi_root / FAVOURITES_FILENAME)
    if not isinstance(data, dict):
        return []
    favs = data.get("favourites")
    if not isinstance(favs, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in favs:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def save_favourites(kimi_root: Path, favourites: list[str]) -> None:
    """Atomically write the favourites list."""
    kimi_root.mkdir(parents=True, exist_ok=True)
    target = kimi_root / FAVOURITES_FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = json.dumps({"favourites": favourites}, indent=2)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, target)


def toggle_favourite(kimi_root: Path, project_path: str, favourite: bool) -> list[str]:
    """Add or remove ``project_path`` from favourites. Returns the new list."""
    favs = load_favourites(kimi_root)
    if favourite and project_path not in favs:
        favs.append(project_path)
    elif not favourite and project_path in favs:
        favs.remove(project_path)
    save_favourites(kimi_root, favs)
    return favs


def _read_current_pin(wd_dir: Path) -> str | None:
    """The session id a ``switch`` pinned as this workspace's current, or None.

    Stored in the ``.jl-current`` sidecar (one session id). Returns None when
    absent, empty, or malformed - the charset gate keeps a tampered pin from
    ever being compared against a directory name.
    """
    try:
        sid = (wd_dir / CURRENT_PIN_FILENAME).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        # OSError: missing file / unreadable. ValueError covers
        # UnicodeDecodeError - a corrupt or non-UTF-8 pin must be ignored, not
        # crash resolution; a NUL in the path would raise here too.
        return None
    if not SESSION_ID_RE.fullmatch(sid):
        return None
    return sid


def _write_current_pin(wd_dir: Path, session_id: str) -> None:
    """Pin ``session_id`` as the workspace's current conversation.

    Best-effort: a write failure just leaves resolution to fall back to
    recency (see ``_read_current_pin`` / ``_pick_current``).
    """
    try:
        (wd_dir / CURRENT_PIN_FILENAME).write_text(session_id, encoding="utf-8")
    except OSError:
        pass


def clear_current_pin(kimi_root: Path, encoded_path: str) -> None:
    """Drop any switch pin for a workspace so recency resumes.

    Called when a new session is started: the new session supersedes a prior
    switch, and it naturally becomes the row's current by recency once its
    state.json lands (it is the newest session). Clearing the pin - rather
    than pinning the not-yet-existent new id - avoids leaving a permanently
    dangling pin if the user abandons the session before it writes its first
    turn. Best-effort: a missing pin, missing dir, or odd path leaves nothing
    to clear.
    """
    wd_dir = _wd_dir_path(kimi_root, encoded_path)
    if wd_dir is None:
        return
    try:
        (wd_dir / CURRENT_PIN_FILENAME).unlink()
    except OSError:
        pass


def set_current_pin(kimi_root: Path, encoded_path: str, session_id: str) -> None:
    """Pin ``session_id`` as a workspace's current conversation.

    Called when a branch is created (a fork): branching is an explicit "go to
    this new conversation" action, so the new branch should become the row's
    current the moment it exists. Unlike a brand-new session - which becomes
    current by recency on its own (it is the newest session) and so only
    needs the pin cleared - a fork is shadowed by the parent you branched
    from: that parent is the conversation you are actively in, so its
    state.json keeps being rewritten and its activity timestamp overtakes the
    fork's, dragging the row back to it. Only a durable pin makes the fork
    win over that recency (see ``_pick_current``). An abandoned fork leaves a
    benign dangling pin (ignored; cleared by the next new session,
    overwritten by the next switch). Best-effort: an odd path or write
    failure leaves resolution to recency.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return
    wd_dir = _wd_dir_path(kimi_root, encoded_path)
    if wd_dir is None:
        return
    _write_current_pin(wd_dir, session_id)


def _session_dirs(wd_dir: Path) -> list[tuple[Path, dict]]:
    """(session_dir, state) pairs for every valid session under a workspace dir.

    A session dir must be named like a session id and carry a readable
    ``state.json`` - anything else (the pin sidecar, a stray folder, a
    corrupt state) is skipped.
    """
    out: list[tuple[Path, dict]] = []
    try:
        children = sorted(wd_dir.iterdir())
    except OSError:
        return out
    for child in children:
        if not child.is_dir():
            continue
        if not SESSION_ID_RE.fullmatch(child.name):
            continue
        state = _load_state(child)
        if state is None:
            continue
        out.append((child, state))
    return out


def _session_activity(session_dir: Path, state: dict) -> int:
    """ms-epoch of the session's last file activity: the later of the
    recorded ``updatedAt`` and the state.json mtime."""
    updated_ms = _parse_iso_ms(state.get("updatedAt"))
    try:
        mtime_ms = int((session_dir / STATE_FILENAME).stat().st_mtime * 1000)
    except OSError:
        mtime_ms = 0
    return max(updated_ms, mtime_ms)


def _pick_current(
    wd_dir: Path, sessions: list[tuple[Path, dict]]
) -> tuple[Path, dict] | None:
    """Pick the workspace's current session, trusting the filesystem.

    A "switch" pins the workspace's current conversation durably (see
    ``switch_branch``). Honour the pin over recency so continuing to work in
    another conversation does not silently drag the row back to it. The pin
    wins only when its session dir still exists with a readable state; a
    dangling pin is ignored and the recency scan resumes.
    """
    if not sessions:
        return None
    pinned = _read_current_pin(wd_dir)
    if pinned:
        for session_dir, state in sessions:
            if session_dir.name == pinned:
                return session_dir, state
    return max(sessions, key=lambda item: _session_activity(*item))


def _resolve_current(wd_dir: Path) -> tuple[Path, dict] | None:
    """``_pick_current`` over the workspace's enumerated sessions."""
    return _pick_current(wd_dir, _session_dirs(wd_dir))


def _message_count(session_dir: Path) -> int:
    """Count of ``context.append_message`` events across every agent wire log
    of the session. Read in binary so a corrupt byte never aborts the count.
    Per-file counts are cached (``_message_count_cache``) and only a wire
    whose mtime or size changed is re-read.
    """
    agents_dir = session_dir / "agents"
    count = 0
    try:
        wires = list(agents_dir.glob("*/wire.jsonl"))
    except OSError:
        return 0
    for wire in wires:
        try:
            st = wire.stat()
        except OSError:
            continue
        key = str(wire)
        cached = _message_count_cache.get(key)
        if cached is not None and cached[:2] == (st.st_mtime_ns, st.st_size):
            count += cached[2]
            continue
        file_count = 0
        try:
            with wire.open("rb") as fh:
                for line in fh:
                    if _MESSAGE_PATTERN in line:
                        file_count += 1
        except OSError:
            continue
        _message_count_cache[key] = (st.st_mtime_ns, st.st_size, file_count)
        count += file_count
    return count


def _git_branch(project_path: str) -> str | None:
    """Current git branch of a workspace root, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", project_path, "branch", "--show-current"],
            capture_output=True,
            timeout=GIT_BRANCH_TIMEOUT_S,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch or None


def list_sessions(kimi_root: Path | None = None) -> list[dict]:
    """Return one row per workspace, surfacing the workspace's current session.

    Each row carries: ``project_path`` (the ``workspaces.json`` root),
    ``encoded_path`` (the ``wd_id``), ``session_id``, ``name``,
    ``name_source``, ``message_count``, ``file_mtime``, ``git_branch``,
    ``favourite``, and ``extra_sessions`` (count of sibling session dirs
    beyond the current one).

    ``name`` is the session's own title when it is a custom title
    (``name_source = "session"``), otherwise the workspace root's basename
    (``name_source = "basename"``). ``presentationMode`` in the frontend
    still decides between this label and the relative path.
    """
    root = kimi_root if kimi_root is not None else kimi_code_home()
    workspaces = load_workspaces(root)
    if not workspaces:
        return []

    favourites = set(load_favourites(root))
    # One subprocess per unique root, shared by every row of that root.
    git_cache: dict[str, str | None] = {}

    rows: list[dict] = []
    for wd_id in sorted(workspaces):
        project_path = workspaces[wd_id]
        wd_dir = root / SESSIONS_DIRNAME / wd_id
        if not wd_dir.is_dir():
            continue
        sessions = _session_dirs(wd_dir)
        current = _pick_current(wd_dir, sessions)
        if current is None:
            continue
        session_dir, state = current

        title = state.get("title")
        title = title if isinstance(title, str) else ""
        # Honour the session's own name only when it is a custom title: an
        # auto-derived title is just the first prompt reworded, so the folder
        # basename is the better label until the user renames the session.
        if title.strip() and bool(state.get("isCustomTitle")):
            name = title
            name_source = "session"
        else:
            name = os.path.basename(project_path) or wd_id
            name_source = "basename"

        if project_path not in git_cache:
            git_cache[project_path] = _git_branch(project_path)

        rows.append({
            "project_path": project_path,
            "encoded_path": wd_id,
            "session_id": session_dir.name,
            "name": name,
            "name_source": name_source,
            "message_count": _message_count(session_dir),
            "file_mtime": _session_activity(session_dir, state),
            "git_branch": git_cache[project_path],
            "favourite": project_path in favourites,
            "extra_sessions": max(len(sessions) - 1, 0),
        })

    rows.sort(key=lambda r: r["file_mtime"], reverse=True)
    return rows


def _dispose_path(target: Path, to_trash: bool) -> None:
    """Delete ``target`` (file or dir), via the desktop trash when asked.

    A failed trash move (no backend, unsupported filesystem, permission
    error, ...) falls back to a permanent delete.
    """
    if to_trash:
        try:
            from send2trash import send2trash

            send2trash(str(target))
            return
        except Exception:
            pass
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _wd_dir_path(kimi_root: Path, encoded_path: str) -> Path | None:
    """Resolve ``sessions/<encoded_path>`` rejecting path traversal.

    No ``/`` in the segment, no ``.``/``..``, and the resolved dir must stay
    under the sessions root. Does NOT require the dir to exist (see
    ``_safe_wd_dir`` for the variant that does).
    """
    if not encoded_path or "/" in encoded_path or encoded_path in (".", ".."):
        return None
    try:
        wd_dir = (kimi_root / SESSIONS_DIRNAME / encoded_path).resolve()
    except (OSError, ValueError):
        return None
    base = (kimi_root / SESSIONS_DIRNAME).resolve()
    try:
        wd_dir.relative_to(base)
    except ValueError:
        return None
    return wd_dir


def _safe_wd_dir(kimi_root: Path, encoded_path: str) -> Path | None:
    """``_wd_dir_path`` plus an is-dir check. Returns None when invalid or
    not a directory."""
    wd_dir = _wd_dir_path(kimi_root, encoded_path)
    if wd_dir is None or not wd_dir.is_dir():
        return None
    return wd_dir


def _prune_index_lines(kimi_root: Path, session_ids) -> None:
    """Rewrite ``session_index.jsonl`` without the pruned session ids.

    Read all lines, drop the ones whose ``sessionId`` is being removed, and
    atomically replace the file (tmp + rename). Lines that do not parse are
    kept - pruning must never destroy content it does not understand.
    Best-effort: a missing or unwritable index leaves nothing to prune.
    """
    ids = set(session_ids)
    if not ids:
        return
    index_path = kimi_root / INDEX_FILENAME
    try:
        with index_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    kept: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        sid = record.get("sessionId") if isinstance(record, dict) else None
        if isinstance(sid, str) and sid in ids:
            continue
        kept.append(line)
    tmp = index_path.with_suffix(index_path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.writelines(kept)
        os.replace(tmp, index_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _index_session_ids_under(kimi_root: Path, wd_dir: Path) -> set[str]:
    """Session ids whose index line's ``sessionDir`` sits under ``wd_dir``.

    Catches stale index lines whose session dir is already gone, so removing
    a workspace prunes ALL of its lines, not only the ones with live dirs.
    """
    prefix = str(wd_dir) + os.sep
    ids: set[str] = set()
    try:
        with (kimi_root / INDEX_FILENAME).open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                sdir = record.get("sessionDir")
                sid = record.get("sessionId")
                if (
                    isinstance(sdir, str)
                    and isinstance(sid, str)
                    and (sdir == str(wd_dir) or sdir.startswith(prefix))
                ):
                    ids.add(sid)
    except OSError:
        pass
    return ids


def list_branches(kimi_root: Path, encoded_path: str) -> dict | None:
    """List a workspace's other conversation sessions ("branches").

    Returns ``{"current": <current sid>,
    "branches": [{"session_id", "file_mtime", "label"}, ...]}`` - the
    current session excluded, newest first, ALL of them (the frontend shows
    the most recent in the submenu and the full list in the "More..." popup).
    The label is the session's own ``state.json`` title, falling back to the
    first 8 chars of the id's uuid part. Returns None on invalid path or when
    no current session resolves.
    """
    wd_dir = _safe_wd_dir(kimi_root, encoded_path)
    if wd_dir is None:
        return None
    sessions = _session_dirs(wd_dir)
    current = _pick_current(wd_dir, sessions)
    if current is None:
        return None
    current_dir, _ = current
    current_sid = current_dir.name

    branches = []
    for session_dir, state in sessions:
        if session_dir.name == current_sid:
            continue
        title = state.get("title")
        # Fallback label is the first 8 chars of the uuid part - the
        # "session_" prefix is shared by every dir and carries no information.
        label = (
            title.strip()
            if isinstance(title, str) and title.strip()
            else session_dir.name[8:16]
        )
        branches.append({
            "session_id": session_dir.name,
            "file_mtime": _session_activity(session_dir, state),
            "label": label,
        })
    branches.sort(key=lambda b: b["file_mtime"], reverse=True)
    return {
        "current": current_sid,
        "branches": branches,
    }


def switch_branch(kimi_root: Path, encoded_path: str, session_id: str) -> dict | None:
    """Make ``session_id`` the workspace's current conversation.

    Persists by writing a durable per-workspace pin (``.jl-current``) that
    ``_pick_current`` honours over recency, and by touching the state.json
    mtime so the file-activity resolution stays roughly aligned. The pin
    makes the choice stick even after later activity in another conversation
    bumps its timestamp higher (the recency-revert defect). Returns
    ``{"requested", "current"}`` where ``current`` is re-resolved after the
    write. Returns ``{"error": "branch_not_found"}`` when the session dir is
    gone (e.g. removed between menu display and click) and None on invalid
    input.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return None
    wd_dir = _safe_wd_dir(kimi_root, encoded_path)
    if wd_dir is None:
        return None
    target = wd_dir / session_id
    if _load_state(target) is None:
        return {"error": "branch_not_found"}
    # Touch mtime so the file-activity resolution stays roughly aligned, and
    # write a durable pin so our resolution sticks even after subsequent
    # activity in another conversation bumps its timestamp higher.
    os.utime(target / STATE_FILENAME, None)
    _write_current_pin(wd_dir, session_id)
    resolved = _resolve_current(wd_dir)
    return {
        "requested": session_id,
        "current": resolved[0].name if resolved else None,
    }


def delete_branches(
    kimi_root: Path,
    encoded_path: str,
    session_ids: list,
    to_trash: bool = False,
) -> int | None:
    """Delete selected branch sessions from a workspace dir.

    For every requested session id the whole ``session_<id>/`` dir is
    removed - to the desktop trash when ``to_trash`` is true - and its line
    is pruned from ``session_index.jsonl``. The current session
    (``_pick_current``) is never deleted even when requested; a missing
    session dir is treated as already deleted (skipped silently). Returns the
    number of sessions actually removed, or None on invalid input.
    """
    if not isinstance(session_ids, list) or not session_ids:
        return None
    for sid in session_ids:
        if not isinstance(sid, str) or not SESSION_ID_RE.fullmatch(sid):
            return None
    wd_dir = _safe_wd_dir(kimi_root, encoded_path)
    if wd_dir is None:
        return None
    current = _resolve_current(wd_dir)
    keep = current[0].name if current else None
    removed: list[str] = []
    for sid in session_ids:
        if sid == keep:
            continue
        session_dir = wd_dir / sid
        if not session_dir.is_dir():
            continue
        _dispose_path(session_dir, to_trash)
        removed.append(sid)
    _prune_index_lines(kimi_root, removed)
    return len(removed)


def cleanup_parallel_sessions(
    kimi_root: Path, encoded_path: str, to_trash: bool = False
) -> int | None:
    """Remove every session in a workspace dir except the current one.

    The current session is the same one ``list_sessions`` surfaces for the
    row (``_pick_current``). Every other ``session_<id>/`` dir is removed -
    to the desktop trash when ``to_trash`` is true - and its line is pruned
    from ``session_index.jsonl``. Anything else in the workspace dir (the
    pin sidecar, ...) is untouched. Returns the number of sessions removed,
    or None on failure (path traversal, missing folder, no resolvable
    current session).
    """
    wd_dir = _safe_wd_dir(kimi_root, encoded_path)
    if wd_dir is None:
        return None
    sessions = _session_dirs(wd_dir)
    current = _pick_current(wd_dir, sessions)
    if current is None:
        return None
    keep = current[0].name
    removed: list[str] = []
    for session_dir, _state in sessions:
        if session_dir.name == keep:
            continue
        _dispose_path(session_dir, to_trash)
        removed.append(session_dir.name)
    _prune_index_lines(kimi_root, removed)
    return len(removed)


def remove_workspace(
    kimi_root: Path, encoded_path: str, to_trash: bool = False
) -> bool:
    """Remove the workspace session dir ``sessions/<encoded_path>``.

    Every session dir of the workspace is removed - to the desktop trash
    when ``to_trash`` is true - and the workspace's lines are pruned from
    ``session_index.jsonl``. ``workspaces.json`` is deliberately left
    untouched: the workspace registry is Kimi's own, and a workspace with no
    sessions is simply a row-less entry. Returns True when anything was
    removed. Refuses to touch anything outside the sessions root (path
    traversal protection).
    """
    wd_dir = _wd_dir_path(kimi_root, encoded_path)
    if wd_dir is None:
        return False
    ids = _index_session_ids_under(kimi_root, wd_dir)
    existed = wd_dir.is_dir()
    if existed:
        for session_dir, _state in _session_dirs(wd_dir):
            ids.add(session_dir.name)
        _dispose_path(wd_dir, to_trash)
    _prune_index_lines(kimi_root, ids)
    return existed or bool(ids)


def fork_session(
    kimi_root: Path,
    encoded_path: str,
    session_id: str,
    name: str | None = None,
) -> dict | None:
    """Branch ``session_id`` by copying its session dir under a fresh id.

    Kimi has no fork flag of its own, so the extension forks on Kimi's
    behalf: ``session_<uuid>`` is copied to ``session_<new-uuid>`` next to
    the original, the copy's ``state.json`` is re-stamped (title = ``name``
    or ``"Fork of <old title>"``, ``isCustomTitle`` true, created/updated =
    now; ``workDir`` and ``agents`` kept as copied), a line is appended to
    ``session_index.jsonl``, and the fork is pinned as the workspace's
    current conversation so it is the row's main session the moment it
    exists - a fork is shadowed by the actively-written parent otherwise
    (see ``set_current_pin``). Kimi picks the fork up as a normal session:
    ``kimi -S <new-id>`` resumes it.

    Returns ``{"session_id", "forked_from"}`` on success,
    ``{"error": "session_not_found"}`` when the source session is gone,
    ``{"error": "fork_failed"}`` when the copy or re-stamp fails, and None
    on invalid input.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return None
    if name is not None and not isinstance(name, str):
        return None
    wd_dir = _safe_wd_dir(kimi_root, encoded_path)
    if wd_dir is None:
        return None
    src = wd_dir / session_id
    state = _load_state(src) if src.is_dir() else None
    if state is None:
        return {"error": "session_not_found"}

    new_id = f"session_{uuid.uuid4()}"
    dst = wd_dir / new_id
    try:
        shutil.copytree(src, dst)
        new_state = dict(state)
        old_title = state.get("title")
        old_title = (
            old_title.strip()
            if isinstance(old_title, str) and old_title.strip()
            else session_id
        )
        new_state["title"] = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"Fork of {old_title}"
        )
        new_state["isCustomTitle"] = True
        now = _now_iso_z()
        new_state["createdAt"] = now
        new_state["updatedAt"] = now
        with (dst / STATE_FILENAME).open("w", encoding="utf-8") as fh:
            json.dump(new_state, fh, indent=2)
            fh.write("\n")
        work_dir = state.get("workDir")
        record = {
            "sessionId": new_id,
            "sessionDir": str(dst),
            "workDir": work_dir if isinstance(work_dir, str) else "",
        }
        # Atomic-ish: a single appended line, so a crash mid-write can only
        # leave one torn trailing line, which readers skip.
        with (kimi_root / INDEX_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except (OSError, shutil.Error):
        shutil.rmtree(dst, ignore_errors=True)
        return {"error": "fork_failed"}
    set_current_pin(kimi_root, encoded_path, new_id)
    return {"session_id": new_id, "forked_from": session_id}
