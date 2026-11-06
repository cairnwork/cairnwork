"""#796 S3 step 1: the guard records a test module whose own import statement failed with "No
module named 'pkg.newmod'" while ``pkg`` is a regular in-repo package
(``collect_missing_submodules``), the parent re-validates it, and no verdict changes:
``collect_errors`` still names the collector, so the run still fails closed."""

import json

import pytest

from cairnwork import report_guard
from cairnwork.collect_import import import_errors, missing_submodules
from cairnwork.verifiers import VerifierRun
from tests.test_collect_import_record import NODE, NONCE, _run, _unchanged

PKG = {"pkg/__init__.py": "def old(): pass\n"}
WANT = ((NODE, "pkg.newmod", "pkg/__init__.py"),)
SHAPES = {
    "import": "import pkg.newmod",
    "from": "from pkg.newmod import x",
    "as": "import pkg.newmod as m",
}


def _raw(where, field="collect_missing_submodules"):
    """What the guard itself wrote, before the parent re-validates it: each guard check is
    pinned here, not only by the parent dropping what the guard let through."""
    return json.loads((where / "verdict.json").read_text())[field]


@pytest.mark.parametrize("shape", SHAPES)
def test_a_new_submodule_of_an_in_repo_package_is_recorded(tmp_path, shape):  # AC2
    done, verdict = _run(tmp_path, {**PKG, NODE: SHAPES[shape] + "\ndef test_a(): pass\n"})
    _unchanged(done, verdict)  # collect_errors is as before: an older reader fails closed
    assert verdict.submodule_errors == WANT
    assert verdict.import_errors == ()  # not mixed into the S1/S2 list


def test_a_deeper_package_and_a_src_layout_are_recorded(tmp_path):
    files = {"src/a/__init__.py": "", "src/a/b/__init__.py": "",
             "pytest.ini": "[pytest]\npythonpath = src\n",
             NODE: "import a.b.newmod\ndef test_a(): pass\n"}  # fmt: skip
    done, verdict = _run(tmp_path, files)
    _unchanged(done, verdict)
    assert verdict.submodule_errors == ((NODE, "a.b.newmod", "src/a/b/__init__.py"),)


def test_an_s2_reader_sees_nothing_in_an_s3_record(tmp_path):  # AC2 negative
    done, verdict = _run(tmp_path, {**PKG, NODE: "import pkg.newmod\ndef test_a(): pass\n"})
    data = json.loads((tmp_path / "verdict.json").read_text())
    assert import_errors(data) == () and data["collect_errors"] == [NODE]
    assert data["collect_import_errors"] == []


NOT_RECORDED = {
    "a new top-level module": {NODE: "from newtop_796 import x"},
    "a missing third-party package": {NODE: "import not_installed_796.sub"},
    "a namespace parent": {"ns/data.txt": "x", NODE: "import ns.newmod"},
    "a namespace one level deeper": {"pkg/__init__.py": "", "pkg/ns/d.txt": "x",
                                     NODE: "import pkg.ns.newmod"},
    "a parent outside the checkout": {NODE: "import json.newmod_796"},
    "a repo __init__ missing its own dependency": {
        "pkg/__init__.py": "import pkg.helper_796\n", NODE: "import pkg"},
    "importlib at module level": {**PKG, NODE: "import importlib\n"
                                  "importlib.import_module('pkg.newmod')"},
    "a submodule importing a missing sibling": {
        **PKG, "pkg/a.py": "import pkg.newmod\n", NODE: "import pkg.a"},
    "not a package": {"pkg.py": "", NODE: "import pkg.newmod"},
}  # fmt: skip


@pytest.mark.parametrize("why", NOT_RECORDED)
def test_shapes_outside_s3_are_not_recorded(tmp_path, why):  # AC3, AC4, AC6
    files = {k: v + ("\ndef test_a(): pass\n" if k == NODE else "")
             for k, v in NOT_RECORDED[why].items()}  # fmt: skip
    done, verdict = _run(tmp_path, files)
    _unchanged(done, verdict)
    assert verdict.submodule_errors == () and verdict.import_errors == ()
    assert _raw(tmp_path) == []


def test_a_parent_shadowed_by_a_package_outside_the_checkout_is_not_recorded(tmp_path):  # AC4
    outside = tmp_path / "site" / "pkg"
    outside.mkdir(parents=True)
    (outside / "__init__.py").write_text("")
    shadow = f"import sys\nsys.path.insert(0, {str(outside.parent)!r})\nimport pkg.newmod\n"
    done, verdict = _run(tmp_path / "repo", {NODE: shadow + "def test_a(): pass\n"})
    _unchanged(done, verdict)
    assert verdict.submodule_errors == () and _raw(tmp_path / "repo") == []


