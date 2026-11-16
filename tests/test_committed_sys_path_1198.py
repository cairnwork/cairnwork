"""RED tests for #1198 and #1230 additions: import machinery, loaders, PYTHONPATH, .pth, .py.

Deferred from PR #1230 to follow-up #1198 PR per Layer 1 split ruling.
When implementing #1198, also flip B1, B2, B4, D2 in tests/test_committed_sys_path.py
(_CEX_CASES) back to "REJECT" and update test_ac5_scope_and_cli_default_off accordingly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from experimental.committed_sys_path import check_commit, check_source

BASE_DIRS = frozenset({"", ".", "src", "bin", "tests"})

_MACHINERY_WRITES: list[tuple[str, str]] = [
    ("meta_path_insert", "import sys\nf = object()\nsys.meta_path.insert(0, f)\n"),
    ("meta_path_append", "import sys\nf = object()\nsys.meta_path.append(f)\n"),
    ("meta_path_alias", "import sys\nf = object()\nmp = sys.meta_path\nmp.insert(0, f)\n"),
    ("path_hooks_insert", "import sys\nh = lambda p: None\nsys.path_hooks.insert(0, h)\n"),
    ("path_hooks_setitem", "import sys\nh = lambda p: None\nsys.path_hooks[0] = h\n"),
    ("path_importer_cache_clear", "import sys\nsys.path_importer_cache.clear()\n"),
    ("path_importer_cache_set", "import sys\nf = object()\nsys.path_importer_cache['x'] = f\n"),
    ("sys_modules_setitem", "import sys\nfake = object()\nsys.modules['calc'] = fake\n"),
    ("sys_modules_update", "import sys\nfake = object()\nsys.modules.update({'calc': fake})\n"),
    (
        "sys_modules_setdefault",
        "import sys\nfake = object()\nsys.modules.setdefault('calc', fake)\n",
    ),
    ("sys_modules_del", "import sys\ndel sys.modules['calc']\n"),
    (
        "sys_modules_param_escape_d1",
        "import sys\nfake = object()\ndef f(m):\n    m['calc'] = fake\nf(sys.modules)\n",
    ),
    ("from_sys_meta_path_insert", "from sys import meta_path as mp\nmp.insert(0, object())\n"),
]

_LOADER_AND_EXEC_CALLS: list[tuple[str, str]] = [
    (
        "spec_from_file_location",
        "import importlib.util\np = '/tmp/calc.py'\n"
        "spec = importlib.util.spec_from_file_location('calc', p)\n",
    ),
    (
        "module_from_spec",
        "from importlib.util import module_from_spec\nspec = object()\nmodule_from_spec(spec)\n",
    ),
    ("loader_exec_module", "loader = object()\nmod = object()\nloader.exec_module(mod)\n"),
    (
        "source_file_loader",
        "from importlib.machinery import SourceFileLoader\n"
        "p = '/tmp/c.py'\nSourceFileLoader('c', p)\n",
    ),
    (
        "sourceless_file_loader",
        "from importlib.machinery import SourcelessFileLoader\n"
        "p = '/tmp/c.pyc'\nSourcelessFileLoader('c', p)\n",
    ),
    (
        "extension_file_loader",
        "from importlib.machinery import ExtensionFileLoader\n"
        "p = '/tmp/c.so'\nExtensionFileLoader('c', p)\n",
    ),
    ("file_finder_dir", "from importlib.machinery import FileFinder\nd = '/tmp'\nFileFinder(d)\n"),
    (
        "file_finder_path_hook_call",
        "import importlib.machinery as machinery\n"
        "h = machinery.FileFinder.path_hook((machinery.SourceFileLoader, ['.py']))\n",
    ),
    (
        "path_finder_find_spec",
        "from importlib.machinery import PathFinder\nPathFinder.find_spec('c')\n",
    ),
    ("importlib_reload", "import importlib, json\nimportlib.reload(json)\n"),
    ("runpy_run_path", "import runpy\np = '/tmp/c.py'\nrunpy.run_path(p)\n"),
    ("runpy_run_module", "import runpy\nrunpy.run_module('calc')\n"),
]

_RUNTIME_PATH_STRINGS: list[tuple[str, str]] = [
    ("fstring_path", "import sys\nbase = '/tmp'\nsys.path.insert(0, f'{base}/sub')\n"),
    ("add_path", "import sys\na = '/tmp'\nsys.path.insert(0, a + '/sub')\n"),
    ("percent_format_path", "import sys\nbase = '/tmp'\nsys.path.insert(0, '%s/sub' % base)\n"),
    ("dot_format_path", "import sys\nbase = '/tmp'\nsys.path.insert(0, '{}/sub'.format(base))\n"),
    ("join_list_path", "import sys\na, b = '/tmp', '/sub'\nsys.path.insert(0, ''.join([a, b]))\n"),
    ("chr_concat_path", "import sys\nsys.path.insert(0, chr(47) + 'tmp')\n"),
    ("bytes_decode_path", "import sys\nsys.path.insert(0, b'/tmp'.decode())\n"),
]

_OUT_OF_SCOPE_BLOCKS: list[tuple[str, str]] = [
    (
        "subprocess_pythonpath_b1",
        "import subprocess\n"
        "subprocess.run(['python', '-c', 'pass'], env={'PYTHONPATH': '/tmp/evil'})\n",
    ),
    (
        "subprocess_pythonpath_tmp",
        "import subprocess\nsubprocess.run(['python'], env={'PYTHONPATH': '/tmp'})\n",
    ),
    ("os_environ_setitem", "import os\nos.environ['PYTHONPATH'] = '/tmp/evil'\n"),
    ("os_environ_update", "import os\nos.environ.update({'PYTHONPATH': '/tmp/evil'})\n"),
    ("os_putenv_b2", "import os\nos.putenv('PYTHONPATH', '/tmp/evil')\n"),
    ("pth_open_write_b4", "sp_dir = '/tmp/sp'\nopen(sp_dir + '/x.pth', 'w').write('/tmp')\n"),
    ("pth_pathlib_write_b4", "from pathlib import Path\nPath('site.pth').write_text('/tmp')\n"),
    ("py_open_alias_d2", "o = open\no('calc.py', 'w').write('X=2')\n"),
    ("py_pathlib_write_d2", "from pathlib import Path\nPath('calc.py').write_text('X=2')\n"),
    ("py_shutil_copy_d2", "import shutil\nshutil.copy('a', 'calc.py')\n"),
    ("py_builtins_open_d2", "import builtins\nbuiltins.open('sub/calc.py', 'w')\n"),
    ("py_list_open_d2", "[open][0]('calc.py', 'w')\n"),
    ("py_partial_open_d2", "from functools import partial\npartial(open, 'calc.py', 'w')()\n"),
]


def _git_1198(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return p.stdout.strip()


def _init_repo_1198(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_1198(repo, "init", "-b", "main")
    _git_1198(repo, "config", "user.email", "test@example.com")
    _git_1198(repo, "config", "user.name", "Test")
    (repo / "bin").mkdir()
    (repo / "bin" / "tool.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "src" / "shadow").mkdir(parents=True)
    (repo / "src" / "shadow" / "keep.py").write_text("Y = 1\n", encoding="utf-8")
    (repo / "src" / "old.py").write_text("Z = 1\n", encoding="utf-8")
    _git_1198(repo, "add", ".")
    _git_1198(repo, "commit", "-m", "base")
    return repo, _git_1198(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("case_id", "code"), _MACHINERY_WRITES, ids=[c[0] for c in _MACHINERY_WRITES]
)
def test_1198_import_machinery_writes_reject(case_id: str, code: str) -> None:
    """Writes/escapes on meta_path, path_hooks, path_importer_cache, sys.modules must REJECT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"


