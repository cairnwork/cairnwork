"""Property tests pinning the checks of issue_task.parse_task_block (#626).

A mutation sweep (2026-11-02) found checks in the eia-task block parser that no test pinned:
invalid JSON, a block that is not an object, a non-string summary, an empty verification list,
the medium difficulty, and the 10-file and 8-verifier boundaries. Each property below is
compared with a small reference written from the module docstring, so a guard that drifts
makes Hypothesis find a counterexample. The only failure a block may cause is IssueRefused:
anything else would leave the Issue queued with no comment, or crash the whole run-once pass.

The block hash tests (exact bytes, reordered keys) pin the exact-bytes hash: pins current
behaviour; spec question open (does a re-laid-out block or reordered keys count as changed?).
"""

import hashlib
import json

import pytest
from hypothesis import HealthCheck, example, given, seed, settings
from hypothesis import strategies as st

from cairnwork.issue_task import IssueRefused, parse_task_block

SETTINGS = settings(
    max_examples=300, deadline=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)  # fmt: skip

GOOD_CMD = "python3 -m pytest -q tests/test_stats.py"
GOOD = {
    "difficulty": "easy",
    "allowed_files": ["src/stats.py"],
    "verification": [GOOD_CMD],
    "capability_ids": ["x"],
}

safe_name = st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True)
safe_path = st.builds(lambda d, f: f"src/{d}/{f}.py", safe_name, safe_name)
json_scalar = st.one_of(st.none(), st.booleans(), st.integers(-3, 3), st.text(max_size=5))
json_any = st.recursive(
    json_scalar,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3), st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=6,
)  # fmt: skip


def body(block_text: str, before: str = "Bug text\n\n", after: str = "\n") -> str:
    return f"{before}```eia-task\n{block_text}\n```{after}"


def code_of(text: str):
    try:
        return parse_task_block(text)
    except IssueRefused as exc:
        return exc.code


# 1. Robustness: whatever JSON sits in the block, the only failure is IssueRefused.
#    (kills: raw not a dict, summary not a str, verification not a list, JSON errors leaking)
@SETTINGS
@seed(20261102)
@given(value=json_any)
@example(value=[])
@example(value="x")
@example(value={**GOOD, "summary": 7})
@example(value={**GOOD, "verification": GOOD_CMD})
def test_any_json_block_is_parsed_or_refused_with_issue_refused(value):
    code_of(body(json.dumps(value)))  # any other exception fails the test


@SETTINGS
@seed(20261102)
@given(text=st.text(max_size=40))
@example(text="{")
@example(text='{"difficulty": "easy",}')
def test_text_that_is_not_json_is_refused_as_json_invalid(text):
    try:
        json.loads(text)
    except ValueError:
        assert code_of(body(text)) == "TASK_BLOCK_JSON_INVALID"


# 2. Counts: allowed_files 1..10 distinct, verification 1..8 (reference oracle).
@SETTINGS
@seed(20261102)
@given(files=st.lists(safe_path, min_size=0, max_size=12))
def test_allowed_files_take_one_to_ten_distinct_paths(files):
    got = code_of(body(json.dumps({**GOOD, "allowed_files": files})))
    ok = 0 < len(files) <= 10 and len(set(files)) == len(files)
    if ok:
        assert not isinstance(got, str), got
        assert got.allowed_files == tuple(files)
    else:
        assert got == "TASK_ALLOWED_FILES_INVALID"


@SETTINGS
@seed(20261102)
@given(n=st.integers(0, 10))
def test_verification_takes_one_to_eight_commands(n):
    cmds = [f"python3 -m pytest tests/test_{i}.py" for i in range(n)]
    got = code_of(body(json.dumps({**GOOD, "verification": cmds})))
    if 0 < n <= 8:
        assert not isinstance(got, str), got
        assert len(got.verifiers) == n
    else:
        assert got == "TASK_VERIFICATION_INVALID"  # 0 verifiers must never become a task


@SETTINGS
@seed(20261102)
@given(value=json_any)
@example(value={"src/stats.py": 1})
@example(value="src/a.py")
def test_allowed_files_that_are_not_a_list_are_refused(value):
    if not isinstance(value, list):
        assert (
            code_of(body(json.dumps({**GOOD, "allowed_files": value})))
            == "TASK_ALLOWED_FILES_INVALID"
        )


@SETTINGS
@seed(20261102)
@given(value=json_any)
@example(value=GOOD_CMD)
@example(value={GOOD_CMD: 1})
def test_verification_that_is_not_a_list_is_refused(value):
    if not isinstance(value, list):
        assert (
            code_of(body(json.dumps({**GOOD, "verification": value})))
            == "TASK_VERIFICATION_INVALID"
        )


