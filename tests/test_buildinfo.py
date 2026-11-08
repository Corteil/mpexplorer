import json
import subprocess

from mpexplorer import __version__, buildinfo


def fake_git(answers, calls=None):
    """A stand-in for subprocess.run: git's reply chosen by its subcommand (after -C <root> -c safe.directory=*)."""
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        out = answers.get(argv[5])
        if isinstance(out, Exception):
            raise out
        if out is None:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")
    return run


CLEAN = {"rev-parse": "d39b18e", "log": "2026-11-08", "status": ""}


def test_from_git_reads_commit_date_and_local_changes(tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []
    assert buildinfo.from_git(str(tmp_path), fake_git(CLEAN, calls)) == \
        {"commit": "d39b18e", "date": "2026-11-08", "changed": False}
    assert calls[0][:5] == ["git", "-C", str(tmp_path), "-c", "safe.directory=*"]       # it may not own the checkout
    edited = dict(CLEAN, status=" M mpexplorer/app.py")
    assert buildinfo.from_git(str(tmp_path), fake_git(edited))["changed"] is True


def test_from_git_gives_none_without_a_checkout_or_without_git(tmp_path):
    assert buildinfo.from_git(str(tmp_path), fake_git(CLEAN)) is None                   # no .git: not even asked
    (tmp_path / ".git").mkdir()
    assert buildinfo.from_git(str(tmp_path), fake_git({})) is None                      # git says no
    assert buildinfo.from_git(str(tmp_path), fake_git({"rev-parse": FileNotFoundError("git")})) is None
    assert buildinfo.from_git(str(tmp_path), fake_git({"rev-parse": subprocess.TimeoutExpired("git", 5)})) is None


def test_a_frozen_build_reads_what_the_build_wrote(tmp_path):
    root, pkg = tmp_path / "src", tmp_path / "pkg"
    root.mkdir()
    pkg.mkdir()
    (root / ".git").mkdir()
    written = buildinfo.write_build_file(str(pkg / buildinfo.BUILD_FILE), str(root), {"GITHUB_RUN_NUMBER": "57"},
                                         fake_git(CLEAN))
    assert written["source"] == "CI run 57"
    info = buildinfo.build_info(str(pkg), fake_git({}))
    assert info == {"version": __version__, "commit": "d39b18e", "date": "2026-11-08", "changed": False,
                    "source": "CI run 57"}
    buildinfo.write_build_file(str(pkg / buildinfo.BUILD_FILE), str(root), {}, fake_git(CLEAN))
    assert json.loads((pkg / buildinfo.BUILD_FILE).read_text())["source"] == "local build"


def test_a_ci_build_is_never_changed_locally(tmp_path):
    # the 0.2.0 release branch's CI exe said "changed locally": MSYS2's git saw the runner's checkout as edited
    (tmp_path / ".git").mkdir()
    out = str(tmp_path / buildinfo.BUILD_FILE)
    edited = fake_git(dict(CLEAN, status=" M mpexplorer/app.py"))
    ci = {"GITHUB_SHA": "d39b18e99", "GITHUB_RUN_NUMBER": "34"}
    info = buildinfo.write_build_file(out, str(tmp_path), ci, edited)
    assert (info["commit"], info["changed"]) == ("d39b18e", False)
    other = buildinfo.write_build_file(out, str(tmp_path), {"GITHUB_SHA": "abc0000"}, edited)
    assert other["changed"] is True                     # a different commit than CI asked for: say so
    assert buildinfo.write_build_file(out, str(tmp_path), {}, edited)["changed"] is True       # a local build


def test_a_build_without_git_uses_the_ci_commit(tmp_path):
    # MSYS2 has no git of its own: the first local build said "build unknown"
    out = str(tmp_path / buildinfo.BUILD_FILE)
    no_git = fake_git({"rev-parse": FileNotFoundError("git")})
    (tmp_path / ".git").mkdir()
    ci = {"GITHUB_SHA": "d39b18e0123456789", "GITHUB_RUN_NUMBER": "58"}
    info = buildinfo.write_build_file(out, str(tmp_path), ci, no_git)
    assert (info["commit"], info["source"]) == ("d39b18e", "CI run 58")
    assert buildinfo.write_build_file(out, str(tmp_path), {}, no_git)["commit"] == "unknown"


def test_a_clone_asks_git_and_an_installed_copy_has_only_the_version(tmp_path):
    pkg = tmp_path / "mpexplorer"
    pkg.mkdir()
    (tmp_path / ".git").mkdir()                                     # the clone's root is the package's parent
    info = buildinfo.build_info(str(pkg), fake_git(CLEAN))
    assert (info["commit"], info["source"]) == ("d39b18e", "git clone")
    (tmp_path / ".git").rmdir()
    assert buildinfo.build_info(str(pkg), fake_git(CLEAN))["commit"] == ""
    (pkg / buildinfo.BUILD_FILE).write_text("not json")             # a broken file is ignored
    assert buildinfo.build_info(str(pkg), fake_git(CLEAN))["commit"] == ""


def test_version_text():
    info = {"version": "0.1.1", "commit": "d39b18e", "date": "2026-11-08", "changed": False}
    assert buildinfo.version_text(info) == "0.1.1 (build d39b18e, 2026-11-08)"
    assert buildinfo.version_text(dict(info, changed=True)) == "0.1.1 (build d39b18e, 2026-11-08, changed locally)"
    assert buildinfo.version_text(dict(info, commit="")) == "0.1.1"
    assert buildinfo.version_text(dict(info, date="")) == "0.1.1 (build d39b18e)"