def test_1198_readonly_probe_and_string_literal_controls_accept() -> None:
    """Read-only machinery introspection (old_python_probe) and string constants must ACCEPT."""
    probe_code = (
        b"import sys, importlib.machinery as machinery\n"
        b"c = machinery.FileFinder.path_hook((machinery.SourceFileLoader, ['.py'])).__code__\n"
        b"hooks = [x for x in sys.path_hooks]\n"
        b"finders = [f for f in sys.meta_path if isinstance(f, machinery.FileFinder)]\n"
        b"mp = sys.meta_path\n"
        b"_ = len(mp)\n"
        b"print(sys.modules.get('x'), list(sys.modules),\n"
        b"      sys.path_importer_cache.get('y'), list(sys.path_importer_cache))\n"
    )
    res_probe = check_source("src/probe.py", None, probe_code, BASE_DIRS)
    assert res_probe.verdict == "ACCEPT", f"probe: got {res_probe.verdict} ({res_probe.reason})"

    scanner_code = (
        b'PAT = r"SourceFileLoader|spec_from_file_location|PYTHONPATH"\n'
        b'name = "x"\n'
        b'is_dyn = (name in ("__import__", "import_module"))\n'
    )
    res_scan = check_source("src/scan.py", None, scanner_code, BASE_DIRS)
    assert res_scan.verdict == "ACCEPT", f"scan: got {res_scan.verdict} ({res_scan.reason})"


