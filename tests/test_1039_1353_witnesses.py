"""Measured Stage 2 witnesses for Issue #1353, using current Cairnwork entry points."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cairnwork import blackbox, blackbox_judge
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.verify_context import VerifyContext


def _declared(expected=True):
    return blackbox.parse(
        json.dumps(
            {
                "salt": "ab" * 32,
                "checks": [
                    {
                        "id": "add",
                        "module": "calc",
                        "function": "add",
                        "cases": [{"id": "one", "args": [2, 3], "expected": expected}],
                    }
                ],
            }
        ).encode()
    )


def _run(value=True, **changes):
    raw = json.dumps(
        {
            "checks": {
                "add": {
                    "file": "/co/calc.py",
                    "cases": {
                        "one": {"ok": value},
                    },
                }
            }
        }
    ).encode()
    run = BlackboxRun(raw, 0, False, 0, (), (), Path("/tmp/blackbox.log"), "/co", "p", True)
    return replace(run, **changes)


def _judge(run, declared=None):
    (verdict,) = blackbox_judge.judge_run(declared or _declared(), "c" * 40, run)
    return verdict.reason


def sandbox_available() -> bool:
    """Same probe as tests/conftest.py's skip marker for 'sandbox' tests."""
    import cairnwork_sandbox

    return cairnwork_sandbox.select() is not None and sys.platform == "darwin"


def _row(class_id, probe, route, observed, expected, classification, reason=""):
    reason = re.sub(r"/(?:private/)?(?:var/folders|tmp)/[^\s:]+", "<temporary-path>", reason)
    matches = observed == expected
    if expected == "NOT_RUN":
        matches = "VERIFICATION_INCOMPLETE: NO_SANDBOX" in observed
    elif expected == "PASS":
        matches = observed == "PASS" or ": PASS " in observed
    elif expected == "VERIFICATION_INCOMPLETE":
        matches = "VERIFICATION_INCOMPLETE:" in observed
    return {
        "class_id": class_id,
        "probe": probe,
        "route": route,
        "observed": observed,
        "expected_on_main": expected,
        "matches_spec": matches,
        "classification": classification,
        "platform": sys.platform,
        "reason": reason,
    }


def _simple_repo(root: Path, source: str, test_source: str):
    root = root.resolve()
    root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()

    git("init", "-q", "-b", "main", "--template=")
    git("config", "user.email", "probe@example.invalid")
    git("config", "user.name", "probe")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests/test_calc.py").write_text(test_source)
    (root / "tests/conftest.py").write_text(
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
    )
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (root / "src/calc.py").write_text(source)
    git("add", "-A")
    git("commit", "-q", "-m", "candidate")
    candidate = git("rev-parse", "HEAD")
    return base, candidate


def _verify(
    root: Path,
    state: Path,
    base: str,
    candidate: str,
    declaration: Path | None = None,
    *,
    strict=False,
):
    args = [
        sys.executable,
        "-I",
        "-m",
        "cairnwork.verify_cli",
        "--state",
        str(state),
        "run",
        "--repo-path",
        str(root),
        "--base",
        base,
        "--candidate",
        candidate,
        "--allowed-file",
        "src/calc.py",
        "--verifier",
        "python3 -m pytest -q tests/test_calc.py",
        "--import-path",
        "src",
    ]
    if declaration is not None:
        args += ["--blackbox", str(declaration)]
    if strict:
        args.append("--require-protection")
    done = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"status": "FAILED", "reason": done.stderr[-300:]}


def _same_process(root: Path, base: str, candidate: str):
    """Run main's committed-patch scans and pytest in this process on the candidate tree."""
    from cairnwork import candidate_sys_path
    from cairnwork.tamper_scan import scan_patch
    from cairnwork.workspace import CandidateRejected

    findings = scan_patch(root, base, candidate, python=sys.executable)
    if findings:
        return "REFUSED", ";".join(finding.rule for finding in findings)
    try:
        candidate_sys_path.refuse_new_sys_path_mutation(root, base, candidate, ("src/calc.py",))
    except CandidateRejected as exc:
        return "REFUSED", exc.code
    import os

    previous = Path.cwd()
    import conftest as saved_conftest

    sys.path.insert(0, str(root / "src"))
    try:
        os.chdir(root)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = pytest.main(["-q", "-p", "no:cacheprovider", "tests/test_calc.py"])
    finally:
        os.chdir(previous)
        sys.path.remove(str(root / "src"))
        for name in ("calc", "test_calc"):
            sys.modules.pop(name, None)
        if saved_conftest is not None:
            sys.modules["conftest"] = saved_conftest
    return ("VERIFIED" if result == pytest.ExitCode.OK else "FAILED"), str(result)


