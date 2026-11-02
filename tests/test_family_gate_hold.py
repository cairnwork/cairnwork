"""#646: a family-gate hold stopped between two GitHub calls never leaves the Issue to take.

Order: journal, queue label off, hold label on, comment, journal cleared. A stop before the
first label edit leaves the Issue as the gate found it (queued, nothing says held); after it, no
``take`` lists the Issue, gate on or off, and the next gate pass finishes the hold.
"""

import json

import pytest
from test_family_gate import Hub, annotated, gate_pass
from test_issue_runner import issue, setup  # noqa: F401  (fixture)

import cairnwork.issue_runner as runner
from experimental.family_gate import __main__ as cli
from experimental.family_gate.families import PASS
from experimental.family_gate.gate import HOLD, Sidecar

SAME = {"issue_author": "opus"}
WORKER = "claude-opus-5-5"
STEPS = ("unqueue", "hold", "comment", "journal")


class Stopping(Hub):
    """Fails Issue 7's hold at one step, as a pass killed there or a GitHub 502 would stop it."""

    def __init__(self, issues, stop=""):
        super().__init__(issues)
        self.stop, self.edits = stop, []

    def _run(self, *args, stdin=None):
        if args[1] == "view":
            return super()._run(*args, stdin=stdin)
        number, step = (
            int(args[2]),
            {
                ("--remove-label", runner.QUEUE): "unqueue",
                ("--add-label", HOLD): "hold",
            }.get((args[5], args[6]), ""),
        )
        self.edits.append((number, args[5], args[6]))
        if number == 7 and step and step == self.stop:
            raise RuntimeError("gh issue failed: 502")
        return super()._run(*args, stdin=stdin)

    def comment(self, number, text):
        if number == 7 and self.stop == "comment":
            raise RuntimeError("gh issue comment failed: 502")
        super().comment(number, text)


def told(github):
    return [t for n, t in github.comments if n == 7 and "家族閘門" in t]


@pytest.fixture
def journal_breaks(monkeypatch):
    """While set, the journal write that drops a finished hold fails."""
    broken, real = [True], Sidecar.set_holds

    def set_holds(self, holds):
        if broken and not holds:
            raise OSError("No space left on device")
        real(self, holds)

    monkeypatch.setattr(Sidecar, "set_holds", set_holds)
    return broken


@pytest.mark.parametrize("stop", STEPS)
def test_a_stopped_hold_is_never_taken_and_the_next_pass_finishes_it(
    setup,  # noqa: F811
    tmp_path,
    request,
    stop,
):
    broken = request.getfixturevalue("journal_breaks") if stop == "journal" else []
    github = Stopping([annotated(families=SAME), issue(8)], "" if stop == "journal" else stop)
    result, _, sidecar = gate_pass(setup, github, tmp_path, WORKER)
    assert result["gate"]["refused"] == [7] and result["gate"]["errors"]
    assert [r["issue"] for r in result["take"]] == [8] and len(setup["started"]) == 1
    if runner.QUEUE in github.labels[7]:  # stopped before any change: as the gate found it
        assert stop == "unqueue" and github.labels[7] == {runner.QUEUE} and told(github) == []
    else:  # the gate turned off now: the plain runner does not list it
        plain = cli.one_pass(setup["intake"], github, setup["clone"], None)
        assert plain["take"] == [] and len(setup["started"]) == 1
    assert set(sidecar.holds()) == {7}  # the journal still names the unfinished hold
    github.stop = ""
    broken.clear()
    result, _, _ = gate_pass(setup, github, tmp_path, WORKER)
    assert "errors" not in result["gate"] and result["take"] == []
    assert github.labels[7] == {HOLD} and sidecar.holds() == {} and len(setup["started"]) == 1
    assert len(told(github)) == (2 if stop == "journal" else 1)  # only a lost journal repeats
    edits = [e for e in github.edits if e[0] == 7]
    result, _, _ = gate_pass(setup, github, tmp_path, WORKER)  # converged: nothing more to do
    assert "errors" not in result["gate"] and result["gate"]["refused"] == []
    assert github.labels[7] == {HOLD} and len(told(github)) == (2 if stop == "journal" else 1)
    assert [e for e in github.edits if e[0] == 7] == edits


def test_the_queue_label_comes_off_before_anything_says_held(setup, tmp_path):  # noqa: F811
    order = []

    class Watching(Stopping):
        def _run(self, *args, stdin=None):
            order.append((args[5], args[6]))
            return super()._run(*args, stdin=stdin)

        def comment(self, number, text):
            order.append(("comment", runner.QUEUE in self.labels[number]))
            super().comment(number, text)

    github = Watching([annotated(families=SAME)])
    gate_pass(setup, github, tmp_path, WORKER)
    assert order == [("--remove-label", runner.QUEUE), ("--add-label", HOLD), ("comment", False)]


def test_an_unwritable_journal_changes_nothing_on_github(setup, tmp_path, monkeypatch):  # noqa: F811
    def refuse(self, holds):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Sidecar, "set_holds", refuse)
    github = Stopping([annotated(families=SAME), issue(8)])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    result = cli.one_pass(setup["intake"], github, setup["clone"], {"worker_model": WORKER,
                                                                   "sidecar": sidecar})  # fmt: skip
    assert result["gate"]["error"].startswith("GATE_NOT_RUN: OSError")
    assert result["take"] == [] and setup["started"] == []
    assert github.edits == [] and github.comments == [] and github.labels[7] == {runner.QUEUE}


