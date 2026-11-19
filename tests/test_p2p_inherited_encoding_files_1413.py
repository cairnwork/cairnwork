"""#1413: source encoding and every changed test-commit path gate inherited removal."""

import base64
import subprocess
from types import SimpleNamespace

import pytest
from test_collect_exposed_1413 import TARGET
from test_p2p_inherited_runtime_1413 import BASE, HONEST, NODE, _side

from cairnwork.p2p_classify_child import classify_payload


def _payload(source, changed=None):
    return {
        "base_ids": [f"{TARGET}::TestA::test_x", NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": changed or [TARGET],
        "exposed": [],
        "sources": {"base": {TARGET: BASE}, "test": {TARGET: source}},
    }


HIDDEN = (
    "import types\nf = lambda self: None\n"
    "types.new_class('Helper', (), exec_body=lambda ns: ns.update({'test_' + 'x': f}))\n"
)
UTF7 = (
    "# -*- coding: utf-7 -*-\n# +AAo-+"
    + base64.b64encode(HIDDEN.encode("utf-16-be")).decode().rstrip("=")
    + "-\n"
)
UNICODE_ESCAPE = "# coding: unicode_escape\n# \\x0a" + HIDDEN.replace("\n", r"\x0a") + "\n"


@pytest.mark.parametrize("cookie", ["# -*- coding: utf-8 -*-\n", "# coding: utf-8\n", ""])
def test_utf8_and_default_encoding_remain_removed(cookie):
    assert NODE in classify_payload(_payload(cookie + HONEST))["removed"]


@pytest.mark.parametrize(
    "source,changed",
    [
        (UNICODE_ESCAPE + HONEST, None),
        ("import evil\n" + HONEST, [TARGET, "tests/evil.pyc"]),
        ("import evil\n" + HONEST, [TARGET, "tests/evil.cpython-312-darwin.so"]),
        (HONEST, [TARGET, "pytest.ini"]),
        (HONEST, [TARGET, "tests/__pycache__/x.cpython-312.pyc"]),
    ],
)
def test_encoding_or_other_changed_file_keeps_inherited_dropped(source, changed):
    assert NODE in classify_payload(_payload(source, changed))["dropped"]


def test_utf7_hidden_code_stays_dropped_with_real_fact(tmp_path):
    side = _side(tmp_path, UTF7 + HONEST, real_fact=True)
    assert NODE in side.dropped and NODE not in side.removed


def test_utf7_hidden_code_stays_dropped_at_payload_level():
    assert NODE in classify_payload(_payload(UTF7 + HONEST))["dropped"]


@pytest.mark.parametrize(
    "prefix",
    [
        "def local():\n    return lambda f: f\n@local()\ndef helper(): pass\n",
        "def local(x):\n    return lambda c: c\n@local(1)\nclass Helper: pass\n",
        "def helper(): pass\ng = helper\n",
        "SOME_NAME = 1\nclass Helper:\n    value = SOME_NAME\n",
    ],
)
def test_allowlist_only_shapes_stay_dropped(prefix):
    assert NODE in classify_payload(_payload(prefix + HONEST))["dropped"]


def test_changed_test_files_count_every_path(tmp_path):
    from cairnwork.p2p_collect import _changed_test_files

    def git(*args):
        cmd = ["git", "-c", "commit.gpgsign=false", "-C", str(tmp_path), *args]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "tests" / "test_a.py").write_text("x = 2\n")
    (tmp_path / "tests" / "evil.pyc").write_bytes(b"compiled")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "README.md").write_text("r\n")
    git("add", "-A")
    git("commit", "-qm", "test")
    head = git("rev-parse", "HEAD")
    task = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit=head)
    assert _changed_test_files(task) == [
        "README.md",
        "pytest.ini",
        "tests/evil.pyc",
        "tests/test_a.py",
    ]
    bad = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit="0" * 40)
    assert _changed_test_files(bad) is None
