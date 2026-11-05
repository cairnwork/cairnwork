"""The observer itself: ``python -I -B runner.py <root> <test file> <test name> <result path>
<extra sys.path entries as JSON>``. Runs inside the sandbox; no pytest, conftest or ini.

It imports the test module the way pytest's ``prepend`` mode does, then calls the target test
directly (unittest methods go through the unittest runner). The result is one JSON object at
<result path>; the exit code mirrors it (0 PASS, 1 FAIL, 3 NA, 4 OBS_ERROR) and is only recorded.
"""

import importlib
import inspect
import json
import os
import re
import sys
import unittest

sys.dont_write_bytecode = True
CODES = {"PASS": 0, "FAIL": 1, "NA": 3, "OBS_ERROR": 4}
# xunit-style setup pytest runs around a target; a name bound at run time in any way counts
XUNIT_FUNCTION = ("setup_module", "teardown_module", "setup_function", "teardown_function",
                  "setup", "teardown", "setUpModule", "tearDownModule")  # fmt: skip
XUNIT_UNITTEST_MODULE = ("setup_module", "teardown_module", "setup_function", "teardown_function")
XUNIT_CLASS = ("setup_method", "teardown_method", "setup_class", "teardown_class")
MARKS_NA = {"parametrize", "usefixtures", "skip", "skipif", "xfail", "filterwarnings"}


def finish(result_path, status, detail=""):
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
    record = {
        "status": status,
        "detail": detail[:300],
        "sys_path": list(sys.path),
        "env": {k: os.environ[k] for k in keep if k in os.environ},
    }
    fd = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle)
    sys.stdout.flush()
    os._exit(CODES[status])


def prepend_root(path):
    """pytest 'prepend': the first directory upward without __init__.py, plus a dotted name."""
    directory, parts = os.path.dirname(path), [os.path.basename(path)[:-3]]
    while os.path.isfile(os.path.join(directory, "__init__.py")):
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        parts.insert(0, os.path.basename(directory))
        directory = parent
    return directory, ".".join(parts)


def conftest_names(root, test_dir):
    """Names a conftest.py between the test directory and root defines at top level."""
    names, directory = set(), test_dir
    while True:
        conftest = os.path.join(directory, "conftest.py")
        if os.path.isfile(conftest):
            with open(conftest, encoding="utf-8", errors="replace") as handle:
                for found in re.finditer(r"^(?:def|class)\s+(\w+)|^(\w+)\s*=", handle.read(), re.M):
                    names.add(found.group(1) or found.group(2))
        if directory == root or os.path.dirname(directory) == directory:
            return names
        directory = os.path.dirname(directory)


def import_failure(root, path, exc):
    """NA when the import needs what a conftest provides, otherwise a real observer error."""
    missing = getattr(exc, "name", None)
    if isinstance(exc, NameError):
        found = re.search(r"name '(\w+)'", str(exc))
        missing = found.group(1) if found else None
    if isinstance(exc, (ImportError, NameError)) and missing:
        if missing == "conftest" or missing in conftest_names(root, os.path.dirname(path)):
            return "NA", f"needs conftest: {missing}"
    return "OBS_ERROR", f"import: {exc!r}"


def judge_unittest(case, method):
    outcome = unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([case(method)]))
    clean = outcome.wasSuccessful() and outcome.testsRun == 1
    clean = clean and not outcome.skipped and not outcome.expectedFailures
    return ("PASS", "") if clean else ("FAIL", "unittest")


def mark_names(obj):
    found = getattr(obj, "pytestmark", None) or []
    return {getattr(m, "name", "") for m in (found if isinstance(found, list) else [found])}


def has_any(obj, names):
    return any(hasattr(obj, n) for n in names)  # hasattr sees every binding form, not only def


def judge(module, name):
    cls_name, _, member = name.rpartition(".")
    try:
        target = getattr(module, cls_name or name)
    except BaseException as exc:  # noqa: BLE001 - the observer reports, never raises
        return "OBS_ERROR", f"lookup: {exc!r}"
    marks = mark_names(module) | mark_names(target)  # module, class and function level
    if cls_name:
        if isinstance(target, type) and issubclass(target, unittest.TestCase):
            marks |= mark_names(getattr(target, member, None))  # the method's own marks
            if marks & MARKS_NA:
                return "NA", "marks"
            if has_any(module, XUNIT_UNITTEST_MODULE) or has_any(target, XUNIT_CLASS):
                return "NA", "XUNIT_SETUP"  # pytest runs setup from the module and the class only
            return judge_unittest(target, member)
        return "NA", "method of a non-unittest class"
    if not callable(target):
        return "OBS_ERROR", "target is not callable"
    if isinstance(target, type):  # a class-level node (file::Class) names no single test
        return "NA", "TARGET_SHAPE"
    if has_any(module, XUNIT_FUNCTION):
        return "NA", "XUNIT_SETUP"
    if inspect.signature(target).parameters or marks & MARKS_NA:
        return "NA", "parameters, fixtures or marks"
    if inspect.iscoroutinefunction(target) or inspect.isgeneratorfunction(target):
        return "NA", "async or generator test"
    try:
        target()
    except BaseException as exc:  # noqa: BLE001 - a failing assertion is the observation
        return "FAIL", repr(exc)
    return "PASS", ""


def main():
    root, rel, name, result_path, extra = sys.argv[1:6]
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, path]) != root or not path.endswith(".py"):
        finish(result_path, "OBS_ERROR", "test file outside root")
    base, modname = prepend_root(path)
    sys.path[:0] = [base, *json.loads(extra)]
    try:
        module = importlib.import_module(modname)
    except BaseException as exc:  # noqa: BLE001
        finish(result_path, *import_failure(root, path, exc))
    else:
        finish(result_path, *judge(module, name))


main()
