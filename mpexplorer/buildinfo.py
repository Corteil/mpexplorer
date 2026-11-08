"""Which build is running: the version plus the git commit and its date (Help > About, --version). GUI-free.

A git clone (the Pi runs one) asks git when asked. A frozen build (the Windows exe) has no .git folder, so the
PyInstaller spec writes _build.json into the package while building; an installed wheel has neither and shows only
the version.
"""
import json
import os
import subprocess

from . import __version__

BUILD_FILE = "_build.json"
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def from_git(root, run=subprocess.run):
    """{'commit', 'date', 'changed'} for the git checkout at root, else None (not a checkout, or no git)."""
    if not os.path.exists(os.path.join(root, ".git")):
        return None

    def git(*args):      # safe.directory: only reading, and a build's git may not own the checkout (CI, MSYS2)
        out = run(["git", "-C", root, "-c", "safe.directory=*"] + list(args), capture_output=True, text=True, timeout=5,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode:
            raise OSError(out.stderr.strip() or "git failed")
        return out.stdout.strip()
    try:
        commit = git("rev-parse", "--short=7", "HEAD")
        date = git("log", "-1", "--format=%cd", "--date=short")
        changed = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "date": date, "changed": changed}


def read_build_file(package_dir=PACKAGE_DIR):
    """What the build wrote into the package, else None."""
    try:
        with open(os.path.join(package_dir, BUILD_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("commit") else None


def write_build_file(path, root, env=None, run=subprocess.run):
    """For the build: record the commit being built (and the CI run, if any) at path. Returns the dict written."""
    env = os.environ if env is None else env
    info = from_git(root, run) or {"commit": (env.get("GITHUB_SHA") or "")[:7] or "unknown", "date": "",
                                   "changed": False}
    run_no = env.get("GITHUB_RUN_NUMBER")
    info["source"] = "CI run %s" % run_no if run_no else "local build"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


def build_info(package_dir=PACKAGE_DIR, run=subprocess.run):
    """{'version', 'commit', 'date', 'changed', 'source'}; commit is '' when nothing says which build this is."""
    info = {"version": __version__, "commit": "", "date": "", "changed": False, "source": ""}
    found = read_build_file(package_dir)
    if not found:
        found = from_git(os.path.dirname(package_dir), run)
        if found:
            found["source"] = "git clone"
    if found:
        info.update({k: found.get(k, info[k]) for k in ("commit", "date", "changed", "source")})
    return info


def version_text(info):
    """'0.1.1 (build d39b18e, 2026-11-08)', with ', changed locally' for a clone with edits; just '0.1.1' without."""
    if not info.get("commit"):
        return info["version"]
    parts = ["build " + info["commit"]] + [p for p in (info.get("date"),) if p]
    if info.get("changed"):
        parts.append("changed locally")
    return "%s (%s)" % (info["version"], ", ".join(parts))
