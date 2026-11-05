"""A real setuptools editable finder on an old verifier Python (#790, review round 1).

``editable_finder_sample.txt`` is the finder module setuptools generated for a flat-layout project
(``pip install -e``, Python 3.9). MAPPING and NAMESPACES are module globals there, not class
attributes, which the first version of the probe did not read.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import commit_change
from test_old_python_inventory import CASES, FILES, UNAVAILABLE, rejection, stub

SAMPLE = Path(__file__).parent / "editable_finder_sample.txt"
INSTALL = "sys.path.insert(0, {site!r})\nimport __editable___pkgb_0_1_finder as f\nf.install()"
# A hook that declines every path (ImportError means "not mine"), so imports still work on any OS.
HOOK = "def odd(path):\n    raise ImportError\nsys.path_hooks.insert(0, odd)"


def finder_site(tmp_path, mapping: dict, namespaces: dict) -> str:
    site = tmp_path / "esite"
    site.mkdir()
    text = SAMPLE.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(text):
        if line.startswith("MAPPING"):
            text[number] = f"MAPPING = {mapping!r}"
        if line.startswith("NAMESPACES"):
            text[number] = f"NAMESPACES = {namespaces!r}"
    (site / "__editable___pkgb_0_1_finder.py").write_text("\n".join(text) + "\n")
    return INSTALL.format(site=str(site))


@pytest.mark.parametrize("path", sorted(CASES))
def test_a_setuptools_editable_finder_does_not_block_a_fix(repo, tmp_path, path):
    """Before: the probe exited on the finder, so F1-F4 were refused. Now they are allowed."""
    commit_change(repo, FILES)
    setup = finder_site(tmp_path, {"pkgB": "/x/pkgB"}, {"pkgB.tests": ["/x/pkgB/tests"]})
    assert rejection(repo, path, stub(tmp_path, setup=setup)) is None


def test_a_name_only_the_editable_finder_knows_stays_protected(repo, tmp_path):
    commit_change(repo, FILES)
    setup = finder_site(tmp_path, {"base": "/x/base"}, {"other.sub": []})
    assert rejection(repo, "pkgB/base.py", stub(tmp_path, setup=setup)) == (
        "PROTECTED_VERIFIER_INPUT"
    )


def test_an_editable_finder_without_its_module_tables_fails_closed(repo, tmp_path):
    commit_change(repo, FILES)
    setup = finder_site(tmp_path, {}, {}) + "\ndel f.MAPPING"
    assert rejection(repo, "pkgB/base.py", stub(tmp_path, setup=setup)) == UNAVAILABLE


def test_the_unavailable_reason_names_the_kind_of_failure(repo, tmp_path):
    from test_old_python_inventory import task

    from cairnwork.pipeline import TaskRejected, check_task

    commit_change(repo, FILES)
    python = stub(tmp_path, setup=HOOK)
    with pytest.raises(TaskRejected) as caught:
        check_task(task(repo, "pkgB/base.py"), verifier_python=python)
    assert "CalledProcessError" in str(caught.value) and "unknown path hook" in str(caught.value)


@pytest.fixture(scope="module")
def real_39_venv(tmp_path_factory):
    """A real CPython 3.9 venv with a flat-layout project installed editable by setuptools."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    root = tmp_path_factory.mktemp("real39")
    proj = root / "proj"
    (proj / "pkgB" / "tests").mkdir(parents=True)
    (proj / "pkgB" / "__init__.py").write_text("")
    (proj / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=64"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="pkgb"\nversion="0.1"\n'
    )
    try:
        py = str(root / "v/bin/python")
        for cmd in (
            [uv, "venv", "-p", "3.9", str(root / "v")],
            [uv, "pip", "install", "-p", py, "-e", str(proj)],
        ):
            subprocess.run(cmd, check=True, capture_output=True, timeout=240)
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.skip(f"no real Python 3.9 with setuptools here: {exc}")
    return str(root / "v/bin/python")


@pytest.mark.parametrize("path", sorted(CASES))
def test_a_real_python_3_9_with_an_editable_install_allows_a_fix(repo, real_39_venv, path):
    """The reviewer's counterexample, end to end through check_task. Main refuses all four."""
    commit_change(repo, FILES)
    assert rejection(repo, path, real_39_venv) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
@pytest.mark.parametrize(
    "name", ("x-1.0.egg-info/top_level.txt", "x-1.0.dist-info/RECORD", "m.pth", "dev.egg-link")
)
def test_an_unreadable_metadata_pth_or_egg_link_file_fails_closed(repo, tmp_path, name):
    """A file the probe cannot read may name modules, so it is a failure, not "absent"."""
    commit_change(repo, FILES)
    site = tmp_path / "site"
    (site / name).parent.mkdir(parents=True)
    (site / name).write_text("x\n")
    (site / name).chmod(0)
    try:
        assert rejection(repo, "pkgB/base.py", stub(tmp_path, extra=[site])) == UNAVAILABLE
    finally:
        (site / name).chmod(0o644)
    assert rejection(repo, "pkgB/base.py", stub(tmp_path, extra=[site])) is None  # the control
