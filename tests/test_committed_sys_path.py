"""Comprehensive tests for experimental committed_sys_path.

Covers AC1-AC5, failure modes, and in-process search-path escape counterexamples.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from experimental.committed_sys_path import check_commit, check_source
from experimental.committed_sys_path.cli import main

BASE_DIRS = frozenset({"", ".", "src", "bin", "tests", "src/cairnwork", "src/shadow", "src/src"})


def test_ac1_legitimate_paths_and_negatives() -> None:
    """AC1: Legitimate file-relative paths accept; uncommitted/absolute/escaped reject."""
    legit_code = (
        b"import sys, os\n"
        b"from pathlib import Path\n"
        b"ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        b"BIN = os.path.join(ROOT, 'bin')\n"
        b"sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n"
        b"sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))\n"
        b"sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        b"sys.path.insert(0, BIN)\n"
    )
    v = check_source("src/x.py", None, legit_code, BASE_DIRS)
    assert v.verdict == "ACCEPT"

    negatives = [
        ("os.path.join(os.path.dirname(__file__), 'nodir')", "src/x.py"),
        ("os.path.join(os.path.dirname(__file__), '..', '..', 'outside')", "src/x.py"),
        ("'/tmp/secret'", "src/x.py"),
        ("str(Path.home() / '.local')", "src/x.py"),
    ]
    for expr, fpath in negatives:
        code = f"import sys, os\nfrom pathlib import Path\nsys.path.insert(0, {expr})\n".encode()
        nv = check_source(fpath, None, code, BASE_DIRS)
        assert nv.verdict == "REJECT"
        assert nv.reason == "NON_COMMITTED_SEARCH_PATH"


def test_ac2_dynamic_targets_and_unchanged_base() -> None:
    """AC2: Dynamic path targets reject regardless of write technique; unchanged base accepts."""
    dynamic_snippets = [
        "sys.path.insert(0, tempfile.mkdtemp())",
        "sys.path.append(os.environ.get('TMP', '/tmp'))",
        "sys.path.extend([tempfile.mkdtemp()])",
        "p = sys.path[:]; sys.path[:] = [tempfile.mkdtemp()] + p",
        "sys.path += [tempfile.mkdtemp()]",
        "site.addsitedir(tempfile.mkdtemp())",
        "__path__.insert(0, tempfile.mkdtemp())",
    ]
    write_prefaces = [
        "o = open; o('/tmp/p', 'w').write('x')",
        "import builtins; getattr(builtins, 'open')('/tmp/p', 'w').write('x')",
        "import functools; functools.partial(open, '/tmp/p', 'w')().write('x')",
        "[open][0]('/tmp/p', 'w').write('x')",
        "import bz2; bz2.BZ2File('/tmp/p', 'w')",
        "os.system('echo 1 > /tmp/p')",
    ]
    for i, snip in enumerate(dynamic_snippets):
        pref = write_prefaces[i % len(write_prefaces)]
        code = f"import sys, os, site, tempfile\n{pref}\n{snip}\n".encode()
        res = check_source("src/x.py", None, code, BASE_DIRS)
        assert res.verdict == "REJECT"
        assert res.reason == "NON_COMMITTED_SEARCH_PATH"

    old_code = b"import sys\nsys.path.insert(0, 'bin')\ndef foo(): return 1\n"
    new_code = b"import sys\nsys.path.insert(0, 'bin')\ndef foo(): return 2\n"
    unchanged = check_source("src/x.py", old_code, new_code, BASE_DIRS)
    assert unchanged.verdict == "ACCEPT"


def test_ac3_sys_alias_expressions_and_custom_objects() -> None:
    """AC3: Sys alias tracking rejects dynamic edits; non-sys objects accept."""
    alias_snippets = [
        "from sys import path as sp\nsp.insert(0, d)",
        "import os\nos.sys.path.insert(0, d)",
        "import sys\nm = [sys][0]\nm.path.insert(0, d)",
        "import sys\nm = sys if True else None\nm.path.insert(0, d)",
        "import sys\ngetattr(sys, 'path').insert(0, d)",
        "import importlib\nimportlib.import_module('sys').path.insert(0, d)",
        "import sys\ndef g(): return sys\ng().path.insert(0, d)",
        "import sys\n(m := sys).path.insert(0, d)",
        "import sys\ndef f(m=sys): m.path.insert(0, d)\nf()",
        "import sys\na, m = 1, sys\nm.path.insert(0, d)",
        "import sys\nreg = {'s': sys}\nreg['s'].path.insert(0, d)",
        "import sys\nm = True and sys\nm.path.insert(0, d)",
        "from sys import *\npath += [d]",
        "__path__[:] = [d]",
        "__spec__.submodule_search_locations.insert(0, d)",
    ]
    for snip in alias_snippets:
        code = f"import tempfile\nd = tempfile.mkdtemp()\n{snip}\n".encode()
        res = check_source("src/x.py", None, code, BASE_DIRS)
        assert res.verdict == "REJECT"
        assert res.reason == "NON_COMMITTED_SEARCH_PATH"

    custom_code = (
        b"class Node:\n"
        b"    def __init__(self):\n"
        b"        self.path = []\n"
        b"n = Node()\n"
        b"n.path.insert(0, 'custom')\n"
    )
    assert check_source("src/x.py", None, custom_code, BASE_DIRS).verdict == "ACCEPT"


def test_ac4_shadow_modules_in_committed_dirs() -> None:
    """AC4: Committed dirs containing candidate shadow modules reject; clean dirs accept."""
    code = (
        b"import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shadow'))\n"
    )
    shadowed = check_source(
        "src/x.py", None, code, BASE_DIRS, shadow_dirs=frozenset({"src/shadow"})
    )
    assert shadowed.verdict == "REJECT"
    assert shadowed.reason == "SHADOW_IN_COMMITTED_DIR"

    clean = check_source("src/x.py", None, code, BASE_DIRS, shadow_dirs=frozenset())
    assert clean.verdict == "ACCEPT"


def test_ac5_scope_and_cli_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: Test files & allowlists skipped; subprocess env ignored; CLI default off."""
    code = b"import sys, tempfile\nsys.path.insert(0, tempfile.mkdtemp())\n"
    assert check_source("tests/test_dyn.py", None, code, BASE_DIRS).verdict == "ACCEPT"
    assert (
        check_source("tests/test_dyn.py", None, code, BASE_DIRS, include_tests=True).verdict
        == "REJECT"
    )

    exp_res = check_source(
        "experimental/worker.py", None, code, BASE_DIRS, allowlist_prefixes=("experimental/",)
    )
    assert exp_res.verdict == "ACCEPT"

    safe_env_code = (
        b"import subprocess, machinery\n"
        b"subprocess.run(['python'], env={'PYTHONPATH': '/tmp'})\n"
        b"_H = machinery.FileFinder.path_hook((machinery.SourceFileLoader, ['.py'])).__code__\n"
    )
    assert check_source("src/x.py", None, safe_env_code, BASE_DIRS).verdict == "ACCEPT"

    monkeypatch.delenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", raising=False)
    assert main(["--base", "HEAD~1", "--candidate", "HEAD"]) == 2


