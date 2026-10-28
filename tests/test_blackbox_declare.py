"""#303 declarations of methods, expected raises and float tolerances: what loads, what is refused
at load time (never silently ignored), what the commitment covers, and what reaches the run."""

import builtins
import json
from dataclasses import replace

import pytest
from test_blackbox_kinds import ACCOUNT_CHECK, MEAN_CHECK, RUN, SALT, calls, declare, judged

from cairnwork import blackbox, blackbox_judge

OLD = {
    "salt": SALT,
    "checks": [{"id": "add", "module": "calc", "function": "add", "cases": [
        {"id": "c1", "args": [2, 3], "expected": 5},
        {"id": "c2", "args": [[1, "x"]], "kwargs": {"k": None}, "expected": {"a": [True, None]}},
    ]}],
}  # fmt: skip
# Computed with the code of be29786 (#308), before methods, raises and floats existed.
OLD_COMMITMENT = "sha256:d6a71303b3e40461badf4b3a2201f50519150685d75c027e1fa76206bc34ab2b"
OLD_INPUT = (
    b'{"checks":[{"cases":[{"args":[2,3],"id":"c1","kwargs":{}},{"args":[[1,"x"]],"id":"c2",'
    b'"kwargs":{"k":null}}],"function":"add","id":"add","module":"calc"}],"out":"/out",'
    b'"paths":["/co"]}'
)


def test_an_older_declaration_keeps_its_commitment_and_runner_input():
    declared = blackbox.parse(json.dumps(OLD).encode())
    assert declared.commitment() == OLD_COMMITMENT
    assert declared.runner_input(["/co"], "/out") == OLD_INPUT


def test_the_new_kinds_load():
    declared = declare(ACCOUNT_CHECK, MEAN_CHECK)
    acct, mean = declared.checks
    assert (acct.kind, acct.name, len(acct.cases[0].calls)) == ("class", "Account", 3)
    assert acct.cases[0].calls[1].raises == '{"message":"bad amount -1","type":"ValueError"}'
    tolerance = {"abs_tol": {"$float": (1e-12).hex()}, "rel_tol": {"$float": (1e-9).hex()}}
    assert (mean.kind, json.loads(mean.cases[0].tolerance)) == ("function", tolerance)


def mean_case(**over):
    """MEAN_CHECK with its first case changed, the others kept."""
    first, *rest = MEAN_CHECK["cases"]
    return MEAN_CHECK | {"cases": [first | over, *rest]}


def mean_without(*keys, **over):
    first, *rest = MEAN_CHECK["cases"]
    case = {k: v for k, v in first.items() if k not in keys}
    return MEAN_CHECK | {"cases": [case | over, *rest]}


def acct_call(**over):
    return ACCOUNT_CHECK | {"cases": [calls({"method": "deposit", "args": [1]} | over)]}


