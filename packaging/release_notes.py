"""Print a release's notes: the install instructions for its tag, then its CHANGELOG.md section.
Used by .github/workflows/release.yml:  python packaging/release_notes.py v0.1.1 > notes.md
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def changelog_section(version, text):
    m = re.search(r"^## \[%s\][^\n]*\n(.*?)(?=^## \[|\Z)" % re.escape(version), text, re.M | re.S)
    if not m:
        raise SystemExit("CHANGELOG.md has no section for %s" % version)
    return m.group(1).strip()


def notes(tag):
    install = (ROOT / "packaging" / "release_install.md").read_text().replace("{tag}", tag)
    install = install.replace("{version}", tag.removeprefix("v")).strip()
    changes = changelog_section(tag.removeprefix("v"), (ROOT / "CHANGELOG.md").read_text())
    return changes + "\n\n" + install + "\n"


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))
