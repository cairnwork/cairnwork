"""Every account probe in src/ and scripts/ runs under the account lock (Issue #560).

``VerifierAccount.available()`` starts a process as the shared account, and a probe that times
out ends everything the account runs. Only a caller that holds ``exclusive()`` may do that, or it
ends another run's processes. These tests read the source and drive ``locked_available`` with a
stand-in account: no sudo, nothing runs as the account.
"""

import ast
import contextlib
from pathlib import Path
from typing import Any

from cairnwork import account_check
from cairnwork.verifier_account import AccountBusy, VerifierAccountError

REPO = Path(__file__).resolve().parents[1]


def _probes_outside_the_lock(tree: ast.AST) -> tuple[int, list[int]]:
    """(probes seen, lines of those not inside a ``with <...>.exclusive(...)`` block)."""
    seen, outside = 0, []

    def visit(node: ast.AST, locked: bool) -> None:
        nonlocal seen
        if isinstance(node, ast.With | ast.AsyncWith) and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "exclusive"
            for item in node.items
        ):
            locked = True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "available"
            and "account" in ast.unparse(node.func.value).lower()
        ):
            seen += 1
            if not locked:
                outside.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, locked)

    visit(tree, False)
    return seen, outside


def test_every_account_probe_in_src_and_scripts_holds_the_lock():
    seen, outside = 0, []
    for path in sorted([*(REPO / "src").rglob("*.py"), *(REPO / "scripts").rglob("*.py")]):
        count, lines = _probes_outside_the_lock(ast.parse(path.read_text(), str(path)))
        seen += count
        outside += [f"{path.relative_to(REPO)}:{line}" for line in lines]
    assert outside == []
    assert seen >= 3  # run_verifiers, blackbox_exec, account_check: the check is not vacuous


def test_the_check_flags_a_probe_outside_the_lock():
    source = (
        "ACCOUNT = VerifierAccount() if VerifierAccount().available() else None\n"
        "with account.exclusive(5):\n    account.available(py)\n"
    )
    assert _probes_outside_the_lock(ast.parse(source)) == (2, [1])


class _Account:  # stands in for VerifierAccount
    def __init__(self, events, busy=None, usable=True):
        self.events, self.busy, self.usable = events, busy, usable

    @contextlib.contextmanager
    def exclusive(self, timeout=None):
        if self.busy is not None:
            raise self.busy
        self.events.append(("lock", timeout))
        try:
            yield
        finally:
            self.events.append("unlock")

    def available(self, python=None):
        self.events.append("probe")
        return self.usable


def _stub(events, **kw) -> Any:
    return _Account(events, **kw)


def test_locked_available_probes_inside_the_lock():
    events = []
    assert account_check.locked_available(_stub(events), 7.0) is True
    assert events == [("lock", 7.0), "probe", "unlock"]


def test_locked_available_reports_an_unusable_account():
    events = []
    assert account_check.locked_available(_stub(events, usable=False)) is False
    assert events == [("lock", None), "probe", "unlock"]


def test_a_busy_or_broken_lock_is_unusable_and_starts_nothing():
    for error in (AccountBusy("busy"), VerifierAccountError("queue"), OSError("no root")):
        events = []
        assert account_check.locked_available(_stub(events, busy=error)) is False
        assert events == []
