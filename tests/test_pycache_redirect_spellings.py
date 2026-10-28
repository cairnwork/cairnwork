"""Issue #276 end to end on cairnwork-verify: the redirect spelled so tamper_scan cannot see it.

test_pycache_redirect.py drives run_verifiers; this drives the whole verify path (tamper_scan,
the sandboxed run, the after-run bytecode scan). The protected test compares ``add(2, 3)`` with
``EXPECTED`` from tests/helper.py. The candidate leaves ``add`` wrong and, when calc is imported,
points ``sys.pycache_prefix`` at the run's HOME and plants bytecode there for tests/helper.py whose
``EXPECTED`` matches the wrong answer.

A spelled-out redirect (plain, aliased, setattr, vars) is refused by tamper_scan before anything
runs; a name built from strings passes it, and the after-run scan fails the run instead. Bytecode
removed again before the run ends (the prefix directory, or only the pyc files in it; the name
built by concatenation or with chr) is the known gap of that scan (bytecode_roots.py): Seatbelt
closes it by never reading bytecode under a write root (#368), and those cases stay strict xfails
under bwrap, while the sys.modules swap in test_blackbox_verify.py, which needs no bytecode at all,
stays a false green. Both are in-process
forgeries; the boundary is the blackbox declaration (#303) and a verdict produced outside the
candidate's process, not these scans.
"""

import sys

import pytest
from verify_helpers import BAD, base_of, candidate, verify

pytestmark = pytest.mark.sandbox

PLANT = (
    "import importlib.util, marshal, os, sys\n"
    "from pathlib import Path\n"
    "_d = os.path.join(os.environ['HOME'], 'alt')\n"
    "{redirect}\n"
    "_src = Path(__file__).parent.parent / 'tests' / 'helper.py'\n"
    "_st = os.stat(_src)\n"
    "_data = (importlib.util.MAGIC_NUMBER + bytes(4)\n"
    "         + (int(_st.st_mtime) & 0xFFFFFFFF).to_bytes(4, 'little')\n"
    "         + (_st.st_size & 0xFFFFFFFF).to_bytes(4, 'little')\n"
    "         + marshal.dumps(compile('EXPECTED = 6\\n', str(_src), 'exec')))\n"
    "for _p in {{str(_src), os.path.realpath(_src)}}:\n"
    "    _pyc = importlib.util.cache_from_source(_p)\n"
    "    os.makedirs(os.path.dirname(_pyc), exist_ok=True)\n"
    "    Path(_pyc).write_bytes(_data)\n"
    "def add(a, b):\n    return a + b + 1\n"
)
SPELLED = {
    "plain": "sys.pycache_prefix = _d",
    "alias": "import sys as s\ns.pycache_prefix = _d",
    "setattr": "setattr(sys, 'pycache_prefix', _d)",
    "vars": "vars(sys)['pycache_prefix'] = _d",
}
BUILT = "setattr(sys, 'pycache' + '_prefix', _d)"
CHR = f"setattr(sys, ''.join(map(chr, {[ord(c) for c in 'pycache_prefix']})), _d)"
# Evidence removed when the candidate's Python exits, before the after-run scan.
REMOVED = {
    "built_rmtree": BUILT + "\nimport atexit, shutil\natexit.register(shutil.rmtree, _d, True)",
    "chr_rmtree": CHR + "\nimport atexit, shutil\natexit.register(shutil.rmtree, _d, True)",
    "built_pyc_only": BUILT
    + (
        "\nimport atexit\natexit.register(lambda: [os.remove(os.path.join(r, f))"
        " for r, _, fs in os.walk(_d) for f in fs if f.endswith('.pyc')])"
    ),
}


def run(state, project, redirect):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": PLANT.format(redirect=redirect)})
    return verify(state, project, base, sha, "--import-path", "src")[1]


@pytest.mark.parametrize("how", sorted(SPELLED))
def test_a_spelled_out_redirect_is_refused_before_it_runs(state, helper_project, how):
    out = run(state, helper_project, SPELLED[how])
    assert out["status"] == "REFUSED", out
    assert out["reason"].startswith("VERIFIER_TAMPER_API: src/calc.py:"), out["reason"]
    assert "[pycache_prefix]" in out["reason"], out["reason"]


def test_a_redirect_built_from_strings_fails_on_the_planted_bytecode(state, helper_project):
    out = run(state, helper_project, BUILT)
    assert out["status"] == "FAILED" and not out["verified"], out
    assert "FORGED_BYTECODE" in out["reason"] and "tests/helper.py" in out["reason"], out


@pytest.mark.xfail(
    sys.platform != "darwin",  # Seatbelt denies reading the planted pyc (#368); bwrap cannot
    strict=True,
    raises=AssertionError,
    reason="Refs #276: bytecode removed before the after-run scan still gives VERIFIED",
)
@pytest.mark.parametrize("how", sorted(REMOVED))
def test_bytecode_removed_before_the_run_ends_is_not_verified(state, helper_project, how):
    """The gap bytecode_roots.py names: add is wrong, so only the forged helper passes it
    (test_the_same_wrong_add_without_a_forgery_fails). Under bwrap the run comes back VERIFIED and
    the final assert xfails; a fix makes it XPASS, which strict mode fails: then remove the marker.
    Any other outcome (a broken run) is a plain failure, never hidden by the xfail."""
    out = run(state, helper_project, REMOVED[how])
    if sys.platform == "darwin":  # #368: the planted pyc is never read, so the wrong add fails
        assert out["status"] == "FAILED" and "pytest exit code 1" in out["reason"], out
        return
    if out["status"] != "VERIFIED" and "FORGED_BYTECODE" not in out.get("reason", ""):
        pytest.fail(f"neither the known false green nor FORGED_BYTECODE: {out}")
    assert out["status"] != "VERIFIED", out


def test_the_same_wrong_add_without_a_forgery_fails(state, helper_project):
    base = base_of(helper_project)
    wrong = candidate(helper_project, base, {"src/calc.py": "import os, sys\n" + BAD})
    out = verify(state, helper_project, base, wrong, "--import-path", "src")[1]
    assert out["status"] == "FAILED" and "pytest exit code 1" in out["reason"], out
