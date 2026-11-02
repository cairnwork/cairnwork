"""Property tests pinning issue_task.settle_check, the re-check before settle opens a PR (#624).

A mutation sweep (2026-11-02) found checks that no test pinned: which label settle asks trust
about and what happens when GitHub fails. settle_check asks trust(n, "cairnwork:queue") first
(an untrusted body is never parsed), then reads the current body; a body without exactly one
eia-task block, or whose block text no longer hashes to the bound block, counts as changed
(ISSUE_CHANGED_AFTER_TAKE). The block is not parsed again with the configured test roots, so a
roots change after take does not count as an edit (#649). A refusal moves the Issue to
needs-fix with exactly one comment and raises RuntimeError. A GitHub error is not a changed
Issue: it propagates, with no relabel and no comment.

Pure fakes (no git, no clone): a GitHub that records every call and answers trust() per label,
so a wrong label is visible. test_issue_settle_recheck.py uses a FakeGitHub whose trust()
ignores the label.
"""

import json

import pytest
from hypothesis import HealthCheck, example, given, seed, settings
from hypothesis import strategies as st

from cairnwork.issue_task import IssueRefused, parse_task_block, settle_check

LABELS = ("cairnwork:queue", "cairnwork:running", "cairnwork:needs-fix")
SETTINGS = settings(
    max_examples=200, deadline=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)  # fmt: skip


def block(cmd="python3 -m pytest -q tests/test_calc.py", files=("src/calc.py",)):
    raw = {"difficulty": "easy", "allowed_files": list(files), "verification": [cmd],
           "capability_ids": ["x"]}  # fmt: skip
    return f"Bug\n\n```eia-task\n{json.dumps(raw)}\n```\n"


class GitHub:
    """Records every call; trust() answers per label, so asking about the wrong label shows."""

    def __init__(self, body, trust_by_label=None, current_error=None):
        self.body = body
        self.trust_by_label = trust_by_label or {}
        self.current_error = current_error
        self.calls = []

    def current(self, number):
        self.calls.append(("current", number))
        if self.current_error:
            raise self.current_error
        return {"title": "t", "body": self.body}

    def trust(self, number, label):
        self.calls.append(("trust", number, label))
        return self.trust_by_label.get(label, "")

    def relabel(self, number, add, remove):
        self.calls.append(("relabel", number, add, remove))

    def comment(self, number, text):
        self.calls.append(("comment", number, text))


def run(github, known):
    try:
        settle_check(github, 7, known, LABELS)
    except RuntimeError as exc:
        return str(exc)
    return ""


def moves_of(github, kind):
    return [c for c in github.calls if c[0] == kind]


trust_reason = st.sampled_from(
    ["", "BODY_EDITED_BY_UNTRUSTED", "AUTHOR_UNTRUSTED", "QUEUE_LABEL_EVENT_MISSING"]
)
bodies = st.sampled_from(
    [block(), block(files=("src/calc.py", "src/other.py")), "no block", "", None,
     block() + block(), block(cmd="sh -c true")]
)  # fmt: skip


# Reference: an untrusted queue event is the reason and the body is not even read; else pass
# iff the current body parses to the bound block; on refusal exactly one relabel
# running -> needs-fix and one comment naming the reason and the queue label.
@SETTINGS
@seed(20261102)
@given(
    current=bodies,
    bound=st.sampled_from([block(), block(files=("src/x.py",))]),
    reason=trust_reason,
)
@example(current=block(), bound=block(), reason="")
def test_settle_check_matches_the_reference(current, bound, reason):
    known = {"task_id": "t", "block_sha256": parse_task_block(bound).block_sha256}
    github = GitHub(current, {LABELS[0]: reason})
    try:
        same = parse_task_block(current or "").block_sha256 == known["block_sha256"]
    except IssueRefused:
        same = False
    want = reason or ("" if same else "ISSUE_CHANGED_AFTER_TAKE")
    assert run(github, known) == want
    if reason:
        assert moves_of(github, "current") == []
    if want:
        assert moves_of(github, "relabel") == [("relabel", 7, LABELS[2], LABELS[1])]
        comments = moves_of(github, "comment")
        assert len(comments) == 1 and want in comments[0][2]
        assert LABELS[0] in comments[0][2] and LABELS[2] in comments[0][2]
    else:
        assert moves_of(github, "relabel") == [] and moves_of(github, "comment") == []


