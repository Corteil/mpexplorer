import re
import subprocess
import sys
from pathlib import Path

import mpexplorer

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
    assert mpexplorer.__version__ == declared


def test_changelog_has_current_version():
    assert "## [%s]" % mpexplorer.__version__ in (ROOT / "CHANGELOG.md").read_text()


def test_version_flag_needs_no_gtk():
    out = subprocess.run([sys.executable, "-m", "mpexplorer", "--version"], cwd=ROOT,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert out.stdout.strip() == "mpexplorer %s" % mpexplorer.__version__
