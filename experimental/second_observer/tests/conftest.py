"""The sandbox marker of tests/conftest.py, for this directory (testpaths only names tests/)."""

import os

import pytest

import cairnwork_sandbox


def pytest_collection_modifyitems(config, items):
    if cairnwork_sandbox.select() is not None:
        return
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1":
        raise pytest.UsageError("CAIRNWORK_REQUIRE_SANDBOX=1 but sandbox-exec is unavailable")
    skip = pytest.mark.skip(reason="needs macOS sandbox-exec")
    for item in items:
        if "sandbox" in item.keywords:
            item.add_marker(skip)