def test_failure_modes() -> None:
    """Failure: Unreadable source and git failures fail-closed to ERROR."""
    bad_syntax = b"def syntax error in python("
    err_source = check_source("src/x.py", None, bad_syntax, BASE_DIRS)
    assert err_source.verdict == "ERROR"
    assert err_source.reason == "UNREADABLE_SOURCE"

    with patch("experimental.committed_sys_path._rev_parse", return_value=None):
        commit_err = check_commit("/nowhere", "deadbeef", "12345678")
        assert commit_err.verdict == "ERROR"
        assert commit_err.reason == "INVALID_COMMIT"


def test_cex_counterexamples_matrix() -> None:
    """Verify all counterexamples A0-A9, B1-B4, C1-C7, D1-D3, E1-E4 match specification."""
    cex_cases = {
        "A0": ("import sys, tempfile\nd=tempfile.mkdtemp()\nsys.path.insert(0,d)", "REJECT"),
        "A1": ("from sys import path as sp\nsp.insert(0,d)", "REJECT"),
        "A2": ("import os\nos.sys.path.insert(0,d)", "REJECT"),
        "A3": ("import sys\nm=[sys][0]\nm.path.insert(0,d)", "REJECT"),
        "A4": ("import sys\nm=sys if True else None\nm.path.insert(0,d)", "REJECT"),
        "A5": ("import sys\ngetattr(sys,'path').insert(0,d)", "REJECT"),
        "A6": ("import importlib\nimportlib.import_module('sys').path.insert(0,d)", "REJECT"),
        "A7": ("import sys\ndef g(): return sys\ng().path.insert(0,d)", "REJECT"),
        "A8": ("import sys\n(m:=sys).path.insert(0,d)", "REJECT"),
        "A9": ("import sys\np=sys.path[:]\nsys.path[:]=[d]+p", "REJECT"),
        "B1": (
            "import subprocess,tempfile\n"
            "subprocess.run(['python','x'],env={'PYTHONPATH':tempfile.mkdtemp()})",
            "ACCEPT",
        ),
        "B2": ("import os\nos.environ['PYTHONPATH']=d", "ACCEPT"),
        "B3": ("import site\nsite.addsitedir(d)", "REJECT"),
        "B4": ("open(sp_dir+'/x.pth','w').write(d)", "ACCEPT"),
        "C1": ("import sys\no=open\no(P,'w').write('X=2')\nsys.path.insert(0,d)", "REJECT"),
        "C2": (
            "import sys,builtins\ngetattr(builtins,'open')(P,'w')\nsys.path.insert(0,d)",
            "REJECT",
        ),
        "C3": (
            "import sys,functools\nfunctools.partial(open,P,'w')()\nsys.path.insert(0,d)",
            "REJECT",
        ),
        "C4": ("import sys\n[e][0](P,'w')\nsys.path.insert(0,d)", "REJECT"),
        "C5": ("import sys,bz2\nbz2.BZ2File(P,'w')\nsys.path.insert(0,d)", "REJECT"),
        "C6": ("import sys,os\nos.system('echo X=2 > '+P)\nsys.path.insert(0,d)", "REJECT"),
        "C7": ("import sys\nsys.path.insert(0,d)", "REJECT"),
        "D1": ("import sys\ndef swap(m): m.modules['calc']=fake\nswap(sys)", "ACCEPT"),
        "D2": ("o=open\no('calc.py','w').write('X=2')", "ACCEPT"),
        "D3": (
            "import sys,os\nsys.path.insert(0,os.path.join(os.path.dirname(__file__),'shadow'))",
            "REJECT",
        ),
        "E1": (
            "import sys,os\nsys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','bin'))",
            "ACCEPT",
        ),
        "E2": (
            "import sys,os\nsys.path.insert(0,os.path.join(os.path.dirname(__file__),'src'))",
            "ACCEPT",
        ),
        "E3": (
            "import sys,os\nsys.path.insert(0,os.path.join(os.path.dirname(__file__),'nodir'))",
            "REJECT",
        ),
        "E4": (
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))",
            "ACCEPT",
        ),
    }
    shadows = frozenset({"src/shadow"})
    for tag, (code_str, expected_verdict) in cex_cases.items():
        v = check_source("src/x.py", None, code_str.encode("utf-8"), BASE_DIRS, shadow_dirs=shadows)
        assert v.verdict == expected_verdict, (
            f"CEX case {tag} expected {expected_verdict}, got {v.verdict} ({v.reason})"
        )