@cache
def run_baseline_probes():
    """Execute current verification entry points and return their unmodified outputs."""
    from tempfile import TemporaryDirectory

    rows = []
    evasions = {
        "C1": [
            "import importlib; importlib.import_module('_pyt' + 'est.reports')",
            "importlib.__dict__['import_module']('_py' + 'test.reports')",
        ],
        "C2": [
            "for target in (pytest.Item,): setattr(target, 'x', 1)",
            "map(setattr, (pytest.Item,), ('x',))",
        ],
        "C3": [
            "writer = shutil.copy; writer(src, dst)",
            "from sys import path as aliases; aliases.insert(0, extra)",
        ],
        "C4": [
            "importlib.util.spec_from_file_location(name, path)",
            "finder = type('F', (), {'find_spec': lambda *a: None})(); "
            "sys.meta_path.insert(0, finder)",
        ],
    }
    with TemporaryDirectory(prefix="cw1353-") as temp:
        parent = Path(temp).resolve()
        state = parent / "state"
        state.mkdir()
        state.chmod(0o700)
        (state / "config.json").write_text('{"verifier_account": false}')
        (state / "ACCEPTING").touch()
        declaration = parent / "declaration.json"
        declaration.write_text(
            json.dumps(
                {
                    "salt": "ab" * 32,
                    "checks": [
                        {
                            "id": "add",
                            "module": "calc",
                            "function": "add",
                            "cases": [{"id": "one", "args": [2, 3], "expected": 5}],
                        }
                    ],
                }
            )
        )
        declaration.chmod(0o600)
        for cid, variants in evasions.items():
            for i, evasion in enumerate(variants, 1):
                root = parent / f"{cid}-{i}"
                broken = "def add(a, b):\n    " + evasion + "\n    return a - b\n"
                base, cand = _simple_repo(
                    root,
                    broken,
                    "from calc import add\ndef test_deferred():\n    assert add(2, 3) == 5\n",
                )
                same, scan_reason = _same_process(root, base, cand)
                rows.append(
                    _row(
                        cid,
                        f"same-process-{i}",
                        "scan_patch→refuse_new_sys_path_mutation→pytest.main",
                        same,
                        "VERIFIED",
                        "VERIFIED_WRONG" if same == "VERIFIED" else "REFUSED",
                        scan_reason,
                    )
                )
                ctx = VerifyContext(root, parent / "runs", base_commit=base, import_paths=("src",))
                (parent / "runs").mkdir(exist_ok=True)
                verdicts, _ = blackbox_judge.judge_blackbox(
                    blackbox.load(declaration), ctx, cand, f"c{cid}-{i}"
                )
                bb = verdicts[0].reason
                rows.append(
                    _row(
                        cid,
                        f"blackbox-{i}",
                        "blackbox_judge.judge_blackbox→run_blackbox",
                        bb,
                        "NOT_RUN",
                        "NOT_RUN" if "VERIFICATION_INCOMPLETE" in bb else "REFUSED",
                        bb,
                    )
                )
        # Ten clean candidate trees use the same verifier route and preserve the raw main result.
        for i in range(10):
            root = parent / f"honest-{i}"
            base, cand = _simple_repo(
                root,
                "def add(a, b):\n    return a + b\n# measured fix\n",
                "from calc import add\ndef test_deferred():\n    assert add(2, 3) == 5\n",
            )
            status, detail = _same_process(root, base, cand)
            rows.append(
                _row(
                    "HONEST",
                    f"correct-{i + 1:02d}",
                    "scan_patch→refuse_new_sys_path_mutation→pytest.main",
                    "PASS" if status == "VERIFIED" else status,
                    "PASS",
                    "HONEST_VERIFIED" if status == "VERIFIED" else "HONEST_FAILED",
                    detail,
                )
            )
        # Honest control through the same blackbox route and fixture as the C1-C4 probes.
        for name, source in (
            ("blackbox-control", "def add(a, b):\n    return a + b\n# control\n"),
            ("blackbox-control-wrong", "def add(a, b):\n    return a - b\n"),
        ):
            root = parent / name
            base, cand = _simple_repo(root, source, "x = 1\n")
            ctx = VerifyContext(root, parent / "runs", base_commit=base, import_paths=("src",))
            verdicts, _ = blackbox_judge.judge_blackbox(blackbox.load(declaration), ctx, cand, name)
            rows.append(
                _row(
                    "CONTROL",
                    name,
                    "blackbox_judge.judge_blackbox→run_blackbox",
                    verdicts[0].reason,
                    "PASS" if name == "blackbox-control" else "FAILED",
                    "HONEST_VERIFIED" if "PASS" in verdicts[0].reason else "NOT_RUN",
                    verdicts[0].reason,
                )
            )
    # The remaining rows are raw outputs of the production judge, policy and receipt verifier.
    for name, value in (
        ("low-entropy-true", True),
        ("low-entropy-zero", 0),
        ("low-entropy-empty", ""),
    ):
        observed = _judge(_run(value), _declared(value))
        rows.append(
            _row(
                "C6",
                name,
                "blackbox_judge.judge_run",
                observed,
                "PASS",
                "VERIFIED_WRONG" if "PASS" in observed else "REFUSED",
            )
        )
    for name, run in (("timeout", _run(timed_out=True)), ("no-account", _run(account=False))):
        observed = _judge(run)
        rows.append(
            _row(
                "C9",
                name,
                "blackbox_judge.judge_run",
                observed,
                "VERIFICATION_INCOMPLETE",
                "REFUSED",
            )
        )
    from types import SimpleNamespace

    from cairnwork import verify_cli
    from cairnwork.intake import Intake
    from cairnwork.pipeline import TaskRejected

    try:
        verify_cli._run(
            Intake(Path("/nonexistent")),
            SimpleNamespace(
                require_protection=True,
                blackbox=None,
                verifier=(),
                repo_path=".",
                base="0" * 40,
                candidate="1" * 40,
                allowed_file=["src/calc.py"],
                test_commit="",
                import_path=[],
            ),
        )
    except TaskRejected as exc:
        c7a = exc.code
    else:
        c7a = "ACCEPTED"
    rows.append(
        _row(
            "C7",
            "a-no-blackbox",
            "verify_cli._run(--require-protection)",
            c7a,
            "UNPROTECTED",
            "REFUSED",
        )
    )
    with TemporaryDirectory(prefix="cw1353-policy-") as temp:
        parent = Path(temp).resolve()
        state = parent / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text('{"verifier_account": false}')
        (state / "ACCEPTING").touch()
        decl = parent / "decl.json"
        decl.write_text(
            json.dumps(
                {
                    "salt": "ab" * 32,
                    "checks": [
                        {
                            "id": "add",
                            "module": "calc",
                            "function": "add",
                            "cases": [{"id": "one", "args": [2, 3], "expected": 5}],
                        }
                    ],
                }
            )
        )
        decl.chmod(0o600)
        for name in ("b-partial", "c-require-blackbox"):
            root = parent / name
            base, cand = _simple_repo(
                root,
                "def add(a,b):\n return a+b\n# strict\n",
                "from calc import add\ndef test_deferred(): assert add(2,3)==5\n",
            )
            result = _verify(root, state, base, cand, decl, strict=True)
            observed = result.get("status", result.get("error", "FAILED"))
            expected = "VERIFIED partial_macos" if name == "b-partial" else "UNPROTECTED"
            rows.append(
                _row(
                    "C7",
                    name,
                    "verify_cli._run(--require-protection)",
                    observed,
                    expected,
                    "REFUSED",
                    result.get("reason", result.get("detail", "")),
                )
            )
    with TemporaryDirectory(prefix="cw1353-c5-") as temp:
        parent = Path(temp).resolve()
        for index, class_name in enumerate(("Liar", "Answer"), 1):
            root = parent / f"candidate-{index}"
            src = (
                f"class {class_name}(int):\n    def __eq__(self, other): return True\n"
                "    __hash__ = int.__hash__\ndef add(a, b): return "
                f"{class_name}(a + b + 1)\n"
            )
            base, cand = _simple_repo(
                root, src, "from calc import add\ndef test_deferred(): assert add(2,3)==5\n"
            )
            observed, detail = _same_process(root, base, cand)
            rows.append(
                _row(
                    "C5",
                    f"always-eq-{index}",
                    "scan_patch→refuse_new_sys_path_mutation→pytest.main",
                    observed,
                    "VERIFIED",
                    "VERIFIED_WRONG" if observed == "VERIFIED" else "REFUSED",
                    detail,
                )
            )
    import types

    from cairnwork.blackbox_runner import _RUNNER_SOURCE, MODULE

    runner = types.ModuleType(MODULE)
    sys.modules[MODULE] = runner
    exec(compile(_RUNNER_SOURCE, MODULE, "exec"), runner.__dict__)
    try:
        for index, condition in enumerate(
            (f"{MODULE!r} in sys.modules", "'pytest' not in sys.modules"), 1
        ):
            saved_pytest = sys.modules.pop("pytest", None) if index == 2 else None
            namespace = {}
            source = (
                "import sys\ndef add(a, b):\n    if "
                + condition
                + ": return a + b\n    return a - b\n"
            )
            exec(compile(source, f"candidate-{index}.py", "exec"), namespace)
            case = {"args": [2, 3], "kwargs": {}}
            raw = runner._call(namespace["add"], case)
            observed = _judge(
                _run(
                    raw["ok"],
                    report=json.dumps(
                        {"checks": {"add": {"file": "/co/calc.py", "cases": {"one": raw}}}}
                    ).encode(),
                ),
                _declared(5),
            )
            if saved_pytest is not None:
                sys.modules["pytest"] = saved_pytest
            rows.append(
                _row(
                    "C10",
                    f"runner-sniff-{index}",
                    "blackbox_runner._call→blackbox_judge.judge_run",
                    observed,
                    "blackbox:add: PASS [matched 1/1]",
                    "KNOWN_RESIDUAL",
                    f"runner returned {raw!r}; outside-runner branch returns "
                    f"{namespace['add'](2, 3)!r}",
                )
            )
    finally:
        sys.modules.pop(MODULE, None)
    # Receipt round trips are exercised by the real signed receipt fixtures and verify_bundle API.
    from cairnwork_attest.bundle import read_bundle
    from cairnwork_attest.verify import verify_bundle
    from tests.attest.binding_helpers import (
        notarize_bound,
        policy,
        producer,
        receiver,
        resign,
        sign_bound,
    )

    with TemporaryDirectory(prefix="cw1353-receipt-") as temp:
        p = producer(Path(temp))
        state = Path(temp) / "state"
        run = notarize_bound(
            state,
            p,
            required=("pytest-0", "blackbox:add"),
            blackbox_commitment="sha256:" + "d" * 64,
            protection="partial_macos" if sys.platform == "darwin" else "partial",
        )
        bundle = sign_bound(state, run, Path(temp) / "bundle.json")
        full = receiver(Path(temp), "receiver", p["work"], p["head"], p["tests"])
        pub = state / "public.pem"

        def check(path=bundle, **kw):
            kw.setdefault("expected_candidate", p["head"])
            return verify_bundle(path, public_key_pem=pub, repo=full, **kw).code

        # Valid baseline plus real verifier refusals; each record stores verify_bundle's code.
        valid = verify_bundle(bundle, public_key_pem=pub, repo=full, expected_candidate=p["head"])
        rows.append(
            _row(
                "C8",
                "valid",
                "verify_bundle",
                valid.code,
                "OK",
                "HONEST_VERIFIED" if valid.ok else "HONEST_FAILED",
            )
        )
        for name, kwargs, wanted in (
            ("head", {"expected_candidate": p["other"]}, "HEAD_MISMATCH"),
            ("base", {"expected_base": "1" * 40}, "BASE_MISMATCH"),
        ):
            rows.append(_row("C8", name, "verify_bundle", check(**kwargs), wanted, "REFUSED"))
        stmt = json.loads(__import__("base64").b64decode(read_bundle(bundle)["payload"]))
        for name, field, value, wanted in (
            ("test_commit", "hidden_test_commit", "e" * 40, "TEST_SOURCE_MISMATCH"),
            ("executed_tree", "executed_tree", "e" * 40, "TREE_MISMATCH"),
            (
                "blackbox_commitment",
                "blackbox_commitment",
                "sha256:" + "e" * 64,
                "BLACKBOX_COMMITMENT_MISMATCH",
            ),
        ):
            edited = json.loads(json.dumps(stmt))
            if field == "hidden_test_commit":
                edited["predicate"][field] = value
            else:
                edited["predicate"]["binding"][field] = value
            path = resign(state, edited, Path(temp) / f"{name}.json")
            rows.append(_row("C8", name, "verify_bundle", check(path), wanted, "REFUSED"))
        # Existing receiver policy verifies the actual missing-check and missing-protection cases.
        pol = policy(
            state,
            Path(temp),
            p["tests"],
            blackbox_commitment="sha256:" + "d" * 64,
            blackbox_checks=["add"],
            protection="blackbox",
        )
        rows.append(
            _row(
                "C8",
                "blackbox_checks",
                "verify_bundle",
                check(policy=pol),
                "BLACKBOX_CHECKS_MISSING",
                "REFUSED",
            )
        )
        old = notarize_bound(state, p, attempt="old", required=("pytest-0",))
        old_bundle = sign_bound(state, old, Path(temp) / "old.json")
        strict = policy(state, Path(temp), p["tests"], protection="blackbox")
        old_code = verify_bundle(
            old_bundle, public_key_pem=pub, repo=full, expected_candidate=p["head"], policy=strict
        ).code
        rows.append(
            _row(
                "C8",
                "v0.1-under-protected-policy",
                "verify_bundle",
                old_code,
                "TEST_SOURCE_MISMATCH",
                "REFUSED",
            )
        )
    return sorted(rows, key=lambda r: (r["class_id"], r["probe"]))