@pytest.mark.parametrize("text", ["[]", '{"7": 3}', '{"x": "t"}', "{not json"])
def test_an_unreadable_journal_stops_the_gate(setup, tmp_path, text):  # noqa: F811
    github = Stopping([annotated(families=SAME)])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    sidecar.dir.mkdir(parents=True)
    sidecar.holds_path.write_text(text)
    result = cli.one_pass(setup["intake"], github, setup["clone"], {"worker_model": WORKER,
                                                                   "sidecar": sidecar})  # fmt: skip
    assert result["gate"]["error"].startswith("GATE_NOT_RUN") and result["take"] == []
    assert github.edits == [] and github.comments == [] and github.labels[7] == {runner.QUEUE}


def test_a_held_issue_queued_again_is_decided_again(setup, tmp_path):  # noqa: F811
    github = Stopping([annotated(families={"issue_author": "gpt-5.4"})])
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    sidecar.set_holds({7: "an older refusal, family since changed"})
    result, rows, _ = gate_pass(setup, github, tmp_path, WORKER)
    assert [r["issue"] for r in result["take"]] == [7] and rows[0]["verdict"] == PASS
    assert github.labels[7] == {runner.RUNNING} and told(github) == [] and sidecar.holds() == {}
    assert json.loads(sidecar.holds_path.read_text()) == {}
    assert [e for e in github.edits if e[2] == HOLD] == []  # the old hold is not finished


@pytest.mark.parametrize("meanwhile", ("taken", "queued past the listing", "closed"))
def test_a_journaled_hold_is_not_finished_on_an_issue_that_moved_on(
    setup,  # noqa: F811
    tmp_path,
    meanwhile,
):
    github = Stopping([annotated(families=SAME)], "unqueue")  # journaled, nothing else done
    result, _, sidecar = gate_pass(setup, github, tmp_path, WORKER)
    assert set(sidecar.holds()) == {7} and github.labels[7] == {runner.QUEUE}
    github.stop = ""
    if meanwhile == "taken":  # a pass with the gate off takes it
        plain = cli.one_pass(setup["intake"], github, setup["clone"], None)
        assert [r["issue"] for r in plain["take"]] == [7]
    elif meanwhile == "closed":
        github.labels[7] = set()
        github.closed = {7}
    else:  # still queued, but not in the gate's listing (it lists at most 50)
        github.issues = lambda label: []
    labels = set(github.labels[7])
    result, _, _ = gate_pass(setup, github, tmp_path, WORKER)
    assert "errors" not in result["gate"] and sidecar.holds() == {}
    assert github.labels[7] == labels and HOLD not in labels and told(github) == []


def test_a_hold_whose_issue_github_cannot_place_waits(setup, tmp_path):  # noqa: F811
    github = Stopping([annotated(families=SAME)], "hold")  # out of the queue, not yet held
    gate_pass(setup, github, tmp_path, WORKER)
    github.stop, real = "", github._run

    def view_fails(*args, stdin=None):
        if args[1] == "view":
            raise RuntimeError("gh issue view failed: 502")
        return real(*args, stdin=stdin)

    github._run = view_fails
    result, _, sidecar = gate_pass(setup, github, tmp_path, WORKER)
    assert result["gate"]["errors"] == [{"issue": 7, "error": "HOLD_STATE_UNKNOWN"}]
    assert set(sidecar.holds()) == {7} and github.labels[7] == set() and told(github) == []
    github._run = real
    result, _, _ = gate_pass(setup, github, tmp_path, WORKER)
    assert "errors" not in result["gate"] and sidecar.holds() == {}
    assert github.labels[7] == {HOLD} and len(told(github)) == 1


def test_a_failed_hold_stays_journaled_when_a_later_one_finishes(setup, tmp_path):  # noqa: F811
    github = Stopping([annotated(families=SAME), annotated(8, families=SAME)], "hold")
    result, _, sidecar = gate_pass(setup, github, tmp_path, WORKER)
    assert [e["issue"] for e in result["gate"]["errors"]] == [7]
    assert github.labels[8] == {HOLD} and sidecar.holds() == {7: sidecar.holds()[7]}
    github.stop = ""
    gate_pass(setup, github, tmp_path, WORKER)
    assert github.labels[7] == {HOLD} and len(told(github)) == 1 and sidecar.holds() == {}


class Killed(BaseException):
    """The pass dies here (SIGKILL, power): nothing after this line of the gate runs."""


def test_each_finished_hold_leaves_the_journal_at_once(setup, tmp_path):  # noqa: F811
    class Dying(Stopping):
        def comment(self, number, text):
            if number == 7 and self.stop == "comment":
                raise Killed
            super().comment(number, text)

    github = Dying([annotated(6, families=SAME), annotated(families=SAME)], "comment")
    sidecar = Sidecar(tmp_path / "sidecar", github.repo)
    with pytest.raises(Killed):
        gate_pass(setup, github, tmp_path, WORKER)
    assert set(sidecar.holds()) == {7}  # 6 finished first: the next pass does not tell it again
    github.stop = ""
    gate_pass(setup, github, tmp_path, WORKER)
    assert [n for n, t in github.comments if "家族閘門" in t] == [6, 7]
    assert sidecar.holds() == {}
