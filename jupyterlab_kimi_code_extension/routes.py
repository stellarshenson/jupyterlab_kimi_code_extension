"""Tornado API handlers for the Kimi Code sessions extension."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import tornado
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join

from . import sessions as sessions_mod


URL_PREFIX = "jupyterlab-kimi-code-extension"


# A bare uuid4 (no ``session_`` prefix) as kimi's ``-S`` also accepts it; it
# is normalized by prepending the prefix. Full prefixed ids are gated by
# ``sessions_mod.SESSION_ID_RE``.
_BARE_UUID_RE = re.compile(r"[0-9a-f-]{36}")


# bash one-liner that waits until the JL WebSocket client has resized the pty
# from its initial default before clearing and `exec`ing the real argv. The
# previous threshold-based version (rows>=20 && cols>=80) was a no-op because
# terminado's default is 24x80, so `c=80 >= 80` passed on the first iteration
# and we never actually waited.
#
# Strategy: capture the initial size, install a SIGWINCH trap, and loop until
# either SIGWINCH fires OR the size has visibly changed. 5 s timeout fallback
# so we still launch if no client ever connects. After exec, bash is replaced
# by kimi on the same pid - auto-close on exit and the `_tree_has_kimi`
# reuse filter still work.
_INIT_WAITER = (
    "trap 'CHANGED=1' WINCH; "
    "read R0 C0 < <(stty size 2>/dev/null || echo '0 0'); "
    "for i in $(seq 1 50); do "
    'if [ -n "$CHANGED" ]; then break; fi; '
    "read r c < <(stty size 2>/dev/null || echo '0 0'); "
    'if [ "$r" != "$R0" ] || [ "$c" != "$C0" ]; then break; fi; '
    "sleep 0.1; "
    "done; "
    "clear; "
    'exec "$@"'
)


def _wrap_with_init(argv: list[str]) -> list[str]:
    """Prepend the terminal-init waiter so kimi only starts once the JL
    terminal widget has connected and sized the pty to a usable window."""
    return ["/bin/bash", "-c", _INIT_WAITER, "kimi-terminal-init", *argv]


def _process_comm(pid: int) -> str | None:
    if sys.platform != "linux":
        return None
    try:
        with open(f"/proc/{pid}/comm", "r") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _process_children(pid: int) -> list[int]:
    if sys.platform != "linux":
        return []
    try:
        with open(f"/proc/{pid}/task/{pid}/children", "r") as fh:
            return [int(x) for x in fh.read().split() if x.isdigit()]
    except OSError:
        return []


def _process_cwd_link(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _tree_has_kimi(root_pid: int) -> bool:
    """Return True iff any process in the pty's tree has ``comm == kimi``.

    Used to filter the reuse path: a JL terminal whose cwd matches a project
    folder but doesn't actually have kimi running in it (e.g. a plain
    ``bash`` opened at the project) must NOT be reused - the panel should
    spawn a new terminal with ``kimi -S`` instead.
    """
    queue: list[int] = [root_pid]
    while queue:
        pid = queue.pop(0)
        if _process_comm(pid) == "kimi":
            return True
        queue.extend(_process_children(pid))
    return False


def _parse_resume_id(cmdline: bytes) -> str | None:
    """The session id value a kimi cmdline was launched with, or None.

    Kimi resumes with ``-S <id>`` / ``--session <id>`` / ``--session=<id>``.
    A bare ``-S`` opens the interactive picker, and ``-c`` or a bare ``kimi``
    carry no id at all - all of those read back as None, so an unknown-id
    terminal is never claimed for a conversation it is not running. Kept pure
    (takes bytes, not a pid) so it is unit-testable without a live process.
    """
    args = [p.decode("utf-8", "replace") for p in cmdline.split(b"\x00") if p]

    def value_of(flag: str) -> str | None:
        for i, arg in enumerate(args):
            if arg == flag:
                nxt = args[i + 1] if i + 1 < len(args) else ""
                # A following token that is itself a flag means the value is
                # missing/malformed - do not swallow it as the id.
                return None if nxt.startswith("-") else (nxt or None)
            if arg.startswith(flag + "="):
                return arg[len(flag) + 1:] or None
        return None

    return value_of("-S") or value_of("--session")


def _normalize_session_id(value: str | None) -> str | None:
    """Normalize a parsed ``-S`` value to a full session id, or None.

    A full ``session_<uuid>`` is kept as-is; a bare ``<uuid>`` gets the
    prefix prepended; anything else is not a session id at all.
    """
    if not value:
        return None
    if sessions_mod.SESSION_ID_RE.fullmatch(value):
        return value
    if _BARE_UUID_RE.fullmatch(value):
        return "session_" + value
    return None


def _resume_id_from_cmdline(pid: int) -> str | None:
    """Session id the process at ``pid`` is resuming, read from /proc."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return _parse_resume_id(fh.read())
    except OSError:
        return None