def test_observed_values_are_route_returns_not_literals():
    """The record schema ties each value to its callable route and source return value."""
    import inspect
    import re

    source = inspect.getsource(run_baseline_probes)
    assert '"observed"' not in source
    assert not re.search(r'observed\s*=\s*["\'](?:VERIFIED|FAILED|PASS|NOT_RUN)', source)
    for row in run_baseline_probes():
        assert row["route"] and row["observed"] is not None
        assert isinstance(row["matches_spec"], bool)


@pytest.mark.parametrize("class_id", [f"C{i}" for i in range(1, 11)])
def test_class_results_are_pinned_to_main(class_id):
    """C1-C10; spec §5.1 rows are compared with measured, pinned production results."""
    rows = [p for p in run_baseline_probes() if p["class_id"] == class_id]
    assert len(rows) >= 2
    assert all(p["observed"] for p in rows)
    if class_id in {"C1", "C2", "C3", "C4"}:
        got = {row["probe"]: row["observed"] for row in rows}
        expected = {
            "C1": ("REFUSED", "REFUSED"),
            "C2": ("FAILED", "REFUSED"),
            "C3": ("FAILED", "FAILED"),
            "C4": ("REFUSED", "REFUSED"),
        }[class_id]
        assert (got["same-process-1"], got["same-process-2"]) == expected
        # Broken candidates (a - b) never match; owner mode can never pass in any case.
        wanted = (
            "blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 0/1]"
            if sandbox_available()
            else "blackbox:add: VERIFICATION_INCOMPLETE: NO_SANDBOX"
        )
        assert all(
            row["observed"] == wanted for row in rows if row["probe"].startswith("blackbox-")
        )
    if class_id == "C7":
        got = {row["probe"]: row["observed"] for row in rows}
        assert got["a-no-blackbox"] == "UNPROTECTED"
        # Owner mode (no verifier account) can never pass a declared check; no sandbox: no run.
        if sandbox_available():
            assert got["b-partial"] == got["c-require-blackbox"] == "FAILED"
            assert all(
                "BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 1/1]" in r["reason"]
                for r in rows
                if r["probe"] != "a-no-blackbox"
            )
    if class_id == "C10":
        assert {row["probe"]: row["observed"] for row in rows} == {
            "runner-sniff-1": "blackbox:add: PASS [matched 1/1]",
            "runner-sniff-2": "blackbox:add: PASS [matched 1/1]",
        }
    if class_id == "C5":
        assert [row["observed"] for row in rows] == ["VERIFIED", "VERIFIED"]
    if class_id == "C6":
        assert all(": PASS " in row["observed"] for row in rows)