def _raise(args, name="'pkg.newmod'", path="None", kind="ModuleNotFoundError"):
    return f"import pkg\nraise {kind}({args}, name={name}, path={path})\n"


ODD = {
    "the is-not-a-package wording": _raise(
        '"No module named \'pkg.newmod\'; \'pkg\' is not a package"'),
    "extra text": _raise('"No module named \'pkg.newmod\' (x)"'),
    "a name that does not match": _raise('"No module named \'pkg.other\'"'),
    "two args": _raise('"No module named \'pkg.newmod\'", 1'),
    "a str path": _raise('"No module named \'pkg.newmod\'"', path="'pkg/newmod.py'"),
    "a plain ImportError": _raise('"No module named \'pkg.newmod\'"', kind="ImportError"),
    "a subclass": "import pkg\nclass E(ModuleNotFoundError):\n    pass\n"
                  + _raise('"No module named \'pkg.newmod\'"', kind="E"),
    "a name with no dot": _raise('"No module named \'pkg\'"', name="'pkg'"),
    "a str-subclass name": "import pkg\nclass S(str):\n    pass\n"
                           + _raise('"No module named " + repr(S("pkg.newmod"))',
                                    name="S('pkg.newmod')"),
    "a str-subclass message raising": (
        "import pkg\nclass S(str):\n    def __eq__(self, other):\n        raise SystemExit(5)\n"
        "    __hash__ = str.__hash__\n" + _raise("S(\"No module named 'pkg.newmod'\")")),
    "the parent swapped for a non-module": (
        "import sys\nsys.modules['pkg'] = object()\n" + _raise('"No module named \'pkg.newmod\'"')),
    "a non-module parent with an in-checkout __file__": (  # #970 review nit 1
        "import os, sys\nclass P:\n    pass\np = P()\n"
        "p.__file__ = os.path.abspath('pkg/__init__.py')\n"
        "p.__path__ = [os.path.abspath('pkg')]\np.__spec__ = None\n"
        "sys.modules['pkg'] = p\nimport pkg.newmod\n"
    ),
    "the parent with no __file__": (
        "import sys, types\nsys.modules['pkg'] = types.ModuleType('pkg')\n"
        + _raise('"No module named \'pkg.newmod\'"').replace("import pkg\n", "")),
    "the parent __file__ not an __init__": (
        "import pkg\npkg.__file__ = pkg.__file__.replace('__init__', 'other')\n"
        + _raise('"No module named \'pkg.newmod\'"')),
}  # fmt: skip


@pytest.mark.parametrize("why", ODD)
def test_odd_exceptions_record_nothing_and_change_nothing(tmp_path, why):  # AC5, AC9
    done, verdict = _run(tmp_path, {**PKG, NODE: ODD[why] + "def test_a(): pass\n"})
    _unchanged(done, verdict)
    assert verdict.submodule_errors == () and _raw(tmp_path) == []


def test_the_same_forged_raise_with_exact_values_is_recorded(tmp_path):  # the checks above held
    body = _raise("\"No module named 'pkg.newmod'\"") + "def test_a(): pass\n"
    done, verdict = _run(tmp_path, {**PKG, NODE: body})
    _unchanged(done, verdict)
    assert verdict.submodule_errors == WANT


def test_the_guard_never_runs_a_module_getattr_of_the_parent(tmp_path):  # AC7
    lazy = ("import os\ndef __getattr__(name):\n"
            "    open(os.path.join(os.path.dirname(__file__), 'TOUCHED'), 'w').close()\n"
            "    raise AttributeError(name)\n")  # fmt: skip
    done, verdict = _run(
        tmp_path, {"pkg/__init__.py": lazy, NODE: "import pkg.newmod\ndef test_a(): pass\n"}
    )
    _unchanged(done, verdict)
    assert verdict.submodule_errors == WANT
    assert not (tmp_path / "pkg" / "TOUCHED").exists()


def test_a_test_module_cannot_rebind_what_the_recorder_uses(tmp_path):
    rebind = ("import os, sys\ndef _b(*a, **k):\n    raise RuntimeError('rebound')\n"
              "os.path.realpath = os.path.relpath = os.path.basename = _b\n"
              "import pkg.newmod\n")  # fmt: skip
    done, verdict = _run(tmp_path, {**PKG, NODE: rebind + "def test_a(): pass\n"})
    assert not verdict.ran and verdict.collect_errors == (NODE,)
    assert verdict.submodule_errors == WANT