def _kimi_session_id(root_pid: int) -> str | None:
    """The session id the pty's kimi is running, parsed from its argv.

    Kimi writes no per-pid session file, so argv is the only handle on a
    terminal's conversation: an id is known only for launches handed one
    explicitly (``kimi -S <id>``, i.e. every extension resume launch). A
    ``-c`` or bare ``kimi`` reads back as None - an unknown id is never
    reused, so such a terminal is never focused for a row it may not be
    running.
    """
    queue: list[int] = [root_pid]
    while queue:
        pid = queue.pop(0)
        if _process_comm(pid) == "kimi":
            session_id = _normalize_session_id(_resume_id_from_cmdline(pid))
            if session_id:
                return session_id
        queue.extend(_process_children(pid))
    return None


def _terminal_cwds(root_pid: int) -> list[str]:
    """Walk the pty's process tree and return ALL distinct live cwds found.

    Only ``/proc/<pid>/cwd`` (the kernel's authoritative current directory)
    is consulted - never the ``PWD`` in ``/proc/<pid>/environ``, which is the
    frozen exec-time environment and, for the pty's root process, is just
    whatever ``PWD`` the Jupyter server itself was launched with (so every
    terminal would otherwise report the server's startup directory). Walking
    the whole tree still covers the case where ``bash`` (the pty root) sits
    in the project folder while ``kimi`` or a background sub-shell has cd'd
    elsewhere. The frontend matches a project_path against ANY entry.
    """
    seen: set[str] = set()
    out: list[str] = []
    queue: list[int] = [root_pid]
    while queue:
        pid = queue.pop(0)
        source = _process_cwd_link(pid)
        if (
            source
            and source.startswith("/")
            and not source.startswith(("/proc/", "/sys/", "/dev/"))
        ):
            try:
                resolved = os.path.realpath(source)
            except OSError:
                resolved = source
            if resolved not in seen and os.path.isdir(resolved):
                seen.add(resolved)
                out.append(resolved)
        for child in _process_children(pid):
            queue.append(child)
    return out


class StatusHandler(APIHandler):
    """Reports whether the extension should be active.

    Active iff the ``kimi`` binary is on ``PATH``.
    """

    @tornado.web.authenticated
    def get(self) -> None:
        binary = sessions_mod.kimi_binary_available()
        # ``server_root_dir`` is the root path Jupyter is serving notebooks
        # from. Fall back to the user's home directory if unset. Expand a
        # leading ``~`` - some deployments (e.g. JupyterHub) leave the
        # setting as ``~/workspace``, and the frontend compares it against
        # absolute session paths, so an unexpanded ``~`` never matches.
        root_dir = os.path.expanduser(
            self.settings.get("server_root_dir") or "~"
        )
        self.finish(json.dumps({
            "enabled": binary is not None,
            "kimi_path": binary,
            "root_dir": root_dir,
        }))


class SessionsListHandler(APIHandler):
    """Returns the workspace session list.

    Off the IOLoop: the listing walks the sessions tree, scans wire logs for
    message counts, AND shells out to ``git`` per unique
    workspace root, and this endpoint is polled every 30s - run inline it
    would stall the whole server (kernels, terminals, contents) for the
    duration of each poll.
    """

    @tornado.web.authenticated
    async def get(self) -> None:
        rows = await asyncio.get_running_loop().run_in_executor(
            None, sessions_mod.list_sessions
        )
        self.finish(json.dumps({"sessions": rows}))


class SessionFavouriteHandler(APIHandler):
    """Toggle favourite flag for a project path.

    Body: ``{"project_path": "...", "favourite": true|false}``
    """

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        project_path = body.get("project_path")
        favourite = body.get("favourite")
        if not isinstance(project_path, str) or not isinstance(favourite, bool):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        favs = sessions_mod.toggle_favourite(
            sessions_mod.kimi_code_home(), project_path, favourite
        )
        self.finish(json.dumps({"favourites": favs}))


