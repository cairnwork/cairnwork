"""A verifier Python older than 3.10 gets a module inventory that can only over-list (#790).

The stand-in "old Python" is this interpreter behind a script that answers the version question
with 3.9 and hides the two 3.10+ APIs before running the probe, so main's probe fails on it the
way a real 3.9 does. Each case can also put directories on its sys.path or install import hooks.
"""

import ast
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import commit_change, git

from cairnwork.module_inventory import _VERSION
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task

PROBE = Path(__file__).parent.parent / "src" / "cairnwork" / "old_python_probe.py"
FILES = {
    "setup.py": "import sys\nsys.path.insert(0, '.')\n",  # base may edit sys.path: no seal
    "pkgA/__init__.py": "",
    "pkgA/geometry/__init__.py": "",
    "pkgA/geometry/point.py": "X = 1\n",
    "pkgA/combinatorics/__init__.py": "",
    "pkgA/combinatorics/permutations.py": "X = 1\n",
    "pkgA/geometry/tests/__init__.py": "",
    "pkgA/geometry/tests/test_point.py": "def test_x():\n    pass\n",
    "pkgB/__init__.py": "",
    "pkgB/base.py": "X = 1\n",
    "pkgB/feature_extraction/__init__.py": "",
    "pkgB/feature_extraction/text.py": "X = 1\n",
    "pkgB/tests/__init__.py": "",
    "pkgB/tests/test_base.py": "def test_x():\n    pass\n",
}
CASES = {
    "pkgA/geometry/point.py": "pkgA/geometry/tests",
    "pkgA/combinatorics/permutations.py": "pkgA/geometry/tests",
    "pkgB/feature_extraction/text.py": "pkgB/tests",
    "pkgB/base.py": "pkgB/tests",
}
STUB = """#!{exe} -I
import sys
code = sys.argv[-1]
if code == {version_code!r}:
    print({major}, {minor})
    raise SystemExit(0)
if {old}:
    import importlib.metadata
    del sys.stdlib_module_names
    del importlib.metadata.packages_distributions
    sys.version_info = ({major}, {minor}, 0, "final", 0)
sys.path.extend({extra!r})
{setup}
exec(compile(code, "<probe>", "exec"), {{"__name__": "__main__"}})
"""


def stub(tmp_path, extra=(), setup="", version=(3, 9)) -> str:
    path = tmp_path / f"python{len(list(tmp_path.glob('python*')))}"
    old = version < (3, 10)
    text = STUB.format(
        exe=sys.executable, version_code=_VERSION, major=version[0], minor=version[1], old=old,
        extra=[str(p) for p in extra], setup=setup,
    )  # fmt: skip
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


def task(repo: Path, path: str) -> TaskSpec:
    root = CASES[path]
    verify = (("pytest", ("python3", "-m", "pytest", "-q", f"{root}/test_x.py")),)
    head = git(repo, "rev-parse", "HEAD")
    return TaskSpec("k", "c", "t", "fix", repo, head, (path,), verify, test_roots=(root,))


def refused(repo, path, python) -> bool:
    try:
        check_task(task(repo, path), verifier_python=python)
    except TaskRejected as exc:
        assert "PROTECTED_VERIFIER_INPUT" in str(exc)
        return True
    return False


@pytest.mark.parametrize("path", sorted(CASES))
def test_an_in_package_fix_is_allowed_on_an_old_verifier_python(repo, tmp_path, path):
    """F1-F4: main refuses all four on a 3.9 verifier (its probe needs 3.10); now allowed."""
    commit_change(repo, FILES)
    assert not refused(repo, path, stub(tmp_path))
    assert not refused(repo, path, stub(tmp_path, version=(3, 12)))  # 3.10+: unchanged


