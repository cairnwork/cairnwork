"""#916: a test-commit conftest may not take a name the base binds or its tests request
(conftest_names.py), and nothing in a new node may run code when the conftest is imported."""

import pytest
from conftest import commit_change, git

import cairnwork.deferred_tests as dt
from cairnwork.conftest_check import check_conftests
from cairnwork.conftest_names import bound, requested

BASE = (
    "import pytest\nSTRICT = True\n\n\n@pytest.fixture\ndef check():\n"
    "    def _c(v):\n        if STRICT:\n            assert v.is_valid()\n\n    return _c\n"
)
BASE_TEST = "def test_old(check, tmp_path):\n    check(object)\n"


def _check(repo, files, path="tests/conftest.py", base_files=None):
    commit_change(
        repo, {"tests/conftest.py": BASE, "tests/test_old.py": BASE_TEST, **(base_files or {})}
    )
    base = git(repo, "rev-parse", "HEAD")
    commit = commit_change(repo, files, message="tests")
    check_conftests(repo, base, commit, list(files))


REFUSED = {
    "a constant the same file binds": ("tests/conftest.py", BASE + "STRICT = False\n"),
    "a fixture of the same name": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef check():\n    return lambda v: None\n",
    ),
    "a fixture whose name= is taken": (
        "tests/conftest.py",
        BASE + '\n\n@pytest.fixture(name="check")\ndef other():\n    return lambda v: None\n',
    ),
    "an import that binds a taken name": (
        "tests/conftest.py",
        BASE + "from os import sep as STRICT\n",
    ),
    "a nested conftest overriding a fixture above it": (
        "tests/sub/conftest.py",
        "import pytest\n\n\n@pytest.fixture\ndef check():\n    return lambda v: None\n",
    ),
    "a fixture named like a builtin a base test requests": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef tmp_path():\n    return 1\n",
    ),
    "a fixture a base test requests by usefixtures": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef env():\n    return 1\n",
    ),
    "a nested conftest importing a name that registers a fixture above it": (
        "tests/sub/conftest.py",
        "from os.path import join as check\n",
    ),
    "an annotation that calls code": ("tests/conftest.py", BASE + "X: print('x') = 0\n"),
    "a parameter default that calls code": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef f(_=print('x')):\n    return 1\n",
    ),
    "a parameter annotation that calls code": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef f(request: print('x')):\n    return 1\n",
    ),
    "a return annotation that calls code": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef f() -> (lambda: 1)():\n    return 1\n",
    ),
    "a parameter annotation with an operator (a dunder call, no Call node)": (
        "tests/conftest.py",
        BASE + "\n\n@pytest.fixture\ndef f(request: pytest.mark + 1):\n    return 1\n",
    ),
}
USES = {
    "tests/sub/test_env.py": 'import pytest\n\n\n@pytest.mark.usefixtures("env")\n'
    "def test_e():\n    pass\n"
}


@pytest.mark.parametrize("name", list(REFUSED))
def test_a_taken_name_or_import_time_code_is_refused(repo, name):
    path, text = REFUSED[name]
    with pytest.raises(dt.TestCommitRejected, match="TEST_COMMIT_CONFTEST_STATIC"):
        _check(repo, {path: text}, base_files=USES)


ALLOWED = {
    "a new fixture with a new name": BASE + "\n\n@pytest.fixture\ndef two():\n    return 2\n",
    "importing again what the base imports": BASE + "import pytest\n",
    "a plain annotation and a literal default": BASE
    + "X: int | None = 0\n\n\n@pytest.fixture\ndef f(n: int = 2) -> list[int]:\n    return [n]\n",
}


NESTED_ALLOWED = {
    "a nested conftest importing pytest, as the one above does": (
        "import pytest\n\n\n@pytest.fixture\ndef local():\n    return 1\n"
    ),
    "a nested literal reusing a constant name of the conftest above": "STRICT = False\n",
}


@pytest.mark.parametrize("name", list(NESTED_ALLOWED))
def test_another_module_s_globals_are_its_own(repo, name):
    _check(repo, {"tests/sub/conftest.py": NESTED_ALLOWED[name]})


@pytest.mark.parametrize("name", list(ALLOWED))
def test_a_new_name_with_plain_annotations_is_allowed(repo, name):
    _check(repo, {"tests/conftest.py": ALLOWED[name]})


def test_a_name_requested_only_outside_the_conftest_s_directory_is_free(repo):
    other = {"tests/other/test_o.py": "def test_o(local):\n    pass\n"}
    sub = "import pytest\n\n\n@pytest.fixture\ndef local():\n    return 1\n"
    _check(repo, {"tests/sub/conftest.py": sub}, base_files=other)


def test_a_base_file_that_does_not_parse_refuses_rather_than_passes(repo):
    with pytest.raises(dt.TestCommitRejected, match="cannot read the base file"):
        _check(repo, {"tests/conftest.py": ALLOWED["a new fixture with a new name"]},
               base_files={"tests/data/broken.py": "def (:\n"})  # fmt: skip


def test_bound_and_requested_read_every_form():
    import ast

    tree = ast.parse(
        "import a.b as c\nfrom d import e\nx = y = 1\nz: int = 2\n"
        "@pytest.fixture(name='alias')\ndef f(p, /, q, *, r):\n"
        "    request.getfixturevalue('dyn')\n    return lambda s: s\n"
        "@pytest.mark.usefixtures('u1', 'u2')\nclass T:\n    pass\n"
    )
    assert bound(tree) == {"c", "e", "x", "y", "z", "f", "alias", "T"}
    assert requested(tree) == {"p", "q", "r", "s", "dyn", "u1", "u2"}
