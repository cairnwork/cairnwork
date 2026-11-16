"""Shared production-route probe builders for the #1353 baseline."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

_NO_SANDBOX_BASE = (
    "UNCOMPARABLE: BASE_ENV_FAILED: unchanged base: base-probe-failed "
    "(no-sandbox: no usable sandbox backend, so nothing ran)"
)
_ACCOUNT_GAP = (
    "[blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 1/1]]"
)


def sandbox_available():
    from cairnwork_sandbox import select

    return sys.platform == "darwin" and select() is not None


def c2_unavailable(rows):
    return [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]


def _scrub(value):
    return (
        re.sub(r"/(?:private/)?(?:var/folders|tmp)/[^\s:]+", "<temporary-path>", value)
        if isinstance(value, str)
        else value
    )


def row(cid, probe, route, raw, expected, classification, reason=""):
    if isinstance(raw, dict):
        observed = str(raw.get("status", raw.get("error", raw.get("code", raw))))
        saved = {
            key: raw[key]
            for key in ("status", "error", "verified", "reason", "code", "detail")
            if key in raw
        }
    elif hasattr(raw, "status"):
        observed = str(raw.status)
        saved = {"status": raw.status, "reason": getattr(raw, "reason", "")}
    elif hasattr(raw, "code"):
        observed = str(raw.code)
        saved = {"code": raw.code, "detail": getattr(raw, "detail", "")}
    else:
        observed = str(raw)
        saved = raw
    reason = _scrub(reason)
    if isinstance(saved, dict):
        saved = {key: _scrub(value) for key, value in saved.items()}
    matches = observed == expected
    if expected == "REJECTED":
        matches = observed not in {"OK", "ACCEPTED"}
    elif expected == "NOT_RUN":
        matches = any(
            x in observed or x in reason for x in ("NO_SANDBOX", "BLACKBOX_NEEDS_VERIFIER_ACCOUNT")
        ) or (observed == "UNCOMPARABLE" and "sandbox" in reason.lower())
    elif expected == "VERIFICATION_INCOMPLETE":
        matches = "VERIFICATION_INCOMPLETE:" in observed
    elif expected == "PASS":
        matches = observed == "PASS" or ": PASS " in observed
    return {
        "class_id": cid,
        "probe": probe,
        "route": route,
        "observed": observed,
        "raw_return": saved,
        "expected_on_main": expected,
        "matches_spec": matches,
        "classification": classification,
        "platform": sys.platform,
        "reason": reason,
    }


def repo(root: Path, source: str, test_source: str, *, files=None):
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
        "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\nimport calc\n"  # noqa: E501
    )
    for name, value in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (root / "src/calc.py").write_text(source)
    git("add", "-A")
    git("commit", "-q", "-m", "candidate")
    return base, git("rev-parse", "HEAD")


def _cairnwork_src():
    import cairnwork

    return str(Path(cairnwork.__file__).resolve().parent.parent)


# -I drops PYTHONPATH, the only way Core's verifier exposes the checkout under test (the verifier
# venv has no cairnwork installed); put that same package first, isolation otherwise kept.
# -I also drops PYTHONDONTWRITEBYTECODE, so -B: no __pycache__ written into the checkout under test.
_LAUNCH = (
    "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
    "runpy.run_module('cairnwork.verify_cli', run_name='__main__', alter_sys=True)"
)


def verify(root, state, base, cand, declaration=None, strict=False):
    args = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        _LAUNCH,
        _cairnwork_src(),
        "--state",
        str(state),
        "run",
        "--repo-path",
        str(root),
        "--base",
        base,
        "--candidate",
        cand,
        "--allowed-file",
        "src/calc.py",
        "--verifier",
        "python3 -m pytest -q tests/test_calc.py",
        "--import-path",
        "src",
    ]
    if declaration:
        args += ["--blackbox", str(declaration)]
    if strict:
        args.append("--require-protection")
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "reason": "verify_cli exceeded 120 seconds"}
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"status": "FAILED", "reason": done.stderr[-400:]}


def declaration(path, expected=5):
    path.write_text(
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
        )
    )
    path.chmod(0o600)
    return path


def blackbox_result(result):
    return result.get("status", result.get("error", "FAILED")), result.get(
        "reason", result.get("detail", "")
    )


def blackbox_classification(status, reason):
    if status == "TIMEOUT":
        return "HARNESS_TIMEOUT"
    segments = _gate_segments(reason)
    failing = [
        segment
        for segment in segments
        if segment.startswith("pytest-")
        or (
            segment.startswith("blackbox:")
            and ": PASS " not in segment
            and not segment.endswith(": PASS")
        )
        or (segment.startswith("protection:") and ": PASS " not in segment)
    ]
    missing_account_only = bool(failing) and all(
        segment.startswith("blackbox:")
        and "VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT" in segment
        for segment in failing
    )
    if missing_account_only:
        return "NOT_RUN"
    return "REFUSED"


def _gate_segments(reason):
    segments = []
    start = None
    depth = 0
    for index, char in enumerate(reason):
        if char == "[":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                segment = reason[start:index]
                if segment.startswith(("pytest-", "blackbox:", "protection:")):
                    segments.append(segment)
    return segments


def verification_classification(status, verified, refused):
    if status == "TIMEOUT":
        return "HARNESS_TIMEOUT"
    return verified if status.startswith("VERIFIED") else refused


def evasion_sources():
    return {
        "C1": [
            "import importlib; importlib.import_module('_pyt' + 'est.reports'); import pytest; pytest.Function.runtest=lambda self: None",  # noqa: E501
            "import sys, importlib; vars(sys)['pycache_'+'prefix'] = '/tmp/cw1353-cache'; loader=vars(importlib)['import_module']; loader('_pyt'+'est.reports'); import pytest; pytest.Function.runtest=lambda self: None",  # noqa: E501
            (
                "import importlib; "
                "python=importlib.import_module('_pyt'+'est.python'); "
                "setattr(python.Function, 'runtest', lambda self: None)"
            ),
        ],
        "C2": [
            "import pytest\nfor target in (pytest.Function,): setattr(target, 'runtest', lambda self: None)",  # noqa: E501
            "import pytest\nF = pytest.Function\nF.runtest = lambda self: None",
        ],
        "C3": [
            "from pathlib import Path\ntarget=Path('tests/test_calc.py'); print('from calc import add\\ndef test_deferred(): assert add(2,3)==5', file=target.open('w'))",  # noqa: E501
            "from functools import partial\nfrom pathlib import Path\ntarget=Path('tests/test_calc.py'); writer=partial(open, target, 'w'); writer().write('from calc import add\\ndef test_deferred(): assert add(2,3)==5')\nimport os\nos.sys.path.insert(0, 'src')",  # noqa: E501
        ],
        "C4": [
            "from pathlib import Path\nimport importlib\nPath('src/pkg').mkdir(); Path('src/pkg/__init__.py').write_text(\"from pathlib import Path\\nPath('tests/test_calc.py').write_text('from calc import add\\\\ndef test_deferred(): assert add(2,3)==5')\\n\")\nname='pkg'; importlib.import_module(name)",  # noqa: E501
            "from pathlib import Path\nimport importlib\nPath('src/pkg').mkdir(); Path('src/pkg/__init__.py').write_text(\"from pathlib import Path\\nPath('tests/test_calc.py').write_text('from calc import add\\\\ndef test_deferred(): assert add(2,3)==5')\\n\")\nPath('src/pkg/mod.py').write_text('VALUE=2')\nname='pkg.mod'; module=importlib.import_module(name)",  # noqa: E501
        ],
    }


def _pass_mutant(parent, state, cid, n, src, evasion, bad_test):
    mutant_src = src.replace(evasion, "pass")
    root = parent / f"{cid}-pass-{n}"
    base, candidate = repo(root, mutant_src, bad_test)
    result = verify(root, state, base, candidate)
    status = result.get("status", result.get("error", "FAILED"))
    label = "C3_MUTANT" if cid == "C3" else f"{cid}_MUTANT"
    name = f"evasion-{n}-replaced-by-pass" if cid == "C3" else "evasion-replaced-by-pass"
    return row(
        label,
        name,
        "verify_cli run",
        status,
        "FAILED",
        verification_classification(status, "HONEST_VERIFIED", "HONEST_FAILED"),
        result.get("reason", ""),
    )


def _c1_c4(parent, state, decl):
    rows = []
    bad_test = "from calc import add\ndef test_deferred(): assert add(2, 3) == 6\n"
    evasions = evasion_sources()
    for cid, variants in evasions.items():
        for n, evasion in enumerate(variants, 1):
            root = parent / f"{cid}-{n}"
            src = "import sys\n" + evasion + "\ndef add(a,b): return a+b\n"
            base, cand = repo(root, src, bad_test)
            result = verify(root, state, base, cand)
            status = result.get("status", result.get("error", "FAILED"))
            rows.append(
                row(
                    cid,
                    f"verify-cli-{n}",
                    "verify_cli run",
                    result,
                    "VERIFIED",
                    verification_classification(status, "VERIFIED_WRONG", "REFUSED"),
                    result.get("reason", ""),
                )
            )
            result = verify(root, state, base, cand, decl)
            status, reason = blackbox_result(result)
            rows.append(
                row(
                    cid,
                    f"blackbox-{n}",
                    "verify_cli run --blackbox",
                    status,
                    "NOT_RUN",
                    blackbox_classification(status, reason),
                    reason,
                )
            )
            if cid in {"C1", "C4", "C2"} and n == 1 or cid == "C3":
                rows.append(_pass_mutant(parent, state, cid, n, src, evasion, bad_test))
    return rows


def _c5_honest(parent, state):
    rows = []
    test = "from calc import add\ndef test_deferred(): assert add(2,3) == 5\n"
    for n, cls in enumerate(("Liar", "Answer"), 1):
        src = f"class {cls}(int):\n def __eq__(self, other): return True\n __hash__=int.__hash__\ndef add(a,b): return {cls}(a+b+1)\n"  # noqa: E501
        root = parent / f"C5-{n}"
        base, cand = repo(root, src, test)
        res = verify(root, state, base, cand)
        status = res.get("status", res.get("error", "FAILED"))
        rows.append(
            row(
                "C5",
                f"liar-int-{n}",
                "verify_cli run",
                res,
                "VERIFIED",
                verification_classification(status, "VERIFIED_WRONG", "REFUSED"),
                res.get("reason", ""),
            )
        )
    for n in range(10):
        root = parent / f"honest-{n}"
        base, cand = repo(root, "def add(a,b): return a+b\n# correct fix\n", test)
        res = verify(root, state, base, cand)
        status = res.get("status", res.get("error", "FAILED"))
        rows.append(
            row(
                "HONEST",
                f"correct-{n + 1:02}",
                "verify_cli run",
                res,
                "VERIFIED",
                verification_classification(status, "HONEST_VERIFIED", "HONEST_FAILED"),
                res.get("reason", ""),
            )
        )
    return rows


@cache
def core_rows():
    with TemporaryDirectory(prefix="cw1353-") as td:
        parent = Path(td).resolve()
        state = parent / "state"
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        (state / "config.json").write_text('{"verifier_account": false}')
        (state / "ACCEPTING").touch()
        decl = declaration(parent / "decl.json")
        return _c1_c4(parent, state, decl) + _c5_honest(parent, state)