@pytest.mark.parametrize(
    "layout",
    (
        {"point/geom/x.py": ""},  # a namespace directory, invisible to pkgutil
        {"point.py": ""},
        {"d-1.0.dist-info/RECORD": "point.py,,\n"},  # metadata only, no file
        {"d-1.0.egg-info/top_level.txt": "point\n"},
        {"dev.egg-link": "{dev}\n", "dev/point.py": ""},
        {"x.pth": "{dev}\n", "dev/point.py": ""},
    ),
)
def test_a_name_installed_in_the_old_verifier_python_stays_protected(repo, tmp_path, layout):
    """F1-neg (N1, N4, N5a): every source of an installed name ``point`` keeps point.py refused
    in the root module position."""
    commit_change(repo, {**FILES, "point.py": "X = 1\n"})
    site = tmp_path / "site"
    for name, text in layout.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.format(dev=site / "dev"))
    CASES["point.py"] = "pkgA/geometry/tests"
    try:
        assert refused(repo, "point.py", stub(tmp_path, extra=[site]))
        assert not refused(repo, "point.py", stub(tmp_path))  # the control: no such name
    finally:
        del CASES["point.py"]


HOOKS = {
    "path hook": "sys.path_hooks.insert(0, lambda path: None)",
    "path importer": "sys.path_importer_cache['/nowhere'] = object()",
    "finder named like PathFinder": (
        "PathFinder = type('PathFinder', (), {'find_spec': classmethod(lambda *a: None)})\n"
        "sys.meta_path.insert(0, PathFinder)"
    ),
    "finder named like the distutils hack": (
        "DistutilsMetaFinder = type('DistutilsMetaFinder', (), {})\n"
        "sys.meta_path.insert(0, DistutilsMetaFinder())"
    ),
    "MAPPING outside an editable finder": (
        "sys.meta_path.append(type('F', (), {'MAPPING': {}, 'find_spec': lambda *a: None})())"
    ),
}


@pytest.mark.parametrize("setup", sorted(HOOKS))
def test_an_import_hook_the_probe_cannot_enumerate_fails_closed(repo, tmp_path, setup):
    """N2, N5b, N5d, N5e: names served by an unknown hook could be missing, so nothing is safe."""
    commit_change(repo, FILES)
    assert refused(repo, "pkgB/base.py", stub(tmp_path, setup=HOOKS[setup]))


@pytest.mark.parametrize(
    "setup",
    (
        "Finder = type('Finder', (), {'MAPPING': {'base.x': '/x'}})\n"
        "Finder.__module__ = '__editable___d_finder'\nsys.meta_path.append(Finder)",
        "import types\nsys.modules['base'] = types.ModuleType('base')",
    ),
)
def test_editable_and_sys_modules_names_are_listed(repo, tmp_path, setup):
    """N5c and N5f: a name only an editable finder or sys.modules knows still protects."""
    commit_change(repo, FILES)
    assert refused(repo, "pkgB/base.py", stub(tmp_path, setup=setup))
    assert not refused(repo, "pkgB/base.py", stub(tmp_path))


def test_a_stdlib_name_of_either_python_is_protected(repo, tmp_path):
    """N3: tomllib exists only in Cairnwork's own stdlib (3.11+); the union keeps it."""
    commit_change(repo, {**FILES, "pkgB/tomllib.py": "X = 1\n"})
    CASES["pkgB/tomllib.py"] = "pkgB/tests"
    try:
        assert refused(repo, "pkgB/tomllib.py", stub(tmp_path))
    finally:
        del CASES["pkgB/tomllib.py"]


def test_the_probe_parses_as_python_3_6():
    ast.parse(PROBE.read_text(encoding="utf-8"), feature_version=(3, 6))


def test_main_s_probe_still_runs_on_3_10_and_newer(tmp_path, monkeypatch):
    from cairnwork import module_inventory

    seen = []
    real = module_inventory._run
    monkeypatch.setattr(
        module_inventory, "_run", lambda py, code: seen.append(code) or real(py, code)
    )
    module_inventory.importable_names(stub(tmp_path, version=(3, 12)))
    assert seen == [module_inventory._VERSION, module_inventory._MODULE_NAMES]


def test_an_old_inventory_covers_main_s_inventory_of_the_same_site(tmp_path):
    """AC8: on this interpreter, with a namespace dir, an egg-link, dist-info and egg-info, the old
    probe (3.10+ APIs hidden) lists every name main's probe lists."""
    from cairnwork import module_inventory

    site = tmp_path / "site"
    for name, text in {"ns/a/x.py": "", "d-1.0.dist-info/RECORD": "dm.py,,\n",
                       "e-1.0.egg-info/top_level.txt": "em\n", "dev.egg-link": f"{site}/dev\n",
                       "dev/devm.py": "", "m.pth": f"{site}/dev\n"}.items():  # fmt: skip
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_text(text)
    old = module_inventory.importable_names(stub(tmp_path, extra=[site]))
    new = module_inventory.importable_names(stub(tmp_path, extra=[site], version=(3, 12)))
    assert new <= old
    assert {"ns", "dm", "em", "devm"} <= old