class SessionRemoveHandler(APIHandler):
    """Remove a workspace's Kimi history.

    Body: ``{"encoded_path": "wd_test_7a1e2234555b"}``

    Honours JupyterLab's ``ContentsManager.delete_to_trash`` setting: when
    enabled the workspace session dir is sent to the desktop trash, otherwise
    it is deleted permanently (a permanent delete is also the fallback if the
    trash move fails).
    """

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        encoded_path = body.get("encoded_path")
        if not isinstance(encoded_path, str):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        to_trash = bool(getattr(self.contents_manager, "delete_to_trash", True))
        ok = sessions_mod.remove_workspace(
            sessions_mod.kimi_code_home(), encoded_path, to_trash=to_trash
        )
        if not ok:
            self.set_status(400)
            self.finish(json.dumps({"error": "remove_failed"}))
            return
        self.finish(json.dumps({"removed": encoded_path}))


class SessionCleanupHandler(APIHandler):
    """Remove a workspace's parallel sessions, keeping only the current one.

    Body: ``{"encoded_path": "wd_test_7a1e2234555b"}``

    Honours JupyterLab's ``ContentsManager.delete_to_trash`` setting the same
    way ``SessionRemoveHandler`` does.
    """

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        encoded_path = body.get("encoded_path")
        if not isinstance(encoded_path, str):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        to_trash = bool(getattr(self.contents_manager, "delete_to_trash", True))
        removed = sessions_mod.cleanup_parallel_sessions(
            sessions_mod.kimi_code_home(), encoded_path, to_trash=to_trash
        )
        if removed is None:
            self.set_status(400)
            self.finish(json.dumps({"error": "cleanup_failed"}))
            return
        self.finish(json.dumps({"removed_count": removed}))


class SessionBranchesHandler(APIHandler):
    """List a workspace's other conversation sessions ("branches").

    ``GET sessions/branches?encoded_path=wd_test_7a1e2234555b`` returns
    ``{"current", "branches": [{"session_id", "file_mtime",
    "label"}]}`` - newest first, current excluded. The frontend
    shows the most recent in the submenu and the rest via "More...".

    Off the IOLoop on its own merits: the listing opens and stats every
    branch state.json to read its title and activity, unbounded by session
    count. It makes no subprocess call - that is the sessions listing's
    reason, not this one.
    """

    @tornado.web.authenticated
    async def get(self) -> None:
        encoded_path = self.get_query_argument("encoded_path", default="")
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            sessions_mod.list_branches,
            sessions_mod.kimi_code_home(),
            encoded_path,
        )
        if result is None:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_encoded_path"}))
            return
        self.finish(json.dumps(result))


class SessionSwitchHandler(APIHandler):
    """Switch a workspace's current conversation to another branch.

    Body: ``{"encoded_path": "wd_test_7a1e2234555b", "session_id":
    "session_<uuid>"}``. Touches the branch state.json's mtime so the
    file-activity resolution makes it the row's current session, and pins it
    durably. 404 ``branch_not_found`` when the session dir no longer exists
    (removed between menu display and click).
    """

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        encoded_path = body.get("encoded_path")
        session_id = body.get("session_id")
        if not isinstance(encoded_path, str) or not isinstance(session_id, str):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        result = sessions_mod.switch_branch(
            sessions_mod.kimi_code_home(), encoded_path, session_id
        )
        if result is None:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        if result.get("error") == "branch_not_found":
            self.set_status(404)
            self.finish(json.dumps(result))
            return
        self.finish(json.dumps(result))


class SessionDeleteBranchesHandler(APIHandler):
    """Delete selected branch sessions from a workspace dir.

    Body: ``{"encoded_path": "wd_test_7a1e2234555b", "session_ids":
    ["session_<uuid>", ...]}``. The current session is never deleted;
    missing session dirs are treated as already deleted. Honours JupyterLab's
    ``ContentsManager.delete_to_trash`` setting the same way
    ``SessionCleanupHandler`` does.
    """

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        encoded_path = body.get("encoded_path")
        session_ids = body.get("session_ids")
        if not isinstance(encoded_path, str) or not isinstance(session_ids, list):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        to_trash = bool(getattr(self.contents_manager, "delete_to_trash", True))
        removed = sessions_mod.delete_branches(
            sessions_mod.kimi_code_home(), encoded_path, session_ids, to_trash=to_trash
        )
        if removed is None:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        self.finish(json.dumps({"removed_count": removed}))


