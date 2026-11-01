import json

import pytest
from test_issue_runner import FakeGitHub, issue
from test_issue_runner import setup as setup  # noqa: F401 - fixture

import cairnwork.issue_runner as runner
import cairnwork.issue_task as issue_task
from cairnwork.issue_task import IssueRefused, parse_task_block, queue_trusted

GOOD = {
    "difficulty": "easy",
    "allowed_files": ["src/stats.py"],
    "verification": ["python3 -m pytest -q tests/test_stats.py"],
    "capability_ids": ["x"],
    "summary": "fix   average",
}


def body(block=GOOD, extra=""):
    return f"Bug text\n\n```eia-task\n{json.dumps(block)}\n```\n{extra}"


def test_a_good_block_parses():
    task = parse_task_block(body())
    assert task.allowed_files == ("src/stats.py",)
    assert task.verifiers == (("python3", "-m", "pytest", "-q", "tests/test_stats.py"),)
    assert task.summary == "fix average" and len(task.block_sha256) == 64


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("no block", "TASK_BLOCK_REQUIRED"),
        (body() + body(), "TASK_BLOCK_REQUIRED"),
        (body({**GOOD, "extra": 1}), "TASK_BLOCK_KEYS_INVALID"),
        (body({**GOOD, "difficulty": "hard"}), "TASK_DIFFICULTY_NOT_TAKEN"),
        (body({**GOOD, "allowed_files": []}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": ["../x.py"]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": ["a/.hidden/x.py"]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": [["src/calc.py"]]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": [{"p": 1}]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": [7]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": ["src/calc.py", ["x"]]}), "TASK_ALLOWED_FILES_INVALID"),
        (body({**GOOD, "allowed_files": ["pyproject.toml"]}), "TASK_ALLOWED_FILES_PROTECTED"),
        (body({**GOOD, "allowed_files": ["src/conftest.py"]}), "TASK_ALLOWED_FILES_PROTECTED"),
        (body({**GOOD, "allowed_files": [".github/x.yml"]}), "TASK_ALLOWED_FILES_PROTECTED"),
        (body({**GOOD, "allowed_files": ["Tasks/card.md"]}), "TASK_ALLOWED_FILES_PROTECTED"),
        (
            body({**GOOD, "verification": ["sh -c 'git diff --check a b'"]}),
            "TASK_VERIFICATION_UNSUPPORTED",
        ),
        (
            body({**GOOD, "verification": ["python3 -m pytest -c x.ini tests/test_a.py"]}),
            "TASK_VERIFICATION_UNSUPPORTED",
        ),
        (body({**GOOD, "verification": ["python3 -m pytest -q"]}), "TASK_VERIFICATION_UNSUPPORTED"),
        (body(extra="```eia-contract\n{}\n```"), "TASK_BODY_HAS_FOREIGN_CONTRACT"),
        (body({**GOOD, "capability_ids": []}), "TASK_CAPABILITY_IDS_INVALID"),
        (body({**GOOD, "test_commit": "abc"}), "TASK_TEST_COMMIT_INVALID"),
        (body({**GOOD, "test_commit": 7}), "TASK_TEST_COMMIT_INVALID"),
    ],
)
def test_refused_blocks(text, code):
    with pytest.raises(IssueRefused) as caught:
        parse_task_block(text)
    assert caught.value.code == code


USER = {"login": "James3014", "__typename": "User"}
BOT = {"login": "james3014-nexus-agent", "__typename": "Bot"}
STRANGER = {"login": "someone", "__typename": "User"}
LABELED = {"event": "labeled", "label": "q", "actor": "James3014", "at": "2026-10-20T07:00:00Z"}


def trust(events=(LABELED,), author=USER, nodes=(), total=None):
    edits = {"nodes": list(nodes), "totalCount": len(nodes) if total is None else total}
    return queue_trusted(label="q", events=events, author=author, edits=edits)


def test_trusted_queue():
    assert trust() == ""
    assert trust(author=BOT) == ""
    early = {"editedAt": "2026-10-20T06:00:00Z", "editor": STRANGER}
    assert trust(nodes=[early]) == ""  # an edit before the label was reviewed by the labeler


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"events": ()}, "QUEUE_LABEL_EVENT_MISSING"),
        ({"events": ({**LABELED, "actor": "someone"},)}, "QUEUE_LABELED_BY_UNTRUSTED"),
        ({"author": STRANGER}, "AUTHOR_UNTRUSTED"),
        ({"author": {"login": "james3014-nexus-agent", "__typename": "User"}}, "AUTHOR_UNTRUSTED"),
        (
            {"nodes": [{"editedAt": "2026-10-20T08:00:00Z", "editor": STRANGER}]},
            "BODY_EDITED_BY_UNTRUSTED",
        ),
        ({"total": 5}, "EDIT_HISTORY_INCOMPLETE"),
        (
            {"nodes": [{"editedAt": "2026-10-20T07:00:00Z", "editor": STRANGER}]},
            "BODY_EDITED_BY_UNTRUSTED",
        ),
        ({"events": ({**LABELED, "at": "garbage"},)}, "QUEUE_LABEL_TIME_UNREADABLE"),
        (
            {"nodes": [{"editedAt": "not-a-time", "editor": STRANGER}]},
            "BODY_EDITED_BY_UNTRUSTED",
        ),
        (
            {
                "events": (
                    LABELED,
                    {"event": "renamed", "actor": "someone", "at": "2026-10-20T09:00:00Z"},
                )
            },
            "TITLE_EDITED_BY_UNTRUSTED",
        ),
        (  # same second as the label counts as after it
            {
                "events": (
                    LABELED,
                    {"event": "renamed", "actor": "someone", "at": "2026-10-20T07:00:00Z"},
                )
            },
            "TITLE_EDITED_BY_UNTRUSTED",
        ),
    ],
)
def test_untrusted_queue(kwargs, reason):
    assert trust(**kwargs) == reason


def test_the_latest_label_event_counts():
    relabeled = {**LABELED, "actor": "someone", "at": "2026-10-20T10:00:00Z"}
    assert trust(events=(LABELED, relabeled)) == "QUEUE_LABELED_BY_UNTRUSTED"


def test_a_test_commit_is_carried():
    assert parse_task_block(body({**GOOD, "test_commit": "a" * 40})).test_commit == "a" * 40


def test_a_block_nested_too_deep_to_decode_is_refused():
    """#650: json.loads raises RecursionError, not ValueError, for deep nesting (3.11 to 3.14)."""
    deep = "[" * 100_000 + "]" * 100_000
    text = f"Bug text\n\n```eia-task\n{deep}\n```\n"
    with pytest.raises(IssueRefused) as refused:
        parse_task_block(text)
    assert refused.value.code == "TASK_BLOCK_JSON_INVALID"


def test_take_refuses_a_block_the_decoder_cannot_nest(setup, monkeypatch):  # noqa: F811
    """#650: a RecursionError in the decoder is a refusal (needs-fix), not a retry every pass."""

    def deep(_text, *a, **k):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(issue_task.json, "loads", deep)
    github = FakeGitHub([issue()])
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out[0]["refused"] == "TASK_BLOCK_JSON_INVALID", out
    assert github.labels[7] == {runner.NEEDS_FIX}
