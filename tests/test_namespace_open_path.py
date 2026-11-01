"""#525 review: an own module or package that extends its own ``__path__`` beside an installed
module or regular package of the same name (namespace_shadow, namespace_open_init)."""

import subprocess

import pytest
from conftest import commit_change
from namespace_site import make_site, site_dir
from test_namespace_shadow import EXTEND, gate


@pytest.fixture(scope="module")
def python(tmp_path_factory):
    return make_site(tmp_path_factory.mktemp("site"))


BESIDE_REGULAR = {
    "an installed regular package": ["src/regpkg/calc.py"],
    "an installed pkgutil-style package": [
        "src/pkuco/somelib/__init__.py",
        "src/pkuco/somelib/impl.py",
    ],
}


@pytest.mark.parametrize("paths", BESIDE_REGULAR.values(), ids=BESIDE_REGULAR.keys())
def test_runtime_an_open_own_package_puts_its_directory_ahead_of_an_installed_one(
    repo, python, paths
):
    commit_change(repo, {f"src/{paths[0].split('/')[1]}/__init__.py": EXTEND})
    code = f"import {paths[-1][4:-3].replace('/', '.')} as m; print(m.__file__)"
    run, env = [python, "-c", code], {"PYTHONPATH": str(repo / "src")}
    assert "site-packages" in subprocess.check_output(run, env=env, text=True)
    commit_change(repo, dict.fromkeys(paths, ""))
    assert subprocess.check_output(run, env=env, text=True).strip() == str(repo / paths[-1])


@pytest.mark.parametrize("paths", BESIDE_REGULAR.values(), ids=BESIDE_REGULAR.keys())
def test_a_deep_path_below_an_open_own_package_beside_an_installed_one_is_refused(
    repo, python, paths
):
    commit_change(repo, {f"src/{paths[0].split('/')[1]}/__init__.py": EXTEND})
    assert gate(repo, paths, python) == tuple(paths)


def test_an_own_module_below_an_open_package_beside_an_installed_one_stays_editable(repo, python):
    """The base's own file is found first before and after, as at the leaf."""
    commit_change(repo, {"src/regpkg/__init__.py": EXTEND, "src/regpkg/calc.py": "x = 1\n"})
    assert gate(repo, ["src/regpkg/calc.py"], python) == ()
    assert gate(repo, ["src/regpkg/calc.py"], python, allow_deletions=True) == (
        "src/regpkg/calc.py",
    )


def test_a_plain_own_package_beside_an_installed_one_ends_the_walk(repo, python):
    commit_change(repo, {"src/regpkg/__init__.py": ""})
    assert gate(repo, ["src/regpkg/calc.py", "src/regpkg/new.py"], python) == ()


def test_a_package_that_extends_its_spec_search_locations_is_refused(repo, python):
    site = str(site_dir(python) / "nsco")
    commit_change(
        repo, {"src/nsco/__init__.py": f"__spec__.submodule_search_locations.append({site!r})\n"}
    )
    assert gate(repo, ["src/nsco/somelib/impl.py"], python) == ("src/nsco/somelib/impl.py",)


@pytest.mark.parametrize("order", [1, -1])
def test_what_is_read_of_each_own_file_is_kept_by_its_full_path(repo, python, order):
    """A plain and an open package share the ``__init__.py`` file name."""
    commit_change(repo, {"src/nsco/__init__.py": "", "src/regpkg/__init__.py": EXTEND})
    paths = ["src/nsco/somelib/impl.py", "src/regpkg/calc.py"][::order]
    assert gate(repo, paths, python) == ("src/regpkg/calc.py",)
