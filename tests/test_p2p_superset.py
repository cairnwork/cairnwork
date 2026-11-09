"""#877: the python_files rule only adds to main's selection, never replaces it, so the
module-dir selection is always a superset of what main selects (R1 and R2 of the review).
Since #1041, no-rule selection may also be wider than main's exact names via the default
python_files name-part match, but it is always a superset of main's selection."""

from __future__ import annotations

import random
from collections.abc import Callable
from fnmatch import fnmatch
from posixpath import basename, dirname, splitext

import pytest

from cairnwork import p2p_selection as sel
from cairnwork.p2p_selection import select_p2p_targets as select

ROOTS = ("tests",)


def _rule(*patterns: str) -> Callable[[str], bool]:
    return lambda path: any(fnmatch(basename(path), p) for p in patterns)


def _main(changed, tests, roots, cap=sel.MODULE_DIR_CAP):
    """main's module-dir selection: exact names only, root-level fallback, the same cap."""
    size: dict[str, int] = {}
    for t in tests:
        size[dirname(t)] = size.get(dirname(t), 0) + 1
    root_level = {dirname(t) for t in tests if dirname(t) in roots}
    out: set[str] = set()
    for c in changed:
        if any(c == r or c.startswith(f"{r}/") for r in roots):
            continue
        tail = sel._mirror_subpath(dirname(c))
        mirror = {f"{r}/{tail}" for r in roots} if tail else set()
        stem = splitext(basename(c))[0]
        named = {f"test_{stem}.py", f"{stem}_test.py"}
        hit = {dirname(t) for t in tests if dirname(t) in mirror or basename(t) in named}
        for d in hit or root_level:
            out |= {
                t for t in tests if dirname(t) == d and (size[d] <= cap or basename(t) in named)
            }
    return out


def test_a_custom_rule_never_drops_what_main_selects():
    tests = ("tests/test_app.py", "tests/test_misc.py", "tests/sub/check_calc.py")
    got = select(("src/calc.py",), tests, ROOTS, "module-dir", _rule("check_*.py"))
    assert {"tests/test_app.py", "tests/test_misc.py"} <= set(got)
    assert "tests/sub/check_calc.py" in got


def test_a_cut_directory_keeps_the_exact_and_the_rule_named_files():
    flat = tuple(f"tests/test_n{i}.py" for i in range(sel.MODULE_DIR_CAP + 2))
    tests = (*flat, "tests/test_calc.py", "tests/check_calc.py")
    got = select(("src/calc.py",), tests, ROOTS, "module-dir", _rule("check_*.py"))
    assert got == ("tests/check_calc.py", "tests/test_calc.py")


_DIRS = ("tests", "tests/sub", "tests/sub/deep", "tests/other")
_STEMS = ("calc", "app", "util", "my_app", "io")
_PREFIX = ("test_", "check_", "", "spec-")
_SUFFIX = ("", "_test", "_check", "_extra")
_PATTERNS = ("test_*.py", "check_*.py", "*_check.py", "spec-*.py", "*.py", "*_test.py")


def _case(rng: random.Random):
    tests = {
        f"{rng.choice(_DIRS)}/{rng.choice(_PREFIX)}{rng.choice(_STEMS)}{rng.choice(_SUFFIX)}.py"
        for _ in range(rng.randint(1, 40))
    }
    if rng.random() < 0.3:
        d = rng.choice(_DIRS)
        tests |= {f"{d}/test_fill{i}.py" for i in range(sel.MODULE_DIR_CAP + 1)}
    changed = tuple(
        f"{rng.choice(('src/pkg', 'src/pkg/sub', 'lib', 'pkg'))}/{rng.choice(_STEMS)}.py"
        for _ in range(rng.randint(1, 3))
    )
    patterns = rng.sample(_PATTERNS, rng.randint(1, 3))
    return changed, tuple(sorted(tests)), _rule(*patterns)


@pytest.mark.parametrize("seed", range(250))
def test_the_selection_is_always_a_superset_of_mains(seed):
    changed, tests, rule = _case(random.Random(seed))
    got = set(select(changed, tests, ROOTS, "module-dir", rule))
    assert _main(changed, tests, ROOTS) <= got
    assert _main(changed, tests, ROOTS) <= set(select(changed, tests, ROOTS, "module-dir"))
