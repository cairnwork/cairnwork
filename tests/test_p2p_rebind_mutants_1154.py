"""Pin rebinding forms of the P2P classifier as dropped (#1154 item 1)."""

from __future__ import annotations

from cairnwork.p2p_collect import classify


def _classify_single(base: str, test_id: str = "t.py::test_a"):
    return classify({test_id}, set(), {"t.py"}, lambda p: "\n", lambda p: base)


# --- Mutant 104 (Lambda walk) ---


def test_walrus_in_lambda_body_does_not_rebind_test():
    base = "def test_a(): pass\nlambda: (test_a := 1)\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_lambda_default_walrus_is_dropped():
    base = "def test_a(): pass\nlambda a=(test_a := 1): a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_lambda_kwonly_default_walrus_is_dropped():
    base = "def test_a(): pass\nlambda *, a=(test_a := 1): a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


# --- Mutants 109, 111 (Comprehension & DictComp) ---


def test_comp_first_iter_walrus_is_dropped():
    base = "def test_a(): pass\n[0 for _ in (test_a := [])]\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_comp_elt_and_ifs_walrus_is_dropped():
    base = "def test_a(): pass\n[0 for _ in [] if (test_a := 1)]\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_dictcomp_first_iter_walrus_is_dropped():
    base = "def test_a(): pass\n{0: 1 for _ in (test_a := [])}\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_dictcomp_key_value_walrus_is_dropped():
    base = "def test_a(): pass\n{(test_a := 0): 1 for _ in [1]}\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


# --- Mutants 124, 127, 128 (Import & ImportFrom) ---


def test_import_as_rebinds_is_dropped():
    base = "def test_a(): pass\nimport os as test_a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_plain_import_rebinds_is_dropped():
    base = "def test_a(): pass\nimport test_a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_import_does_not_rebind_test():
    base = "def test_a(): pass\nimport sys\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_from_import_star_rebinds_is_dropped():
    base = "def test_a(): pass\nfrom helper import *\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_import_from_does_not_rebind_test():
    base = "def test_a(): pass\nfrom os import path\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


# --- Mutants 134, 135 (ExceptHandler) ---


def test_except_as_rebinds_is_dropped():
    base = "def test_a(): pass\ntry:\n    pass\nexcept Exception as test_a:\n    pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_except_handler_does_not_rebind_test():
    base = "def test_a(): pass\ntry:\n    pass\nexcept Exception as other:\n    pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


# --- Mutants 136, 138, 140 (Pattern Matching: MatchAs, MatchStar, MatchMapping) ---


def test_match_as_rebinds_is_dropped():
    base = "def test_a(): pass\nmatch 1:\n    case 1 as test_a:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_match_as_does_not_rebind_test():
    base = "def test_a(): pass\nmatch 1:\n    case 1 as other:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_match_star_rebinds_is_dropped():
    base = "def test_a(): pass\nmatch [1]:\n    case [*test_a]:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_match_star_does_not_rebind_test():
    base = "def test_a(): pass\nmatch [1]:\n    case [*other]:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_match_mapping_rebinds_is_dropped():
    base = "def test_a(): pass\nmatch {}:\n    case {**test_a}:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_unrelated_match_mapping_does_not_rebind_test():
    base = "def test_a(): pass\nmatch {}:\n    case {**other}:\n        pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


# --- Additional Defaults & Class Base Walrus ---


def test_def_kwonly_default_walrus_is_dropped():
    base = "def test_a(): pass\ndef f(*, k=(test_a := 1)): pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_class_base_walrus_is_dropped():
    base = "def test_a(): pass\nclass H((test_a := object)): pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


# --- Mutant 174 (hidden_bindings annotations default) ---


def test_mutant_174_parameter_annotation_walrus_shadowing_is_dropped():
    base = "class TestA:\n    def test_a(self): pass\nclass TestB:\n    def test_a(self): pass\n"
    test = (
        "class TestA:\n    pass\n"
        "class TestB:\n    def test_a(self): pass\n"
        "    def helper(self, x: (test_a := 1)): pass\n"
    )
    side = classify(
        {"t.py::TestA::test_a", "t.py::TestB::test_a"},
        {"t.py::TestB::test_a"},
        {"t.py"},
        lambda p: test,
        lambda p: base,
    )
    assert side.dropped == {"t.py::TestA::test_a"} and side.removed == set()