TOO_MANY = [{"method": "deposit", "args": [1], "expected": 1}] * (blackbox.MAX_CALLS + 1)
REFUSED = {
    "float-without-tolerance": mean_without("tolerance"),
    "tolerance-without-float": mean_case(expected=1),
    "tolerance-with-raises": mean_without("expected", raises={"type": "ValueError"}),
    "expected-and-raises": mean_case(raises={"type": "ValueError"}),
    "neither": mean_without("expected", "tolerance"),
    "raises-builtins-prefix": mean_without("expected", "tolerance",
                                           raises={"type": "builtins.ValueError"}),
    "raises-bad-name": mean_without("expected", "tolerance", raises={"type": "Value Error"}),
    "raises-bare-not-builtin": mean_without("expected", "tolerance", raises={"type": "MyError"}),
    "raises-bare-not-exception": mean_without("expected", "tolerance", raises={"type": "len"}),
    "raises-not-str": mean_without("expected", "tolerance", raises={"type": 1}),
    "raises-no-type": mean_without("expected", "tolerance", raises={"message": "x"}),
    "raises-extra": mean_without("expected", "tolerance", raises={"type": "calc.E", "args": []}),
    "raises-null-message": mean_without("expected", "tolerance",
                                        raises={"type": "calc.E", "message": None}),
    "raises-long-message": mean_without("expected", "tolerance",
                                        raises={"type": "calc.E", "message": "m" * 1001}),
    "tolerance-negative": mean_case(tolerance={"abs_tol": -1}),
    "tolerance-bool": mean_case(tolerance={"abs_tol": True}),
    "tolerance-string": mean_case(tolerance={"abs_tol": "0.1"}),
    "tolerance-empty": mean_case(tolerance={}),
    "tolerance-extra": mean_case(tolerance={"abs_tol": 0, "ulps": 1}),
    "tolerance-huge-int": mean_case(tolerance={"abs_tol": 10**400}),
    "float-key-in-args": mean_case(args=[{"$float": "0x1p+0"}]),
    "float-key-in-answer": mean_case(expected={"$float": "0x1p+0", "x": 0.5}),
    "float-key-in-kwargs": mean_case(kwargs={"$float": 1}),
    "function-and-class": MEAN_CHECK | {"class": "Mean"},
    "neither-function-nor-class": {k: v for k, v in MEAN_CHECK.items() if k != "function"},
    "class-case-shape-in-function": MEAN_CHECK | {"cases": ACCOUNT_CHECK["cases"]},
    "function-case-shape-in-class": ACCOUNT_CHECK | {"cases": MEAN_CHECK["cases"]},
    "no-calls": ACCOUNT_CHECK | {"cases": [calls()]},
    "too-many-calls": ACCOUNT_CHECK | {"cases": [calls(*TOO_MANY)]},
    "bad-method": acct_call(method="a.b", expected=1),
    "call-without-args": ACCOUNT_CHECK | {"cases": [calls({"method": "m", "expected": 1})]},
    "call-extra-key": acct_call(expected=1, note="x"),
    "call-neither": acct_call(),
    "init-not-object": ACCOUNT_CHECK | {"cases": [calls({"method": "m", "args": [],
                                                          "expected": 1}, init=[1])]},
    "init-extra-key": ACCOUNT_CHECK | {"cases": [calls({"method": "m", "args": [], "expected": 1},
                                                       init={"args": [], "self": 1})]},
    "class-case-extra-key": ACCOUNT_CHECK | {"cases": [calls({"method": "m", "args": [],
                                                               "expected": 1}) | {"x": 1}]},
}  # fmt: skip


@pytest.mark.parametrize("check", REFUSED.values(), ids=REFUSED.keys())
def test_an_unsupported_shape_is_refused_at_load(check):
    with pytest.raises(blackbox.BlackboxRejected) as caught:
        declare(check)
    assert caught.value.code == "BAD_BLACKBOX"


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_nan_and_infinities_are_refused(token):
    raw = json.dumps({"salt": SALT, "checks": [mean_case(expected=0.5)]}).encode()
    with pytest.raises(blackbox.BlackboxRejected):
        blackbox.parse(raw.replace(b"0.5", token))


def test_a_rejection_never_quotes_an_answer_message_or_tolerance():
    secret = 123456.789012
    for check in (
        mean_case(expected=secret),  # with a bad tolerance below
        mean_without("expected", "tolerance", raises={"type": "SECRET!", "message": "SECRET"}),
        mean_without("tolerance", expected=secret),
    ):
        if "tolerance" in check["cases"][0]:
            check["cases"][0]["tolerance"] = {"abs_tol": -secret}
        with pytest.raises(blackbox.BlackboxRejected) as caught:
            declare(check)
        assert "SECRET" not in str(caught.value) and "123456" not in str(caught.value)


CHANGES = {
    "raise-type": mean_without("expected", "tolerance", raises={"type": "KeyError"}),
    "raise-message": mean_without("expected", "tolerance",
                                  raises={"type": "ValueError", "message": "other"}),
    "tolerance": mean_case(tolerance={"rel_tol": 1e-6}),
    "float-answer": mean_case(expected=0.6666),
    "float-arg": mean_case(kwargs={"scale": 0.25}),
    "kind": ACCOUNT_CHECK | {"id": "mean", "class": "mean"},
}  # fmt: skip
ACCT_CHANGES = {
    "method": {"method": "withdraw"},
    "call-expected": {"expected": 16},
    "call-raises": {"expected": None, "raises": {"type": "ValueError"}},
    "call-tolerance": {"expected": 15.0, "tolerance": {"abs_tol": 0}},
    "init": None,
}