class SessionForkHandler(APIHandler):
    """Branch a session by copying its session dir under a new id.

    Body: ``{"encoded_path": "wd_test_7a1e2234555b", "session_id":
    "session_<uuid>", "name": "optional label"}``. Kimi has no fork flag of
    its own, so the extension forks by copying ``session_<uuid>`` to
    ``session_<new-uuid>``, stamping the fork's title, appending the
    ``session_index.jsonl`` line, and pinning the fork as the workspace's
    current conversation - all synchronously, so the frontend can immediately
    launch ``kimi -S <new-id>``. 404 ``session_not_found`` when the source
    session is gone, 500 ``fork_failed`` when the copy fails.
    """

    _ALLOWED_KEYS = {"encoded_path", "session_id", "name"}

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict) or any(
            key not in self._ALLOWED_KEYS for key in body
        ):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        encoded_path = body.get("encoded_path")
        session_id = body.get("session_id")
        name = body.get("name")
        if not isinstance(encoded_path, str) or not isinstance(session_id, str):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        if name is not None:
            if not isinstance(name, str):
                self.set_status(400)
                self.finish(json.dumps({"error": "invalid_body"}))
                return
            name = name.strip()
            # A fork name becomes the session title: bounded, and free of
            # control characters so it can never smuggle a newline into the
            # panel or the wire log.
            if (
                not name
                or len(name) > 120
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
            ):
                self.set_status(400)
                self.finish(json.dumps({"error": "invalid_body"}))
                return
        result = sessions_mod.fork_session(
            sessions_mod.kimi_code_home(), encoded_path, session_id, name=name
        )
        if result is None:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_encoded_path"}))
            return
        if result.get("error") == "session_not_found":
            self.set_status(404)
            self.finish(json.dumps(result))
            return
        if result.get("error") == "fork_failed":
            self.set_status(500)
            self.finish(json.dumps(result))
            return
        self.finish(json.dumps(result))


class TerminalCwdHandler(APIHandler):
    """Return the cwd of the deepest shell child of a JL terminal.

    Used by the frontend to match an existing terminal tab to a project
    folder without persisting any state in the browser.

    Deliberately still synchronous, unlike the two listing handlers: this
    reads ``/proc`` only - bounded filesystem work with no subprocess, so it
    does not need the executor those two were moved onto.
    """

    @tornado.web.authenticated
    def get(self, terminal_name: str) -> None:
        terminal_manager = self.settings.get("terminal_manager")
        if terminal_manager is None:
            self.set_status(503)
            self.finish(json.dumps({"error": "terminal_service_unavailable"}))
            return
        # Non-creating lookup: terminado's ``NamedTermManager.get_terminal``
        # is get-or-CREATE, so probing a stale name through it would spawn a
        # ghost terminal instead of 404ing.
        terminal = getattr(terminal_manager, "terminals", {}).get(terminal_name)
        if terminal is None:
            self.set_status(404)
            self.finish(json.dumps({"error": "terminal_not_found"}))
            return
        ptyproc = getattr(terminal, "ptyproc", None)
        if ptyproc is None or not hasattr(ptyproc, "pid"):
            self.set_status(500)
            self.finish(json.dumps({"error": "no_pty"}))
            return
        cwds = _terminal_cwds(ptyproc.pid)
        has_kimi = _tree_has_kimi(ptyproc.pid)
        # The conversation the running kimi is on, parsed from its argv - an
        # id is known only for launches handed one explicitly (kimi -S <id>),
        # so the frontend reuses a terminal only when it provably runs the
        # clicked row's conversation. None for -c / bare / user-opened
        # terminals: an unknown id is never reused.
        session_id = _kimi_session_id(ptyproc.pid)
        self.finish(json.dumps({
            "terminal_name": terminal_name,
            "cwds": cwds,
            "has_kimi": has_kimi,
            "session_id": session_id,
        }))