def test_trust_is_asked_about_the_queue_label():
    """settle must re-check the queue label event, the one take trusted; the running label was
    added by the bot itself, so asking about it would trust any edit made before it."""
    known = {"block_sha256": parse_task_block(block()).block_sha256}
    github = GitHub(block(), {LABELS[1]: "", LABELS[0]: "BODY_EDITED_BY_UNTRUSTED"})
    assert run(github, known) == "BODY_EDITED_BY_UNTRUSTED"
    assert ("trust", 7, LABELS[0]) in github.calls
    assert not [c for c in github.calls if c[0] == "trust" and c[2] != LABELS[0]]


def test_the_configured_test_roots_are_used():
    """An Issue whose verifier sits under a configured root (pkg/tests) still gets its PR."""
    text = block(cmd="python3 -m pytest -q pkg/tests/test_calc.py")
    known = {"block_sha256": parse_task_block(text, ("pkg/tests",)).block_sha256}
    assert run(GitHub(text), known) == ""


def test_a_test_roots_change_after_take_is_not_an_edit():
    """#649: take read the block with the root pkg/tests, which the default roots no longer
    allow. The block text is unchanged, so settle still opens the PR."""
    text = block(cmd="python3 -m pytest -q pkg/tests/test_calc.py")
    known = {"block_sha256": parse_task_block(text, ("pkg/tests",)).block_sha256}
    with pytest.raises(IssueRefused):
        parse_task_block(text)  # the roots now in force would refuse a fresh take
    github = GitHub(text)
    assert run(github, known) == ""
    assert not moves_of(github, "relabel") and not moves_of(github, "comment")


def test_a_foreign_contract_fence_added_after_take_is_a_changed_issue():
    """The eia-task block text is unchanged, but a ```*-contract fence now sits beside it (a
    body take refuses): settle counts the Issue as changed and opens no PR."""
    known = {"block_sha256": parse_task_block(block()).block_sha256}
    github = GitHub(block() + "\n```nexus-contract\n{}\n```\n")
    assert run(github, known) == "ISSUE_CHANGED_AFTER_TAKE"
    assert len(moves_of(github, "comment")) == 1


@pytest.mark.parametrize("error", [RuntimeError("gh: HTTP 502"), OSError("timeout")])
def test_a_github_read_failure_propagates_without_relabel_or_comment(error):
    """A failed read of the Issue is GitHub trouble, retried next pass; it must not move the
    Issue to needs-fix or post a comment."""
    known = {"block_sha256": parse_task_block(block()).block_sha256}
    github = GitHub(block(), current_error=error)
    with pytest.raises(type(error)) as caught:
        settle_check(github, 7, known, LABELS)
    assert caught.value is error
    assert not moves_of(github, "relabel") and not moves_of(github, "comment")


def test_an_old_record_without_a_block_hash_is_a_changed_issue():
    """A record missing block_sha256 is refused with ISSUE_CHANGED_AFTER_TAKE, not KeyError."""
    assert run(GitHub(block()), {"task_id": "t"}) == "ISSUE_CHANGED_AFTER_TAKE"


@pytest.mark.parametrize("text", ["no block", "```eia-task\n{\n```", "```eia-task\n[]\n```"])
def test_a_body_that_no_longer_parses_is_a_changed_issue(text):
    known = {"block_sha256": parse_task_block(block()).block_sha256}
    github = GitHub(text)
    assert run(github, known) == "ISSUE_CHANGED_AFTER_TAKE"
    assert len(moves_of(github, "comment")) == 1