def test_the_commitment_covers_every_new_field():
    variants = [declare(ACCOUNT_CHECK, MEAN_CHECK)]
    variants += [declare(ACCOUNT_CHECK, check) for check in CHANGES.values()]
    for change in ACCT_CHANGES.values():
        case = json.loads(json.dumps(ACCOUNT_CHECK["cases"][0]))
        if change is None:
            case.pop("init")
        else:
            case["calls"][0] |= change
            case["calls"][0] = {k: v for k, v in case["calls"][0].items() if v is not None}
        variants.append(declare(ACCOUNT_CHECK | {"cases": [case]}, MEAN_CHECK))
    commitments = [variant.commitment() for variant in variants]
    assert len(set(commitments)) == len(commitments) == 1 + len(CHANGES) + len(ACCT_CHANGES)


def test_the_run_never_sees_answers_raises_messages_or_tolerances():
    given = declare(ACCOUNT_CHECK, MEAN_CHECK).runner_input(["/co"], "/out")
    text = given.decode()
    for word in ("expected", "raises", "tolerance", "rel_tol", "abs_tol", "message", "bad amount",
                 "no values", "ValueError", (2 / 3).hex(), (1e-9).hex(), SALT):  # fmt: skip
        assert word not in text, word
    checks = json.loads(given)["checks"]
    assert checks[0]["cases"][0] == {
        "id": "c1", "init": {"args": ["ann"], "kwargs": {"balance": 10}},
        "calls": [{"method": "deposit", "args": [5], "kwargs": {}},
                  {"method": "deposit", "args": [-1], "kwargs": {}},
                  {"method": "deposit", "args": [], "kwargs": {"amount": 2}}],
    }  # fmt: skip
    assert checks[1]["cases"][0] == {
        "id": "third",
        "args": [[1, 1, 2]],
        "kwargs": {"scale": {"$float": "0x1.0000000000000p-1"}},
    }


def test_low_entropy_counts_raises_without_a_message_and_class_calls():
    raises = {"id": "empty", "args": [[]], "raises": {"type": "ValueError"}}
    assert declare(MEAN_CHECK | {"cases": [raises]}).checks[0].low_entropy
    raises["raises"] |= {"message": "x"}
    assert not declare(MEAN_CHECK | {"cases": [raises]}).checks[0].low_entropy
    guesses = [
        {"method": "m", "args": [], "expected": True},
        {"method": "m", "args": [], "raises": {"type": "calc.E"}},
    ]
    yes_no = ACCOUNT_CHECK | {"cases": [calls(*guesses)]}
    assert declare(yes_no).checks[0].low_entropy
    assert not declare(ACCOUNT_CHECK).checks[0].low_entropy


# --- #319: aliases, vacuous tolerances, guessable answers, single-field commitment ----------

ALIASES = sorted(
    name for name in dir(builtins)
    if isinstance(getattr(builtins, name), type)
    and issubclass(getattr(builtins, name), BaseException)
    and getattr(builtins, name).__name__ != name
)  # fmt: skip


def raising(kind: str, message: str | None = None):
    raises = {"type": kind} | ({"message": message} if message is not None else {})
    return mean_without("expected", "tolerance", raises=raises)


@pytest.mark.parametrize("alias", ALIASES)
def test_a_builtin_alias_is_refused_because_the_runner_reports_the_class_name(alias):
    with pytest.raises(blackbox.BlackboxRejected, match="builtin exception"):
        declare(raising(alias))


def test_the_aliases_are_the_known_ones_and_their_classes_load():
    assert {"IOError", "EnvironmentError"} <= set(ALIASES)
    for name in ("OSError", "FileNotFoundError", "ValueError"):
        assert declare(raising(name)).checks[0].cases[0].raises


