import os

import pytest


def unavailable(reason):
    """Skip a GUI test module when the toolkit or display is missing.

    CI sets GUI_TESTS_REQUIRED=1 so that a missing toolkit fails there instead of silently skipping.
    """
    if os.environ.get("GUI_TESTS_REQUIRED"):
        pytest.fail("GUI tests are required but cannot run: " + reason, pytrace=False)
    pytest.skip(reason, allow_module_level=True)
