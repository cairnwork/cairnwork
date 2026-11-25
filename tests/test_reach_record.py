"""#1769 review, unit level: which base files the reach run collects, and when it is not made."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork import base_tree_targets as btt
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected

CODE = "TEST_COMMIT_CONFTEST_STATIC"
FILES = {
    "tests/conftest.py": "",
    "tests/test_a.py": "",
    "tests/sib/conftest.py": "",
    "tests/sib/test_s.py": "",
    "tests/data/lib.zip": "z",
    "tests/lib/helper_mod.py": "",
    "tests/test_sig.py": "import sys\nsys.path.insert(0, 'x')\n",
}
TREE = ("tests/sib/test_s.py", "tests/test_a.py")


@pytest.fixture
def task(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=x", "-c", "user.email=x@x", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    for name, text in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    git("add", "-A")
    git("commit", "-qm", "base")
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: TREE)
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: None)  # the static rule is quiet
    return SimpleNamespace(
        repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t", test_roots=("tests",)
    )


def _record(task, *, plugin=None, sys_path=()):
    root = str(Path(task.repo).resolve())
    plugins = (("helper_mod", str(Path(root) / plugin)),) if plugin else ()
    paths = tuple(str(Path(root) / p) for p in sys_path)
    return CollectedRecord(frozenset({"s"}), plugins, paths, paths, root)


class Runs:
    """A fake run_verifiers: the targets of each run; ``records`` are handed out in turn."""

    def __init__(self, monkeypatch, records):
        self.targets, self.records = [], list(records)
        monkeypatch.setattr(bfn, "run_verifiers", self)

    def __call__(self, ctx, commit, specs, run_id, **kwargs):
        self.targets.append(specs[0].targets)
        record = self.records.pop(0) if self.records else None
        return (
            SimpleNamespace(fixture_names=frozenset() if record else None, collect_record=record),
        )


CTX = SimpleNamespace(account=None)


def check(task, paths):
    bfn.check_with_base(CTX, task, paths, "r")


def test_any_changed_base_file_runs_the_whole_tree_plus_the_changed_py_files(task, monkeypatch):
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/data/lib.zip", "tests/lib/helper_mod.py"))
    # a conftest in a sibling directory is not what chose these: the base tree was
    assert runs.targets == [("tests/lib/helper_mod.py", *TREE)]


def test_an_edited_base_test_file_is_not_collected_twice(task, monkeypatch):
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/sib/test_s.py", "tests/data/lib.zip"))
    assert runs.targets == [("tests/sib/test_s.py", "tests/test_a.py")]


def test_only_a_conftest_edit_makes_no_reach_run(task, monkeypatch):
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/conftest.py",))
    assert runs.targets == []


@pytest.mark.parametrize("beside", [(), ("tests/conftest.py",)])
def test_a_sys_path_entry_the_base_lacks_is_refused_for_an_added_file_alone(
    task, monkeypatch, beside
):
    """Added files alone (or with a conftest edit) still get the run (review, round 1)."""
    runs = Runs(monkeypatch, [_record(task, sys_path=("tests/data/missing.zip",))])
    with pytest.raises(TestCommitRejected) as caught:
        check(task, (*beside, "tests/data/missing.zip"))
    assert caught.value.code == CODE
    assert len(runs.targets) == 1


@pytest.mark.parametrize("beside", [(), ("tests/conftest.py",)])
def test_an_added_file_no_entry_names_is_accepted_alone(task, monkeypatch, beside):
    runs = Runs(monkeypatch, [_record(task, sys_path=("tests/data/missing.zip",))])
    check(task, (*beside, "tests/new.py"))
    assert len(runs.targets) == 1


def test_a_conftest_beside_a_changed_base_file_does_get_the_run(task, monkeypatch):
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/conftest.py", "tests/data/lib.zip"))
    assert len(runs.targets) == 1


def test_a_changed_file_that_cannot_be_collected_does_not_void_the_record(task, monkeypatch):
    record = _record(task, sys_path=("tests/data/lib.zip",))
    runs = Runs(monkeypatch, [None, record])  # the first run (with the edited file) gave nothing
    with pytest.raises(TestCommitRejected) as caught:
        check(task, ("tests/sib/test_s.py", "tests/data/lib.zip"))
    assert caught.value.code == CODE
    assert runs.targets == [
        ("tests/sib/test_s.py", "tests/test_a.py"),
        ("tests/test_a.py", "tests/sib/conftest.py"),  # the dropped file's directory picked again
    ]


def test_a_dropped_file_with_no_path_signal_is_accepted_as_the_old_head_did(task, monkeypatch):
    """Layer1 ruling 2: a broken base test that touches no sys.path or plugin is not a refusal."""
    runs = Runs(monkeypatch, [None, _record(task)])
    check(task, ("tests/sib/test_s.py",))
    assert len(runs.targets) == 2


@pytest.mark.parametrize("unreadable", [False, True])
def test_a_dropped_file_with_a_path_signal_or_no_base_text_is_refused(
    task, monkeypatch, unreadable
):
    """Fail closed: the rerun saw nothing of a file that may add to sys.path, or cannot be read."""
    if unreadable:
        monkeypatch.setattr(bfn, "run_git_bytes", lambda *a: (_ for _ in ()).throw(OSError()))
    Runs(monkeypatch, [None, _record(task)])
    with pytest.raises(TestCommitRejected) as caught:
        check(task, ("tests/test_sig.py" if not unreadable else "tests/sib/test_s.py",))
    assert caught.value.code == CODE


def test_a_failed_run_with_no_changed_py_file_is_not_repeated(task, monkeypatch):
    runs = Runs(monkeypatch, [None])
    check(task, ("tests/data/lib.zip",))
    assert len(runs.targets) == 1


def test_a_base_over_the_cap_runs_one_test_file_per_conftest_directory(task, monkeypatch):
    """Layer1 ruling 2 (d): every base conftest is loaded; no mass refusal of a big repo."""
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 1)
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/lib/helper_mod.py",))
    assert runs.targets == [("tests/lib/helper_mod.py", "tests/sib/test_s.py")]


def test_a_base_over_the_cap_still_refuses_a_changed_archive_a_conftest_puts_on_sys_path(
    task, monkeypatch
):
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 1)
    Runs(monkeypatch, [_record(task, sys_path=("tests/data/lib.zip",))])
    with pytest.raises(TestCommitRejected) as caught:
        check(task, ("tests/lib/helper_mod.py", "tests/data/lib.zip"))
    assert caught.value.code == CODE


@pytest.mark.parametrize("bad", ["unreadable tree", "unreadable dirs", "too many dirs"])
@pytest.mark.parametrize("paths", [("tests/data/lib.zip",), ("tests/lib/helper_mod.py",)])
def test_a_plan_that_cannot_be_made_refuses_with_or_without_a_changed_py_file(
    task, monkeypatch, bad, paths
):
    """Layer1 round 3, 1(b)(c): fail closed whatever the commit changes (no run is made)."""
    if bad == "unreadable tree":
        monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: None)
    elif bad == "unreadable dirs":
        monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: None)
    else:
        monkeypatch.setattr(btt, "MAX_CONFTEST_DIRS", 1)  # the base has two conftest directories
    runs = Runs(monkeypatch, [_record(task)])
    with pytest.raises(TestCommitRejected) as caught:
        check(task, paths)
    assert caught.value.code == CODE
    assert runs.targets == []


@pytest.mark.parametrize("cap", [300, 1])
def test_a_base_over_the_cap_or_not_always_runs_at_least_one_tree_file(task, monkeypatch, cap):
    """Layer1 round 3, 1(a): a root conftest or ini loads with any collected file."""
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", cap)
    monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: ())
    runs = Runs(monkeypatch, [_record(task)])
    check(task, ("tests/data/lib.zip",))
    assert runs.targets[0][0] == TREE[0]
    assert len(runs.targets[0]) == (len(TREE) if cap == 300 else 1)


@pytest.mark.parametrize("cap", [300, 1])
def test_a_conftest_directory_with_no_base_test_gets_its_conftest_as_a_target(
    task, monkeypatch, cap
):
    """Layer1 round 3, 3: the commit may add a test there and change what that conftest adds."""
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", cap)
    monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: ("tests/quiet",))
    runs = Runs(monkeypatch, [_record(task, sys_path=("tests/data/lib.zip",))])
    with pytest.raises(TestCommitRejected):
        check(task, ("tests/quiet/test_new.py", "tests/data/lib.zip"))
    assert "tests/quiet/conftest.py" in runs.targets[0]


def test_a_dropped_test_that_was_the_only_one_below_a_conftest_leaves_its_conftest_a_target(
    task, monkeypatch
):
    """Layer1 round 3, 2: the rerun picks again from the tree minus the dropped file."""
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: ("tests/sib/test_s.py",))
    record = _record(task, sys_path=("tests/data/lib.zip",))
    runs = Runs(monkeypatch, [None, record])
    with pytest.raises(TestCommitRejected):
        check(task, ("tests/sib/test_s.py", "tests/data/lib.zip"))
    assert runs.targets[1] == ("tests/conftest.py", "tests/sib/conftest.py")


@pytest.mark.parametrize(
    "word",
    ["sys", "path", "plugin", "importlib", "import_module", "__import__", "exec", "eval", "site"],
)
def test_each_path_signal_word_in_a_dropped_file_refuses(task, monkeypatch, word):
    monkeypatch.setattr(bfn, "run_git_bytes", lambda *a: f"x = 1  # {word}\n".encode())
    Runs(monkeypatch, [None, _record(task)])
    with pytest.raises(TestCommitRejected):
        check(task, ("tests/sib/test_s.py",))


def test_a_dropped_file_with_no_signal_word_is_accepted(task, monkeypatch):
    monkeypatch.setattr(bfn, "run_git_bytes", lambda *a: b"import no_such_module\n")
    Runs(monkeypatch, [None, _record(task)])
    check(task, ("tests/sib/test_s.py",))


def test_an_account_run_makes_no_reach_run(task, monkeypatch):
    runs = Runs(monkeypatch, [_record(task)])
    bfn.check_with_base(SimpleNamespace(account=object()), task, ("tests/data/lib.zip",), "r")
    assert runs.targets == []


def test_a_run_that_raises_leaves_the_static_verdict(task, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(bfn, "run_verifiers", boom)
    check(task, ("tests/data/lib.zip",))


# what the record may refuse
def test_an_archive_on_sys_path_that_the_commit_changes_is_refused(task, monkeypatch):
    Runs(monkeypatch, [_record(task, sys_path=("tests/data/lib.zip",))])
    with pytest.raises(TestCommitRejected):
        check(task, ("tests/data/lib.zip",))


def test_a_changed_base_file_off_sys_path_is_not_refused(task, monkeypatch):
    Runs(monkeypatch, [_record(task, sys_path=("tests/data/lib.zip",))])
    check(task, ("tests/lib/helper_mod.py",))


def test_a_plugin_file_the_commit_changes_is_refused(task, monkeypatch):
    Runs(monkeypatch, [_record(task, plugin="tests/lib/helper_mod.py")])
    with pytest.raises(TestCommitRejected):
        check(task, ("tests/lib/helper_mod.py",))


def test_a_file_the_commit_adds_is_never_a_plugin_hit(task, monkeypatch):
    """The base-only filter: a path the base lacks must not be matched against plugin files."""
    Runs(monkeypatch, [_record(task, plugin="tests/lib/new_plugin.py")])
    check(task, ("tests/data/lib.zip", "tests/lib/new_plugin.py"))


def test_a_sys_path_entry_the_base_names_but_lacks_is_refused_when_the_commit_adds_it(
    task, monkeypatch
):
    """Fail closed: the entry was absent in the base run; the commit supplies the archive."""
    Runs(monkeypatch, [_record(task, sys_path=("tests/data/missing.zip",))])
    with pytest.raises(TestCommitRejected) as caught:
        check(task, ("tests/data/lib.zip", "tests/data/missing.zip"))
    assert caught.value.code == CODE


def test_an_added_file_that_no_entry_names_is_not_refused(task, monkeypatch):
    Runs(monkeypatch, [_record(task, sys_path=("tests/data/missing.zip",))])
    check(task, ("tests/data/lib.zip", "tests/data/other.zip"))


def test_a_changed_conftest_the_base_ran_as_a_plugin_is_left_to_the_conftest_rules(
    task, monkeypatch
):
    """Pins runtime_reach's conftest exclusion through the whole path."""
    Runs(monkeypatch, [_record(task, plugin="tests/conftest.py")])
    check(task, ("tests/conftest.py", "tests/data/lib.zip"))


def test_the_base_file_list_reaches_the_reach_check(task, monkeypatch):
    seen = []
    monkeypatch.setattr(bfn, "check_runtime_reach", lambda *a: seen.append(a))
    Runs(monkeypatch, [_record(task)])
    check(task, ("tests/data/lib.zip",))
    assert "tests/data/lib.zip" in seen[0][3] and "tests/test_a.py" in seen[0][3]


def test_the_base_file_list_is_read_with_the_option_terminator(task, monkeypatch):
    seen = []
    real = bfn.run_git_bytes
    monkeypatch.setattr(bfn, "run_git_bytes", lambda *a: seen.append(a) or real(*a))
    paths = bfn._base_paths(task)
    assert paths is not None and "tests/test_a.py" in paths
    assert seen[0][-2:] == (task.base_commit, "--")