def test_a_relative_tolerance_of_one_or_more_is_refused():
    for rel_tol in (1, 1.0, 5):
        with pytest.raises(blackbox.BlackboxRejected, match="rel_tol must be below 1"):
            declare(mean_case(tolerance={"rel_tol": rel_tol}))
    assert declare(mean_case(tolerance={"rel_tol": 0.99}))  # below 1 loads (a band, not vacuous)


def only_case(case: dict) -> dict:
    return MEAN_CHECK | {"cases": [{"id": "c", "args": [[1]]} | case]}


GUESSABLE = {
    "empty-message": {"raises": {"type": "ValueError", "message": ""}},
    "zero-within-abs-tol": {"expected": 3.7, "tolerance": {"abs_tol": 1e300}},
    "zero-answer": {"expected": [0.0, {"k": -0.0}], "tolerance": {"rel_tol": 1e-9}},
}
NOT_GUESSABLE = {
    "message": {"raises": {"type": "ValueError", "message": "x"}},
    "tight": {"expected": 3.7, "tolerance": {"rel_tol": 1e-9, "abs_tol": 1e-12}},
    "wide-relative": {"expected": 3.7, "tolerance": {"rel_tol": 0.99}},
    "a-string-too": {"expected": [0.0, "s"], "tolerance": {"abs_tol": 1}},
}


@pytest.mark.parametrize("case", GUESSABLE.values(), ids=GUESSABLE.keys())
def test_an_empty_message_or_a_zero_that_matches_is_low_entropy(case):
    assert declare(only_case(case)).checks[0].low_entropy


@pytest.mark.parametrize("case", NOT_GUESSABLE.values(), ids=NOT_GUESSABLE.keys())
def test_other_answers_stay_unlabelled(case):
    assert not declare(only_case(case)).checks[0].low_entropy


def test_one_field_of_a_raise_alone_changes_the_commitment():
    base = declare(ACCOUNT_CHECK, MEAN_CHECK)
    empty = MEAN_CHECK["cases"][1]
    variants = [base]
    for raises in (
        {"type": "ValueError", "message": "other"},
        {"type": "KeyError", "message": "no values"},
    ):
        cases = [*MEAN_CHECK["cases"]]
        cases[1] = empty | {"raises": raises}
        variants.append(declare(ACCOUNT_CHECK, MEAN_CHECK | {"cases": cases}))
    case = json.loads(json.dumps(ACCOUNT_CHECK["cases"][0]))
    case["calls"][1]["raises"]["message"] = "bad amount -2"
    variants.append(declare(ACCOUNT_CHECK | {"cases": [case]}, MEAN_CHECK))
    assert len({variant.commitment() for variant in variants}) == 4


def test_a_float_answer_is_not_matched_by_a_tag_and_a_boolean():
    """#319 item 4: exact JSON types inside a tolerance answer, both ways round."""
    pair = {"id": "mean", "module": "calc", "function": "mean", "cases": [
        {"id": "one", "args": [[1]], "expected": [1.5, 1], "tolerance": {"abs_tol": 0}},
        {"id": "two", "args": [[2]], "expected": [1.5, True], "tolerance": {"abs_tol": 0}},
    ]}  # fmt: skip
    tag = {"$float": (1.5).hex()}
    got = {"one": {"ok": [tag, True]}, "two": {"ok": [tag, 1]}}
    assert judged(got, pair) == "blackbox:mean: MISMATCH [matched 0/2]"


def test_an_import_error_from_the_report_is_cut_and_printable():
    """#319 item 6: the candidate writes this string; it must not run on or forge a line."""
    error = "x" * 200 + "\n] blackbox:c: PASS ["
    run = replace(RUN, report=json.dumps({"checks": {"mean": {"error": error}}}).encode())
    (verdict,) = blackbox_judge.judge_run(declare(MEAN_CHECK), "c" * 40, run)
    assert verdict.reason == "blackbox:mean: VERIFICATION_INCOMPLETE: NOT_IMPORTED:" + "x" * 80