class LaunchKimiTerminalHandler(APIHandler):
    """Spawn a JL terminal whose pty's only process is ``kimi``.

    With ``session_id`` in the body the session is resumed (``kimi -S
    <id>``); without it a bare ``kimi`` starts a brand-new session whose id
    kimi itself assigns (it appears on the next refresh). ``yolo`` appends
    ``--yolo`` to either form. No other body keys are accepted.

    Bypasses ``terminal:create-new`` (which spawns the user's $SHELL) so the
    terminal tab shows kimi immediately without any visible bash. Uses
    terminado's per-call ``shell_command`` option through
    ``jupyter_server_terminals``' ``TerminalManager.create``. A short bash
    waiter (``_INIT_WAITER``) ``exec``s into kimi only after the WebSocket
    client has resized the pty to a usable window, so the TUI sees a real
    terminal size at launch instead of the pty's tiny default.
    """

    _ALLOWED_KEYS = {"project_path", "session_id", "yolo"}

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_json"}))
            return
        if not isinstance(body, dict) or any(
            key not in self._ALLOWED_KEYS for key in body
        ):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        project_path = body.get("project_path")
        session_id = body.get("session_id")
        yolo = body.get("yolo", False)
        if not isinstance(yolo, bool):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_body"}))
            return
        # The launch dir must exist AND stay under the served root - the
        # frontend derives it from a session row, but the body is user input
        # and a terminal spawns a real shell there.
        root_dir = os.path.realpath(
            os.path.expanduser(self.settings.get("server_root_dir") or "~")
        )
        if not isinstance(project_path, str) or not project_path:
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_project_path"}))
            return
        project_real = os.path.realpath(project_path)
        # ``commonpath`` rather than a prefix check: ``root_dir + os.sep`` is
        # "//" when the served root is "/", which no realpath ever starts
        # with, so the prefix form rejected everything under a root of "/".
        try:
            under_root = os.path.commonpath([project_real, root_dir]) == root_dir
        except ValueError:
            under_root = False
        if not under_root or not os.path.isdir(project_path):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_project_path"}))
            return
        # ``session_id`` is optional: absent/None means "start a new kimi
        # session" instead of resuming an existing one.
        if session_id is not None and (
            not isinstance(session_id, str)
            or not sessions_mod.SESSION_ID_RE.fullmatch(session_id)
        ):
            self.set_status(400)
            self.finish(json.dumps({"error": "invalid_session_id"}))
            return
        kimi = sessions_mod.kimi_binary_available()
        if not kimi:
            self.set_status(503)
            self.finish(json.dumps({"error": "kimi_not_found"}))
            return
        terminal_manager = self.settings.get("terminal_manager")
        if terminal_manager is None:
            self.set_status(503)
            self.finish(json.dumps({"error": "terminal_service_unavailable"}))
            return
        argv = [kimi, "-S", session_id] if session_id else [kimi]
        if yolo:
            argv.append("--yolo")
        model = terminal_manager.create(
            shell_command=_wrap_with_init(argv),
            cwd=project_path,
        )
        # ``jupyter_server_terminals``' TerminalManager.create returns a
        # MODEL dict carrying at least the ``name`` field.
        terminal_name = model["name"]
        # A new session supersedes a prior switch: clear the workspace's pin
        # so recency resumes and the new conversation (newest on disk once it
        # writes) becomes current, instead of staying behind a pinned branch.
        # A resume launch (with session_id) never touches pins - the fork
        # endpoint already pinned a freshly forked branch itself.
        if not session_id:
            kimi_root = sessions_mod.kimi_code_home()
            wd_id = sessions_mod.workspace_id_for_root(kimi_root, project_path)
            if wd_id is not None:
                sessions_mod.clear_current_pin(kimi_root, wd_id)
        self.finish(json.dumps({"terminal_name": terminal_name}))


def setup_route_handlers(web_app) -> None:
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"]

    handlers = [
        (url_path_join(base_url, URL_PREFIX, "status"), StatusHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions"), SessionsListHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions", "favourite"), SessionFavouriteHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions", "remove"), SessionRemoveHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions", "cleanup"), SessionCleanupHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions", "branches"), SessionBranchesHandler),
        (url_path_join(base_url, URL_PREFIX, "sessions", "switch"), SessionSwitchHandler),
        (
            url_path_join(base_url, URL_PREFIX, "sessions", "delete-branches"),
            SessionDeleteBranchesHandler,
        ),
        (
            url_path_join(base_url, URL_PREFIX, "sessions", "fork"),
            SessionForkHandler,
        ),
        (
            url_path_join(base_url, URL_PREFIX, "terminal-cwd", r"([^/]+)"),
            TerminalCwdHandler,
        ),
        (
            url_path_join(base_url, URL_PREFIX, "launch-terminal"),
            LaunchKimiTerminalHandler,
        ),
    ]

    web_app.add_handlers(host_pattern, handlers)