# 3. capability_ids: a non-empty list of non-empty strings, nothing else.
@SETTINGS
@seed(20261102)
@given(ids=json_any)
@example(ids=[""])
@example(ids=[7])
@example(ids="x")
def test_capability_ids_take_a_nonempty_list_of_nonempty_strings(ids):
    got = code_of(body(json.dumps({**GOOD, "capability_ids": ids})))
    ok = isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids)
    assert (not isinstance(got, str)) if ok else got == "TASK_CAPABILITY_IDS_INVALID", (ids, got)


# 4. test_commit: absent/empty, or exactly 40 lowercase hex.
@SETTINGS
@seed(20261102)
@given(
    tc=st.one_of(
        st.from_regex(r"[0-9a-f]{38,42}", fullmatch=True), st.text(max_size=44), json_scalar
    )
)
@example(tc="a" * 41)
@example(tc="a" * 40 + "\n")
@example(tc="a" * 40 + "/../x")
def test_test_commit_takes_empty_or_forty_lowercase_hex(tc):
    got = code_of(body(json.dumps({**GOOD, "test_commit": tc})))
    ok = not tc or (
        isinstance(tc, str) and len(tc) == 40 and all(c in "0123456789abcdef" for c in tc)
    )
    if ok:
        assert not isinstance(got, str), (tc, got)
    else:
        assert got == "TASK_TEST_COMMIT_INVALID", (tc, got)


# 5. difficulty: only easy and medium are taken.
@SETTINGS
@seed(20261102)
@given(d=st.one_of(st.sampled_from(["easy", "medium", "hard", "Easy", ""]), json_scalar))
def test_difficulty_takes_easy_and_medium_only(d):
    got = code_of(body(json.dumps({**GOOD, "difficulty": d})))
    if d in ("easy", "medium"):
        assert not isinstance(got, str) and got.difficulty == d
    else:
        assert got == "TASK_DIFFICULTY_NOT_TAKEN"


# 6. summary: whitespace collapsed, cut at 400, idempotent.
@SETTINGS
@seed(20261102)
@given(s=st.text(alphabet=st.sampled_from("ab \t\n　"), max_size=900))
@example(s="a" * 450)
@example(s="ab " * 200)
def test_summary_is_collapsed_cut_at_400_and_stable(s):
    got = parse_task_block(body(json.dumps({**GOOD, "summary": s})))
    assert got.summary == " ".join(s.split())[:400]
    assert len(got.summary) <= 400
    again = parse_task_block(body(json.dumps({**GOOD, "summary": got.summary})))
    assert again.summary == got.summary


# 7. block_sha256 is the sha256 of the exact block text: prose around the block never
#    changes it; any change inside the block (even whitespace or key order) does.
@SETTINGS
@seed(20261102)
@given(
    before=st.text(alphabet=st.characters(blacklist_characters="`"), max_size=30),
    after=st.text(alphabet=st.characters(blacklist_characters="`"), max_size=30),
    pad=st.sampled_from(["", " ", "  ", "\t"]),
    indent=st.sampled_from([None, 1, 2]),
)
def test_block_hash_is_the_exact_block_bytes(before, after, pad, indent):
    text = json.dumps(GOOD, indent=indent) + pad
    got = parse_task_block(body(text, before=before + "\n", after="\n" + after))
    assert got.block_sha256 == hashlib.sha256(text.encode()).hexdigest()


def test_reordered_keys_are_another_block_pinning_current_behaviour():
    a = parse_task_block(body(json.dumps(GOOD)))
    b = parse_task_block(body(json.dumps(dict(reversed(list(GOOD.items()))))))
    assert a.block_sha256 != b.block_sha256


# 8. roots are threaded to the verifier check.
def test_configured_roots_reach_the_verifier_check():
    block = {**GOOD, "verification": ["python3 -m pytest pkg/tests/test_x.py"]}
    assert code_of(body(json.dumps(block))) == "TASK_VERIFICATION_UNSUPPORTED"
    assert parse_task_block(body(json.dumps(block)), ("pkg/tests",)).verifiers


def test_an_empty_verification_list_is_refused():
    assert code_of(body(json.dumps({**GOOD, "verification": []}))) == "TASK_VERIFICATION_INVALID"


def test_an_invalid_json_block_is_refused_as_json_invalid():
    assert code_of("```eia-task\n{\n```") == "TASK_BLOCK_JSON_INVALID"


@pytest.mark.parametrize("value", [[], ["difficulty"], "x", 7, None])
def test_a_block_that_is_not_an_object_is_refused_as_keys_invalid(value):
    assert code_of(body(json.dumps(value))) == "TASK_BLOCK_KEYS_INVALID"


def test_a_non_string_summary_is_refused():
    assert code_of(body(json.dumps({**GOOD, "summary": 7}))) == "TASK_SUMMARY_INVALID"


def test_a_medium_block_is_taken():
    task = parse_task_block(body(json.dumps({**GOOD, "difficulty": "medium"})))
    assert task.difficulty == "medium"
