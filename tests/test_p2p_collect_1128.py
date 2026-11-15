"""#1128 item 2: only missing parameter cases of still-collected functions are removed."""

from __future__ import annotations

import subprocess
import sys

import pytest

from cairnwork.p2p_collect import classify


def collect(tmp_path, source, filename="test_sample.py"):
    path = tmp_path / filename
    path.write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 5}, result.stderr
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def split(base_ids, test_ids, base, test, edited=("test_sample.py",)):
    side = classify(
        base_ids,
        test_ids,
        edited,
        lambda _: test,
        lambda _: base,
    )
    return side.removed, side.dropped


BASE = "import pytest\n@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n): pass\n"
ONE_CASE = "import pytest\n@pytest.mark.parametrize('n', [1])\ndef test_x(n): pass\n"

ANNOTATED_REBIND = {
    "with-value": "test_x: object = _other\n",
    "walrus-in-annotation": "test_x: (test_x := _other)\n",
    "plain-assign": "test_x = _other\n",
}


@pytest.mark.parametrize("case", sorted(ANNOTATED_REBIND))
def test_a_real_rebinding_next_to_an_annotation_is_still_dropped(case):
    other = "def test_x():\n    assert True\n"
    base = other + "def test_x():\n    assert True\n" + ANNOTATED_REBIND[case]
    base += "def test_keep():\n    assert True\n"
    test = other + "def test_keep():\n    assert True\n"
    assert split(
        {"t.py::test_x", "t.py::test_keep"},
        {"t.py::test_keep"},
        base,
        test,
        edited=("t.py",),
    ) == (set(), {"t.py::test_x"})


def test_missing_param_case_is_removed_when_function_is_collected(tmp_path):
    base_ids = collect(tmp_path, BASE)
    test_ids = collect(tmp_path, ONE_CASE)
    assert split(base_ids, test_ids, BASE, ONE_CASE) == (
        {"test_sample.py::test_x[2]"},
        set(),
    )


def test_plain_test_removed_stays_must_block(tmp_path):
    base = "def test_x(): pass\ndef test_keep(): pass\n"
    test = "if False:\n    def test_x(): pass\ndef test_keep(): pass\n"
    assert split(collect(tmp_path, base), collect(tmp_path, test), base, test) == (
        set(),
        {"test_sample.py::test_x"},
    )


def test_deleted_whole_parametrized_function_keeps_main_behavior(tmp_path):
    test = "def test_keep(): pass\n"
    base = BASE + "def test_keep(): pass\n"
    assert split(collect(tmp_path, base), collect(tmp_path, test), base, test) == (
        {"test_sample.py::test_x[1]", "test_sample.py::test_x[2]"},
        set(),
    )


def test_missing_case_in_unedited_file_stays_dropped(tmp_path):
    base_ids = collect(tmp_path, BASE)
    test_ids = collect(tmp_path, ONE_CASE)
    assert split(base_ids, test_ids, BASE, ONE_CASE, edited=()) == (
        set(),
        {"test_sample.py::test_x[2]"},
    )


def test_plain_id_is_not_a_removed_parameter_case_when_function_is_collected(tmp_path):
    base = "def test_x(): pass\n"
    test = "import pytest\n@pytest.mark.parametrize('n', [1])\ndef test_x(n): pass\n"
    assert split(collect(tmp_path, base), collect(tmp_path, test), base, test) == (
        set(),
        {"test_sample.py::test_x"},
    )


def test_missing_case_when_function_is_not_collected_is_not_newly_removed(tmp_path):
    test = (
        "import pytest\nif False:\n    @pytest.mark.parametrize('n', [1])\n"
        "    def test_x(n): pass\n"
    )
    base_ids = collect(tmp_path, BASE)
    test_ids = collect(tmp_path, test)
    assert split(base_ids, test_ids, BASE, test) == (
        set(),
        {"test_sample.py::test_x[1]", "test_sample.py::test_x[2]"},
    )
