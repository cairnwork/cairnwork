"""Classify verifier setup denials reproduced on the unchanged tree (#962 step 2b).

This is verify-only evidence. The independent control run is never an observation for Core;
it can only stop notarization with UNCOMPARABLE, and every uncertain shape fails closed.

Accepted residual (AC13, N12): when a protected fixture calls candidate code before its denied
write (an assert before the write, or a branch on the result), a wrong candidate can be
UNCOMPARABLE: unintentionally when it behaves like the base there (SX), or on purpose by
re-running the fixture's own code (SBa). It is never VERIFIED: Core is not asked, and nothing
downstream reads UNCOMPARABLE as a pass.
"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath

from cairnwork.account_prelude import after_candidate_ran, busy_once_more
from cairnwork.deferred_tests import BASE_COMPARE_RULE, BaseUncomparable, Comparison
from cairnwork.gitcmd import run_git_bytes
from cairnwork.verifiers import DEFAULT_TIMEOUT_SECONDS, run_verifiers

RULE = "candidate-env-v1"
CODE = "CANDIDATE_ENV_DENIED"


def uncomparable_rule(code: str) -> str:
    """Keep the JSON rule identifier distinct from base-run comparisons."""
    return RULE if code == CODE else BASE_COMPARE_RULE


def control_tree(task) -> str:
    """The tests commit is the unchanged tree when supplied; otherwise use the base."""
    return task.test_commit or task.base_commit


def run_control(task, specs, ctx, result):
    """Run the same specs on the control tree, using the already-used isolated account."""
    return busy_once_more(
        after_candidate_ran,
        run_verifiers,
        ctx,
        control_tree(task),
        specs,
        f"{result.attempt_id}-c",
        timeout=DEFAULT_TIMEOUT_SECONDS,
        deny=True,
    )


def classify(candidate_runs, control_runs, observations, task) -> bool:
    """Return true only when R0b, R1, R2 and AC7 all hold for every failed observation."""
    failed = [obs for obs in observations if not obs.passed]
    verifier_observations = {id(run.observation) for run in candidate_runs}
    if any(id(obs) not in verifier_observations for obs in failed):
        return False
    bad_runs = [run for run in candidate_runs if not run.observation.passed]
    if not bad_runs or not all(_clean(run, failure=True) for run in bad_runs):
        return False
    if not all(_clean(run) for run in control_runs):
        return False
    control_by_id = {run.spec.verifier_id: run for run in control_runs}
    for candidate in bad_runs:
        nodes = [node for node, outcome in candidate.outcomes.items() if outcome != "passed"]
        if not nodes or set(nodes) != set(candidate.setup_denied):
            return False
        control = control_by_id.get(candidate.spec.verifier_id)
        if control is None:
            return False
        for node in nodes:
            cand = candidate.setup_denied.get(node)
            base = control.setup_denied.get(node)
            if not _same_setup_denial(candidate, control, node, cand, base):
                return False
            if cand[0] != base[0] or (cand[1], cand[2]) != (base[1], base[2]):
                return False
            if not _literal_test_writer(task, control_tree(task), base):
                return False
    return True


def _clean(run, *, failure: bool = False) -> bool:
    """The R0b plain-failure and step-1 cleanliness conditions for one run."""
    return (
        (not failure or run.plain_failure)
        and run.recorded
        and run.collection_finished
        and not run.timed_out
        and not run.mutated
        and not run.survivors
        and not run.forged
        and not run.log_too_big
        and not run.report_too_big
        and not run.data_refused
        and not run.not_run
        and not run.renamed
        and not run.teardown_errors
        and not run.collect_skipped
        and "disagrees with the recorded session status" not in run.why
    )


def _same_setup_denial(candidate, control, node, cand, base) -> bool:
    """Require a setup failure and trusted, well-shaped records on both trees."""
    return bool(
        cand is not None
        and base is not None
        and candidate.fail_kind.get(node) == "setup"
        and control.fail_kind.get(node) == "setup"
        and candidate.outcomes.get(node) == "failed"
        and control.outcomes.get(node) == "failed"
        and type(cand) is tuple
        and len(cand) == 3
        and type(base) is tuple
        and len(base) == 3
        and type(cand[0]) is str
        and type(base[0]) is str
        and type(cand[1]) is str
        and type(base[1]) is str
        and type(cand[2]) is int
        and type(base[2]) is int
        and cand[0].startswith("/")
        and base[0].startswith("/")
        and cand[1]
        and base[1]
        and cand[2] > 0
        and base[2] > 0
    )


def _literal_test_writer(task, commit: str, record: tuple[str, str, int]) -> bool:
    """R2: the control tree's recorded source line directly writes the denied literal path."""
    denied_path, origin, line = record
    allowed = set(task.allowed_files)
    if origin in allowed or not _test_origin(origin, task.test_roots):
        return False
    try:
        source = run_git_bytes(task.repo, "show", f"{commit}:{origin}").decode("utf-8")
        tree = ast.parse(source)
    except Exception:  # noqa: BLE001 - malformed or unreadable control source fails closed.
        return False
    statements = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and getattr(node, "lineno", 0) == line
        and line <= getattr(node, "end_lineno", 0)
    ]
    if len(statements) != 1:
        return False
    return _direct_writer(statements[0], line, denied_path)


