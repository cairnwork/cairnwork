"""The recovery drill's wait helper must only match a real pytest run, never a path that says so."""

import pytest
from recovery_wait import is_pytest_command

BLOCK = [
    "/Library/Developer/CommandLineTools/usr/bin/git --no-replace-objects "
    "-c core.hooksPath=/dev/null "
    "-C /private/var/folders/qy/x/T/pytest-of-james/pytest-1164/test_x0/runs/a9cc-x-v07.work "
    "clone --quiet --no-local",
    "/usr/bin/git -C /tmp/pytest-of-u/pytest-1/repo rev-parse HEAD",
    "/usr/bin/sandbox-exec -f /tmp/pytest-of-u/pytest-1/p.sb /bin/sleep 5",
    "sh -c cd /tmp/pytest-of-u/pytest-3/x && true",
    "python3 -c import time; time.sleep(20) /tmp/pytest-of-u/pytest-1/x",
    "",
]
PASS = [
    "/x/.venv/bin/python -P -m pytest -q -p no:cacheprovider -c /tmp/runs/a-x-v0.pytest.ini",
    "/usr/bin/sandbox-exec -f /tmp/p.sb /x/.venv/bin/python -P -m pytest -q",
    "python3.12 -m pytest tests",
]


@pytest.mark.parametrize("cmd", BLOCK)
def test_a_path_or_helper_that_mentions_pytest_is_not_pytest(cmd):
    assert not is_pytest_command(cmd)


@pytest.mark.parametrize("cmd", PASS)
def test_a_python_dash_m_pytest_run_is_pytest(cmd):
    assert is_pytest_command(cmd)