def _record_with(entries):
    return {"nonce": NONCE, "collected": [], "outcomes": {}, "exitstatus": 2, "testsfailed": 0,
            "collect_errors": [NODE], "collect_missing_submodules": entries}  # fmt: skip


GOOD = {"node": NODE, "module": "pkg.newmod", "parent_origin": "pkg/__init__.py"}


def test_the_parent_drops_any_malformed_entry_and_keeps_the_good_ones(tmp_path):
    bad = [
        {**GOOD, "module": ""}, {**GOOD, "module": "a..b"}, {**GOOD, "module": ".x"},
        {**GOOD, "module": "x."}, {**GOOD, "module": "pkg"}, {**GOOD, "module": "p." + "a" * 99},
        {**GOOD, "module": "pkg.class"}, {**GOOD, "module": "pkg.new mod"}, {**GOOD, "module": 7},
        {**GOOD, "parent_origin": "other/__init__.py"}, {**GOOD, "parent_origin": "pkg/x.py"},
        {**GOOD, "parent_origin": "xpkg/__init__.py"},
        {**GOOD, "parent_origin": "../pkg/__init__.py"},
        {**GOOD, "parent_origin": "/r/pkg/__init__.py"}, {**GOOD, "parent_origin": None},
        {**GOOD, "parent_origin": "p\n::error::/pkg/__init__.py"},
        {**GOOD, "node": "/abs/test_x.py"}, {**GOOD, "node": "../t.py"}, {**GOOD, "node": ["t"]},
        {"node": NODE}, "tests/test_x.py", 3, None,
    ]  # fmt: skip
    src = {**GOOD, "parent_origin": "src/pkg/__init__.py"}
    got = missing_submodules(_record_with([*bad, GOOD, GOOD, src]))
    assert got == (WANT[0], (NODE, "pkg.newmod", "src/pkg/__init__.py"))
    path = tmp_path / "v.json"
    path.write_text(json.dumps(_record_with([*bad, GOOD, src])))
    verdict = report_guard.read_verdict(path, NONCE)
    assert verdict.submodule_errors == got and not verdict.ran
    assert verdict.collect_errors == (NODE,) and verdict.import_errors == ()


def test_a_field_that_is_not_a_list_and_an_older_record_give_nothing():
    for value in ({"node": NODE}, "x", 5, None, True):
        assert missing_submodules({"collect_missing_submodules": value}) == ()
    assert missing_submodules({}) == ()
    many = [{**GOOD, "module": f"pkg.m{n}"} for n in range(5000)]
    assert 0 < len(missing_submodules(_record_with(many))) <= 100


def test_a_verifier_run_carries_the_field_with_an_empty_default():
    assert VerifierRun.__dataclass_fields__["submodule_errors"].default == ()


def test_a_parent_with_no_file_in_its_dict_is_never_asked_through_getattr(tmp_path):  # AC7
    lazy = (
        "import os, sys, types\nm = types.ModuleType('pkg')\n_d = os.getcwd()\n"
        "def _ga(name):\n"
        "    return os.path.join(_d, 'pkg', '__init__.py')\n"
        "m.__getattr__ = _ga\nsys.modules['pkg'] = m\n"
        "raise ModuleNotFoundError(\"No module named 'pkg.newmod'\", name='pkg.newmod')\n"
    )
    done, verdict = _run(tmp_path, {**PKG, NODE: lazy + "def test_a(): pass\n"})
    _unchanged(done, verdict)
    # pytest itself asks the module for __file__ here; through getattr the guard would get a
    # valid __init__.py path and record it, so no record means it read only the module dict
    assert verdict.submodule_errors == ()


def test_a_base_exception_while_reading_the_traceback_records_nothing(tmp_path):  # AC9
    body = (
        "import os, pkg\nclass S(str):\n    def startswith(self, prefix, *a):\n"
        "        if prefix == '<frozen importlib':\n            raise SystemExit(5)\n"
        "        return str.startswith(self, prefix, *a)\n"
        '_c = compile("raise ModuleNotFoundError(\\"No module named \'pkg.newmod\'\\",'
        " name='pkg.newmod')\", S(__file__), 'exec')\nexec(_c, {})\ndef test_a(): pass\n"
    )
    done, verdict = _run(tmp_path, {**PKG, NODE: body})
    _unchanged(done, verdict)
    assert verdict.submodule_errors == ()
    calm = body.replace("raise SystemExit(5)", "pass")  # the catch is what held
    done, verdict = _run(tmp_path / "again", {**PKG, NODE: calm})
    assert verdict.submodule_errors == WANT