def _test_origin(origin: str, roots: tuple[str, ...]) -> bool:
    """Only verifier test roots and conftest inputs can establish R2."""
    path = PurePosixPath(origin)
    if path.is_absolute() or ".." in path.parts:
        return False
    return path.name == "conftest.py" or any(
        origin == root or origin.startswith(root.rstrip("/") + "/") for root in roots
    )


def _direct_writer(statement, line: int, denied: str) -> bool:
    """Accept only one-line Expr, Assign value or With item writer calls (R2)."""
    if statement.lineno != line:
        return False
    call = None
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        call = statement.value
    elif isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
        call = statement.value
    elif isinstance(statement, ast.With) and len(statement.items) == 1:
        value = statement.items[0].context_expr
        call = value if isinstance(value, ast.Call) else None
    return call is not None and _writer_call(call, denied)


def _writer_call(call: ast.Call, denied: str) -> bool:
    """Recognize the accepted literal-path writer forms; dynamic paths never qualify."""
    name = _dotted(call.func)
    args = call.args
    if name in {"open", "io.open", "os.open", "os.mkdir", "os.makedirs", "logging.FileHandler"}:
        if not args or not _literal(args[0], denied):
            return False
        if name in {"open", "io.open"}:
            mode = _kw_or_pos(call, "mode", 1, "r")
            return (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and any(flag in mode.value for flag in "wax+")
            )
        if name == "os.open":
            return len(args) > 1 and _write_flags(args[1])
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr in {
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
        "open",
    }:
        receiver = call.func.value
        if isinstance(receiver, ast.Call) and _dotted(receiver.func) in {"Path", "pathlib.Path"}:
            if receiver.args and _literal(receiver.args[0], denied):
                return call.func.attr != "open" or _mode_writes(_kw_or_pos(call, "mode", 0, "r"))
    return False


def _literal(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is str and node.value == value


def _dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _kw_or_pos(call, name: str, position: int, default):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[position] if len(call.args) > position else default


def _mode_writes(mode) -> bool:
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(flag in mode.value for flag in "wax+")
    )


def _write_flags(node) -> bool:
    """Check literal os flags for create, truncate, append, write or read/write."""
    names = {_dotted(part) for part in ast.walk(node)}
    return any(
        name.rsplit(".", 1)[-1] in {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}
        for name in names
    )


def decide(task, verifier_runs, observations, ctx, result) -> None:
    """Raise UNCOMPARABLE only after a control run of the failed specs qualifies (R0: account
    mode only; R3: any error building or running it keeps the step-1 FAILED reason)."""
    failed = tuple(run.spec for run in verifier_runs if not run.observation.passed)
    if ctx.account is None or not failed:
        return
    try:
        controls = run_control(task, failed, ctx, result)
        qualified = classify(verifier_runs, controls, observations, task)
    except Exception as exc:  # noqa: BLE001 - R3 preserves FAILED on any control-run error.
        result.logs.append(f"candidate environment control run failed: {type(exc).__name__}")
        return
    if qualified:
        detail = "test setup is denied the same way on the unchanged tree"
        raise BaseUncomparable(Comparison("UNCOMPARABLE", CODE, detail))