@pytest.mark.parametrize(
    ("case_id", "code"), _LOADER_AND_EXEC_CALLS, ids=[c[0] for c in _LOADER_AND_EXEC_CALLS]
)
def test_1198_importlib_loaders_and_runpy_reject(case_id: str, code: str) -> None:
    """Dynamic importlib loaders, PathFinder/FileFinder calls, reload, and runpy must REJECT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"


@pytest.mark.parametrize(
    ("case_id", "code"), _RUNTIME_PATH_STRINGS, ids=[c[0] for c in _RUNTIME_PATH_STRINGS]
)
def test_1198_runtime_constructed_path_strings_reject(case_id: str, code: str) -> None:
    """Runtime-constructed path strings (f-string, +, %, format, join, chr, decode) REJECT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"


@pytest.mark.parametrize(
    ("case_id", "code"), _OUT_OF_SCOPE_BLOCKS, ids=[c[0] for c in _OUT_OF_SCOPE_BLOCKS]
)
def test_1230_pythonpath_pth_and_runtime_py_writes_reject(case_id: str, code: str) -> None:
    """Uncommitted PYTHONPATH, .pth writes, and cwd/runtime .py writes must REJECT."""
    res = check_source("src/x.py", None, code.encode("utf-8"), BASE_DIRS)
    assert res.verdict == "REJECT", f"{case_id}: got {res.verdict} ({res.reason})"


def test_1230_legitimate_committed_pythonpath_patterns_accept() -> None:
    """Committed PYTHONPATH patterns (agy_shim.py and test_tamper_scan_older_scanner) ACCEPT."""
    agy_shim_code = (
        b"from pathlib import Path\n"
        b"import os\n"
        b"REPO_ROOT = Path(__file__).resolve().parents[2]\n"
        b"env = dict(os.environ)\n"
        b'env["PYTHONPATH"] = os.pathsep.join(\n'
        b'    filter(None, (str(REPO_ROOT), env.get("PYTHONPATH", "")))\n'
        b")\n"
    )
    res_shim = check_source(
        "experimental/local_worker/agy_shim.py",
        None,
        agy_shim_code,
        frozenset({"", "src", "experimental"}),
    )
    assert res_shim.verdict == "ACCEPT", f"agy_shim: got {res_shim.verdict} ({res_shim.reason})"

    scanner_test_code = (
        b"import os, subprocess\n"
        b"from pathlib import Path\n"
        b'src = str(Path(__file__).resolve().parents[1] / "src")\n'
        b'subprocess.run(["py"], env={**os.environ, "PYTHONPATH": src})\n'
    )
    res_test = check_source(
        "tests/test_x.py",
        None,
        scanner_test_code,
        frozenset({"", "src", "tests"}),
        include_tests=True,
    )
    assert res_test.verdict == "ACCEPT", (
        f"older_scanner: got {res_test.verdict} ({res_test.reason})"
    )


def test_git_runtime_py_write_with_dirname_insert_rejected(tmp_path: Path) -> None:
    """Candidate writing bin/helper.py at runtime + sys.path.insert(dirname(__file__)) rejects."""
    repo, base = _init_repo_1198(tmp_path)
    (repo / "bin" / "runner.py").write_text(
        "import os, sys\n"
        "open('bin/helper.py', 'w').write('X = 2\\n')\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n",
        encoding="utf-8",
    )
    _git_1198(repo, "add", ".")
    _git_1198(repo, "commit", "-m", "runtime-write")
    cand = _git_1198(repo, "rev-parse", "HEAD")

    assert check_commit(repo, base, cand).verdict == "REJECT"


def test_1198_readonly_open_and_non_module_write_accept() -> None:
    """Read-only .py open() and non-module (.txt/.json) file writes must ACCEPT."""
    code = (
        b"from pathlib import Path\n"
        b"with open('calc.py', 'r') as f:\n"
        b"    _ = f.read()\n"
        b"with open('calc.py') as f2:\n"
        b"    _ = f2.read()\n"
        b"_ = Path('calc.py').open('r').read()\n"
        b"with open('/tmp/report.txt', 'w') as out:\n"
        b"    out.write('ok')\n"
        b"Path('/tmp/data.json').write_text('{}')\n"
    )
    res = check_source("src/x.py", None, code, BASE_DIRS)
    assert res.verdict == "ACCEPT", f"readonly/non-module: got {res.verdict} ({res.reason})"
