"""#1272: every place that needs the file of a node id reads it at the file boundary
(``node_file.file_part``), never at the first ``::``, so a test file path holding ``::`` is
handled the same everywhere. The scan at the end keeps a new first-``::`` split out of src."""

import re
from pathlib import Path

from cairnwork import (
    collect_reader,
    p2p_classify_child,
    p2p_collect,
    p2p_nodes,
    pycache_guard,
    skip_evidence,
)
from cairnwork.node_file import file_part, name_part
from cairnwork.verdict_record import Verdict

ODD = "a::b/test_t.py"
NODE = f"{ODD}::test_x[1]"


def test_file_part_and_name_part():
    assert (file_part(NODE), name_part(NODE)) == (ODD, "test_x[1]")
    assert (file_part(f"{ODD}::C::m"), name_part(f"{ODD}::C::m")) == (ODD, "C::m")
    assert (file_part("tests/t.py"), name_part("tests/t.py")) == ("tests/t.py", "")
    assert (file_part("bare"), name_part("bare")) == ("bare", "")


def test_verdict_owns_a_test_of_a_target_in_a_path_with_double_colons():
    verdict = Verdict(True, {NODE: "passed"}, paths={NODE: ODD})
    assert verdict.not_run((f"{ODD}::test_x",)) == ()
    assert verdict.not_run((f"{ODD}::test_other",)) == (f"{ODD}::test_other",)


def test_passing_targets_name_the_whole_file():
    targets, nodes, left = p2p_nodes.passing_targets({NODE: "passed"})
    assert targets == [ODD] or targets == (ODD,)
    assert nodes == (NODE,) and left == 0


def test_target_dirs_use_the_directory_of_the_whole_file():
    assert pycache_guard._target_dirs((f"{ODD}::test_x",)) == ("a::b",)


def test_skip_evidence_reads_the_whole_file_under_tests():
    assert skip_evidence._node_file(f"tests/{ODD}::test_x") == f"tests/{ODD}"
    assert skip_evidence._node_file(f"{ODD}::test_x") == ""  # not under tests/


def test_exposed_ids_need_a_class_and_a_method_after_the_file():
    assert collect_reader._valid_exposed_id(f"{ODD}::C::m")
    assert not collect_reader._valid_exposed_id(f"{ODD}::m")  # file and name only
    assert collect_reader._file(NODE) == ODD
    assert collect_reader._files([NODE], frozenset({ODD})) == frozenset({ODD})


def test_the_file_helpers_of_p2p_agree():
    assert p2p_collect._file(NODE) == ODD
    assert p2p_classify_child._file(NODE) == ODD


# Lines that split at "::" on purpose, each with its reason; anything else is a new site.
ALLOWED = {
    "src/cairnwork/node_file.py": "the file-boundary parser itself",
    "src/cairnwork/node_ids.py": "re-exports it",
    "src/cairnwork/deferred_tests.py": "node_id there is a name only; the prefix walk of a whole"
    " node id starts at two parts",
    "src/cairnwork/p2p_classify_child.py": "rpartition: the last name segment, any path",
    "src/cairnwork/report_guard_exposed.py": "runs inside the pytest process; a Module collector's"
    " nodeid has no ::, and _without_params reassembles path::rest unchanged",
    "src/cairnwork/collect_reader.py": "split of the name part after file_part",
}
SPLIT = re.compile(r"""(split|partition)\(["']::["']""")


def test_no_new_first_double_colon_split_in_src():
    root = Path(__file__).resolve().parent.parent
    found = sorted(
        str(path.relative_to(root))
        for path in (root / "src").rglob("*.py")
        if SPLIT.search(path.read_text())
    )
    assert [p for p in found if p not in ALLOWED] == []