@pytest.mark.parametrize(
    "stdout",
    (
        'noise\n{"v": 1, "version": [3, 9], "names": ["a"]}\n',
        'x{"v": 1, "version": [3, 9], "names": ["a"]}\n',
        '{"v": 1, "version": [3, 9], "names": ["a"]}',  # no newline
        '{"v": 2, "version": [3, 9], "names": ["a"]}\n',
        '{"v": 1, "version": [3, "9"], "names": ["a"]}\n',
        '{"v": 1, "version": [3, 8], "names": ["a"]}\n',
        '{"v": 1, "version": [3, 9], "names": [1]}\n',
        '{"v": 1, "version": [3, 9], "names": ["a"], "x": 0}\n',
        "",
    ),
)
def test_any_other_inventory_output_is_refused(stdout):
    """N2b: the old probe's output must be exactly one JSON line of the agreed shape."""
    from cairnwork.module_inventory import _old_names

    assert _old_names(json.dumps({"v": 1, "version": [3, 9], "names": ["a"]}) + "\n", [3, 9])
    with pytest.raises(ValueError):
        _old_names(stdout, [3, 9])


@pytest.mark.parametrize("answer", ("3 5", "2 7", "3.9", "3 9 0", "", "3 9\\n3 9"))
def test_an_unsupported_or_unreadable_version_fails_closed(repo, tmp_path, answer):
    commit_change(repo, FILES)
    python = tmp_path / "python"
    python.write_text(f"#!/bin/sh\necho '{answer}'\n")
    python.chmod(0o755)
    assert refused(repo, "pkgB/base.py", str(python))


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any directory")
def test_a_path_the_probe_cannot_search_fails_closed(repo, tmp_path):
    """Open 6: the verifier's account might import from a directory the probe's user cannot
    search, so a permission error is a failure, never "absent"."""
    commit_change(repo, FILES)
    locked = tmp_path / "locked"
    (locked / "sub").mkdir(parents=True)
    site = tmp_path / "site"
    site.mkdir()
    (site / "x.pth").write_text(f"{locked / 'sub'}\n")
    locked.chmod(0)
    try:
        assert refused(repo, "pkgB/base.py", stub(tmp_path, extra=[site]))
        assert refused(repo, "pkgB/base.py", stub(tmp_path, extra=[locked / "sub"]))
    finally:
        locked.chmod(0o755)
    assert not refused(repo, "pkgB/base.py", stub(tmp_path, extra=[site]))  # the control


def test_zips_eggs_and_installed_files_are_listed(tmp_path):
    """A zip named by a .pth, an .egg on sys.path and installed-files.txt's ``../pkg`` lines."""
    import zipfile

    from cairnwork import module_inventory

    site = tmp_path / "site"
    site.mkdir()
    for name, member in (("lib.zip", "zm.py"), ("e-1.0.egg", "egm/__init__.py")):
        with zipfile.ZipFile(site / name, "w") as archive:
            archive.writestr(member, "")
    (site / "z.pth").write_text(f"{site / 'lib.zip'}\n")
    (site / "o-1.0.egg-info").mkdir()
    (site / "o-1.0.egg-info" / "installed-files.txt").write_text("../ofm/__init__.py\n")
    names = module_inventory.importable_names(stub(tmp_path, extra=[site, site / "e-1.0.egg"]))
    assert {"zm", "egm", "ofm"} <= names


@pytest.mark.parametrize(
    "setup",
    ("sys.version_info = (3, 8, 0, 'final', 0)", "print('extra line')"),
)
def test_a_changed_version_or_extra_output_fails_closed(repo, tmp_path, setup):
    commit_change(repo, FILES)
    assert refused(repo, "pkgB/base.py", stub(tmp_path, setup=setup))
