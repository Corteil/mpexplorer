import os
import subprocess
import sys
from pathlib import Path

import pytest

from mpexplorer import desktop

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME and XDG_DATA_HOME: never touch the real ~/.local/share or ~/Desktop."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    return tmp_path


def test_install_writes_menu_entry_icon_and_desktop_icon(home):
    (home / "Desktop").mkdir()
    written = desktop.install(home / "Desktop", exec_cmd="/opt/bin/mpexplorer")
    apps = home / "share" / "applications" / "mpexplorer.desktop"
    icon = home / "share" / "icons" / "hicolor" / "256x256" / "apps" / "mpexplorer.png"
    on_desktop = home / "Desktop" / "mpexplorer.desktop"
    assert set(written) == {apps, icon, on_desktop}
    text = apps.read_text()
    assert "Exec=/opt/bin/mpexplorer\n" in text and "Icon=mpexplorer\n" in text and "Path=" not in text
    assert on_desktop.read_text() == text
    assert os.access(apps, os.X_OK) and os.access(on_desktop, os.X_OK)
    assert icon.read_bytes() == (ROOT / "mpexplorer" / "icon.png").read_bytes()


def test_install_without_desktop_folder_skips_desktop_icon(home):
    written = desktop.install(None, exec_cmd="mpexplorer")
    assert len(written) == 2 and not (home / "Desktop").exists()


def test_checkout_fallback_sets_working_folder(home):
    desktop.install(None, exec_cmd="/usr/bin/python3 -m mpexplorer", workdir="/src/mpexplorer")
    text = (home / "share" / "applications" / "mpexplorer.desktop").read_text()
    assert "Exec=/usr/bin/python3 -m mpexplorer\nPath=/src/mpexplorer\n" in text


def test_exec_line_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/home/a b/.local/bin/mpexplorer")
    assert desktop.exec_line() == ('"/home/a b/.local/bin/mpexplorer"', None)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    cmd, workdir = desktop.exec_line()
    assert cmd.endswith(" -m mpexplorer") and workdir == str(ROOT)


def test_desktop_dir_never_returns_home(home, monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=str(home) + "\n"))
    assert desktop.desktop_dir() is None


def test_uninstall_removes_what_install_wrote(home):
    (home / "Desktop").mkdir()
    written = desktop.install(home / "Desktop", exec_cmd="mpexplorer")
    assert set(desktop.uninstall(home / "Desktop")) == set(written)
    assert not any(p.exists() for p in written) and desktop.uninstall(home / "Desktop") == []


def test_cli_flag_needs_no_gtk(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), XDG_DATA_HOME=str(tmp_path / "share"))
    out = subprocess.run([sys.executable, "-m", "mpexplorer", "--install-desktop"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "share" / "applications" / "mpexplorer.desktop").exists()
    out = subprocess.run([sys.executable, "-m", "mpexplorer", "--uninstall-desktop"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and not (tmp_path / "share" / "applications" / "mpexplorer.desktop").exists()


def test_desktop_file_ships_in_the_package():
    text = (ROOT / "pyproject.toml").read_text()
    assert '"mpexplorer.desktop"' in text and (ROOT / "mpexplorer" / "mpexplorer.desktop").exists()


def test_release_notes_have_changelog_and_install_for_the_tag():
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        import release_notes
    finally:
        sys.path.pop(0)
    text = release_notes.notes("v0.1.0")
    assert text.startswith("First release.") and "### Known limits" in text
    assert "git+https://github.com/Corteil/mpexplorer@v0.1.0\n" in text and "{tag}" not in text
    assert "## [0.1.0]" not in text and "## [Unreleased]" not in text
    with pytest.raises(SystemExit):
        release_notes.notes("v9.9.9")