def test_blackbox_fixture_imports_the_declared_module():
    """The route's fixture must be sound: an honest candidate is matched (PASS with an account)."""
    rows = {p["probe"]: p["observed"] for p in run_baseline_probes() if p["class_id"] == "CONTROL"}
    if not sandbox_available():
        wanted = "blackbox:add: VERIFICATION_INCOMPLETE: NO_SANDBOX"
        assert set(rows.values()) == {wanted}
        return
    assert "NOT_IMPORTED" not in "".join(rows.values())
    # The route runs as the owner (no verifier account), where a declared check never passes.
    need = "blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT"
    assert rows["blackbox-control"] == f"{need} [matched 1/1]"
    assert rows["blackbox-control-wrong"] == f"{need} [matched 0/1]"


def test_c8_receipt_refusal_codes_are_pinned():
    """C8; spec §5.1 row C8: assert codes returned by real receipt verification."""
    rows = [row for row in run_baseline_probes() if row["class_id"] == "C8"]
    assert {row["probe"]: row["observed"] for row in rows} == {
        "valid": "OK",
        "head": "HEAD_MISMATCH",
        "base": "BASE_MISMATCH",
        "test_commit": "TEST_SOURCE_MISMATCH",
        "executed_tree": "BINDING_HASH_MISMATCH",
        "blackbox_commitment": "BINDING_HASH_MISMATCH",
        "blackbox_checks": "UNPROTECTED",
        "v0.1-under-protected-policy": "UNPROTECTED",
    }
