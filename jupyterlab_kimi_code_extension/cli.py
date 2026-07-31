"""Command-line interface: ``jupyterlab_kimi_code``.

Currently one subcommand, ``install-kimi-statusline``: copies the bundled
powerline statusline script (``assets/statusline-command.sh``) into the
user's ``~/.kimi-code`` directory - after an explicit confirmation - and
points ``status_line.command`` in ``tui.toml`` at it.
"""

from __future__ import annotations

import argparse
import re
import shlex
import stat
import sys
from pathlib import Path

from . import sessions

STATUSLINE_FILENAME = "statusline-command.sh"
TUI_FILENAME = "tui.toml"
# Matches an uncommented TOML table header line, e.g. ``[status_line]``;
# commented-out headers (``# [status_line]``) do not match.
_TABLE_HEADER_RE = re.compile(r"\[\s*status_line\s*\]")
_ANY_TABLE_HEADER_RE = re.compile(r"\[.*\]")
# Matches an uncommented ``command = ...`` key line.
_COMMAND_KEY_RE = re.compile(r"command\s*=")


def read_packaged_statusline() -> str:
    """Read the bundled statusline script, returning its text.

    Raises ``ValueError`` when the asset is missing or does not look like a
    shell script, so we never install garbage.
    """
    asset = Path(__file__).resolve().parent / "assets" / STATUSLINE_FILENAME
    try:
        text = asset.read_text(encoding="utf-8")
    except OSError as err:
        raise ValueError(f"bundled statusline asset is not readable: {err}") from err
    if not text.startswith("#!"):
        raise ValueError(f"{asset} does not look like a shell script")
    return text


def _merge_status_line(tui_path: Path, command: str) -> None:
    """Merge ``command`` into the ``[status_line]`` table of ``tui_path``.

    Kimi's contract (see the commented example kimi writes into tui.toml):
    ``command`` is a shell command whose first stdout line replaces footer
    line 1. The merge is line-based so every other setting - and every
    comment - is preserved exactly: an existing uncommented ``command`` key
    inside ``[status_line]`` is replaced in place, a ``[status_line]`` table
    without one gains the key right after its header, and a file without the
    table gets a new one appended. Raises ``ValueError`` when an existing
    tui.toml is unreadable - better to stop than to clobber a hand-edited
    file.
    """
    lines: list[str] = []
    if tui_path.is_file():
        try:
            lines = tui_path.read_text(encoding="utf-8").splitlines()
        except OSError as err:
            raise ValueError(f"{tui_path} is not readable: {err}") from err

    # TOML basic string: a backslash or a double quote in the command has to
    # be escaped, else the written tui.toml no longer parses at all.
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'command = "{escaped}"'
    header_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _TABLE_HEADER_RE.fullmatch(stripped):
            header_idx = i
            break

    if header_idx is not None:
        # Section span: up to the next table header or end of file.
        end = len(lines)
        for j in range(header_idx + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped.startswith("#") and _ANY_TABLE_HEADER_RE.fullmatch(stripped):
                end = j
                break
        for j in range(header_idx + 1, end):
            stripped = lines[j].strip()
            if stripped.startswith("#"):
                continue
            if _COMMAND_KEY_RE.match(stripped):
                lines[j] = new_line
                break
        else:
            lines.insert(header_idx + 1, new_line)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[status_line]")
        lines.append(new_line)

    tui_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_kimi_statusline(kimi_dir: Path, script_text: str) -> Path:
    """Write the statusline into ``kimi_dir`` and wire tui.toml.

    Writes ``statusline-command.sh`` (marked executable) and merges a
    ``command`` entry into the ``[status_line]`` table of
    ``kimi_dir/tui.toml``, preserving every other line. Returns the
    installed script path. Raises ``ValueError`` when an existing tui.toml
    is unreadable - better to stop than to clobber a hand-edited file.
    """
    kimi_dir.mkdir(parents=True, exist_ok=True)
    dest = kimi_dir / STATUSLINE_FILENAME
    dest.write_text(script_text, encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # Shell-quote the path so a kimi-dir containing spaces - or an apostrophe
    # - survives kimi's shell word-splitting of the command. The TOML string
    # quoting on top of it is ``_merge_status_line``'s job.
    _merge_status_line(kimi_dir / TUI_FILENAME, f"bash {shlex.quote(str(dest))}")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jupyterlab_kimi_code",
        description="Companion CLI for jupyterlab_kimi_code_extension.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser(
        "install-kimi-statusline",
        help="Install the bundled powerline statusline into ~/.kimi-code "
        "and point status_line.command in tui.toml at it.",
    )
    install.add_argument(
        "--kimi-dir",
        type=Path,
        default=sessions.kimi_code_home(),
        help="Kimi directory to install into "
        "(default: $KIMI_CODE_HOME, else ~/.kimi-code)",
    )
    install.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    print(
        f"This installs the bundled statusline script into "
        f"{args.kimi_dir / STATUSLINE_FILENAME} and sets "
        f"status_line.command in {args.kimi_dir / TUI_FILENAME}."
    )
    if not args.yes:
        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Piped/closed stdin or Ctrl-C at the prompt: no answer is
            # no consent.
            print("Aborted.")
            return 1
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0
    try:
        script_text = read_packaged_statusline()
        dest = install_kimi_statusline(args.kimi_dir, script_text)
    except (ValueError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"Installed {dest}")
    print(f"Updated {args.kimi_dir / TUI_FILENAME} (status_line)")
    print("Restart Kimi Code to see the status line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
