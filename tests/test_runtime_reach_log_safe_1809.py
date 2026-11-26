"""#1809: a refusal detail built from a changed path (a deleted one too) never gives a CI log a
command line or a control character. Pure functions only: no run, no sandbox."""

import pytest

from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import check_runtime_reach
from cairnwork.textclean import log_safe

HOSTILE = [  # no NUL: git cannot store one in a file name
    "tests/x\n::error::.../pkg/mod.py",
    "tests/x\r::warning::/pkg/mod.py",
    "tests/x\n##[error]boom/pkg/mod.py",
    "tests/x\x1b[31m/pkg/mod.py",
    "tests/x ::error::/pkg/mod.py",
    "tests/x\x85::error::/pkg/mod.py",
    "tests/::error::x/pkg/mod.py",
    "tests/##[group]x/pkg/mod.py",
]


def _record(plugins=(), sys_path=()):
    return CollectedRecord(
        names=frozenset(),
        plugins=plugins,
        sys_path=sys_path,
        sys_path_finish=(),
        root="/co",
        real=(),
    )


def _no_command(text: str) -> None:
    assert text.isprintable()
    assert "::" not in text
    assert "##[" not in text


@pytest.mark.parametrize("path", HOSTILE)
def test_log_safe_neutralises(path):
    _no_command(log_safe(path))


@pytest.mark.parametrize("path", HOSTILE)
def test_changed_path_named_plugin_still_refused_and_inert(path):
    with pytest.raises(TestCommitRejected) as caught:
        check_runtime_reach(_record(plugins=(("pkg.mod", None),)), [path])
    _no_command(str(caught.value))


@pytest.mark.parametrize("path", HOSTILE)
def test_changed_path_on_sys_path_still_refused_and_inert(path):
    with pytest.raises(TestCommitRejected) as caught:
        check_runtime_reach(_record(sys_path=(path,)), [path])
    _no_command(str(caught.value))


def test_ordinary_path_and_plugin_words_pass():
    changed = ["tests/test_path_plugin.py", "tests/plugin/test_path.py"]
    check_runtime_reach(_record(plugins=(("other.mod", None),), sys_path=("lib",)), changed)


def test_changed_archive_on_recorded_sys_path_still_refused():
    with pytest.raises(TestCommitRejected, match="lib.zip"):
        check_runtime_reach(_record(sys_path=("tests/lib.zip",)), ["tests/lib.zip"])


@pytest.mark.parametrize("path", ["a:::b", "::::x", "a::b", "x:::::"])
def test_colon_runs_leave_no_double_colon(path):
    assert "::" not in log_safe(path)
