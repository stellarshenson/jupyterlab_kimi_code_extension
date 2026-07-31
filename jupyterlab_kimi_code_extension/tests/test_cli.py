"""Tests for the ``jupyterlab_kimi_code`` companion CLI."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from jupyterlab_kimi_code_extension import cli

SCRIPT = "#!/bin/bash\necho statusline\n"


def _command_lines(text: str) -> list[str]:
    """Uncommented ``command = ...`` lines of a tui.toml body."""
    return [
        line.strip() for line in text.splitlines()
        if line.strip().startswith("command") and "=" in line
    ]


def _command_line(dest: Path) -> str:
    """The tui.toml ``command`` line the installer writes for ``dest``."""
    return f'command = "bash {shlex.quote(str(dest))}"'


def test_install_writes_script_and_wires_tui(tmp_path: Path) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    assert dest == kimi_dir / "statusline-command.sh"
    assert dest.read_text() == SCRIPT
    assert os.access(dest, os.X_OK)
    text = (kimi_dir / "tui.toml").read_text()
    assert "[status_line]" in text
    assert _command_line(dest) in text


def test_install_replaces_existing_command_in_place(tmp_path: Path) -> None:
    """An existing ``command`` inside ``[status_line]`` is replaced on its
    own line; every other key, table, and comment survives byte-for-byte."""
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    (kimi_dir / "tui.toml").write_text(
        "# Kimi TUI configuration\n"
        'theme = "dark"\n'
        "\n"
        "[status_line]\n"
        "# refresh every second\n"
        "interval = 1\n"
        'command = "old-command"\n'
        "\n"
        "[keys]\n"
        'quit = "q"\n'
    )
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    lines = (kimi_dir / "tui.toml").read_text().splitlines()
    assert 'command = "old-command"' not in lines
    assert _command_lines("\n".join(lines)) == [_command_line(dest)]
    # Every other line is preserved, including comments and the other table.
    assert "# Kimi TUI configuration" in lines
    assert 'theme = "dark"' in lines
    assert "# refresh every second" in lines
    assert "interval = 1" in lines
    assert "[keys]" in lines
    assert 'quit = "q"' in lines
    # The replacement stayed inside [status_line] (before the next table).
    assert lines.index(_command_line(dest)) < lines.index("[keys]")


def test_install_adds_command_to_bare_status_line_table(tmp_path: Path) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    (kimi_dir / "tui.toml").write_text(
        "[status_line]\n"
        "interval = 2\n"
    )
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    lines = (kimi_dir / "tui.toml").read_text().splitlines()
    # The key is inserted right after the table header.
    assert lines[0] == "[status_line]"
    assert lines[1] == _command_line(dest)
    assert "interval = 2" in lines


def test_install_appends_table_when_absent(tmp_path: Path) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    (kimi_dir / "tui.toml").write_text('theme = "dark"\n')
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    text = (kimi_dir / "tui.toml").read_text()
    assert text.startswith('theme = "dark"\n')
    lines = text.splitlines()
    idx = lines.index("[status_line]")
    assert lines[idx + 1] == _command_line(dest)


def test_install_ignores_commented_out_header(tmp_path: Path) -> None:
    """Kimi ships tui.toml with a commented-out example - the installer must
    add a real table rather than 'filling in' the comment block."""
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    (kimi_dir / "tui.toml").write_text(
        "# [status_line]\n"
        '# command = "example"\n'
    )
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    text = (kimi_dir / "tui.toml").read_text()
    assert "# [status_line]" in text
    assert '# command = "example"' in text
    assert "\n[status_line]\n" in text
    assert _command_lines(text) == [_command_line(dest)]


def test_install_is_idempotent(tmp_path: Path) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    once = (kimi_dir / "tui.toml").read_text()
    cli.install_kimi_statusline(kimi_dir, SCRIPT)
    twice = (kimi_dir / "tui.toml").read_text()
    assert twice == once
    assert twice.count("[status_line]") == 1
    assert _command_lines(twice) == [_command_line(dest)]


@pytest.mark.parametrize("odd_dir", ["a'postrophe", 'a"quote', "back\\slash"])
def test_install_command_survives_shell_and_toml_metacharacters(
    tmp_path: Path, odd_dir: str
) -> None:
    """A kimi-dir path carrying a quote or a backslash must still yield a
    parseable tui.toml whose command resolves back to the exact script path:
    the shell quoting and the TOML string escaping are separate layers and
    both have to hold."""
    import tomllib  # stdlib from 3.11; only this test needs it

    kimi_dir = tmp_path / odd_dir / ".kimi-code"
    dest = cli.install_kimi_statusline(kimi_dir, SCRIPT)
    parsed = tomllib.loads((kimi_dir / "tui.toml").read_text())
    command = parsed["status_line"]["command"]
    assert shlex.split(command) == ["bash", str(dest)]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_install_refuses_unreadable_tui(tmp_path: Path) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    tui = kimi_dir / "tui.toml"
    tui.write_text("[status_line]\n")
    tui.chmod(0)
    try:
        with pytest.raises(ValueError):
            cli.install_kimi_statusline(kimi_dir, SCRIPT)
    finally:
        tui.chmod(0o644)
    # The hand-edited file is untouched.
    assert tui.read_text() == "[status_line]\n"


def test_statusline_get_pwd_keeps_spaces_inside_directory_names(
    tmp_path: Path,
) -> None:
    """The trimmed pwd segment joins components with explicit '/' - the
    earlier ``${end[*]}`` + ``tr`` join turned every space INSIDE a directory
    name into a slash too."""
    deep = tmp_path / "a" / "b b" / "c c" / "d d"
    deep.mkdir(parents=True)
    script = (
        Path(cli.__file__).resolve().parent / "assets" / cli.STATUSLINE_FILENAME
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'cd "$1" || exit 1; . "$2" >/dev/null 2>&1; get_pwd',
            "statusline-test",
            str(deep),
            str(script),
        ],
        capture_output=True,
        text=True,
        # A HOME that cannot prefix-match the test dir, so the "~" rewrite
        # never fires and the assertion stays about the join alone.
        env={**os.environ, "HOME": str(tmp_path / "elsewhere")},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "/.../b b/c c/d d"


def test_read_packaged_statusline_returns_bundled_script() -> None:
    text = cli.read_packaged_statusline()
    assert text.startswith("#!")
    assert len(text) > 100  # a real script, not a stub


def test_read_packaged_statusline_rejects_non_script(
    tmp_path: Path, monkeypatch
) -> None:
    assets = tmp_path / "pkg" / "assets"
    assets.mkdir(parents=True)
    (assets / cli.STATUSLINE_FILENAME).write_text("echo no shebang\n")
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "pkg" / "cli.py"))
    with pytest.raises(ValueError):
        cli.read_packaged_statusline()


def test_read_packaged_statusline_errors_when_asset_missing(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pkg").mkdir()
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "pkg" / "cli.py"))
    with pytest.raises(ValueError):
        cli.read_packaged_statusline()


def test_main_asks_for_confirmation_and_aborts_on_no(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    code = cli.main(["install-kimi-statusline", "--kimi-dir", str(kimi_dir)])
    assert code == 0
    assert "Aborted." in capsys.readouterr().out
    assert not (kimi_dir / cli.STATUSLINE_FILENAME).exists()
    assert not (kimi_dir / cli.TUI_FILENAME).exists()


@pytest.mark.parametrize("raised", [EOFError, KeyboardInterrupt])
def test_main_aborts_cleanly_on_unanswered_prompt(
    tmp_path: Path, monkeypatch, capsys, raised
) -> None:
    """A piped/closed stdin raises EOFError and Ctrl-C raises
    KeyboardInterrupt at the prompt - both are "no answer, no consent" and
    must abort with rc 1, never traceback."""
    kimi_dir = tmp_path / ".kimi-code"

    def unanswered(prompt):
        raise raised

    monkeypatch.setattr("builtins.input", unanswered)
    code = cli.main(["install-kimi-statusline", "--kimi-dir", str(kimi_dir)])
    assert code == 1
    assert "Aborted." in capsys.readouterr().out
    assert not (kimi_dir / cli.STATUSLINE_FILENAME).exists()


def test_main_yes_skips_prompt_and_installs(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    monkeypatch.setattr(cli, "read_packaged_statusline", lambda: SCRIPT)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: pytest.fail("prompt shown despite --yes"),
    )
    code = cli.main(
        ["install-kimi-statusline", "--kimi-dir", str(kimi_dir), "--yes"]
    )
    assert code == 0
    assert "Installed" in capsys.readouterr().out
    assert (kimi_dir / cli.STATUSLINE_FILENAME).read_text() == SCRIPT
    assert "[status_line]" in (kimi_dir / cli.TUI_FILENAME).read_text()


def test_main_confirmed_install_installs(
    tmp_path: Path, monkeypatch
) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    monkeypatch.setattr(cli, "read_packaged_statusline", lambda: SCRIPT)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    code = cli.main(["install-kimi-statusline", "--kimi-dir", str(kimi_dir)])
    assert code == 0
    assert (kimi_dir / cli.STATUSLINE_FILENAME).read_text() == SCRIPT


def test_main_default_kimi_dir_honours_env_home(
    tmp_path: Path, monkeypatch
) -> None:
    """Without ``--kimi-dir`` the install lands in ``$KIMI_CODE_HOME`` - the
    same home resolution sessions.py and the statusline itself use."""
    env_home = tmp_path / "kimi-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(env_home))
    monkeypatch.setattr(cli, "read_packaged_statusline", lambda: SCRIPT)
    code = cli.main(["install-kimi-statusline", "--yes"])
    assert code == 0
    assert (env_home / cli.STATUSLINE_FILENAME).read_text() == SCRIPT
    assert "[status_line]" in (env_home / cli.TUI_FILENAME).read_text()


def test_main_installs_the_real_bundled_asset(tmp_path: Path) -> None:
    """End-to-end over the packaged asset: what ships is what installs."""
    kimi_dir = tmp_path / ".kimi-code"
    code = cli.main(
        ["install-kimi-statusline", "--kimi-dir", str(kimi_dir), "--yes"]
    )
    assert code == 0
    dest = kimi_dir / cli.STATUSLINE_FILENAME
    assert dest.read_text().startswith("#!")
    assert os.access(dest, os.X_OK)
    assert _command_line(dest) in (kimi_dir / cli.TUI_FILENAME).read_text()


def test_main_reports_error_and_exits_1(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    kimi_dir = tmp_path / ".kimi-code"
    kimi_dir.mkdir()
    (kimi_dir / cli.TUI_FILENAME).mkdir()  # a directory: the merge write fails
    monkeypatch.setattr(cli, "read_packaged_statusline", lambda: SCRIPT)
    code = cli.main(
        ["install-kimi-statusline", "--kimi-dir", str(kimi_dir), "--yes"]
    )
    assert code == 1
    assert "error:" in capsys.readouterr().err
