"""#1454 step 2b: shape of the trusted #1353 workflow and the whitelist of its status publisher.

Offline. The workflow files are read with a small parser for the YAML subset they use (no YAML
dependency is installed).
"""

from __future__ import annotations

import re
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest
from workflow_yaml import parse_yaml

from scripts.ci import pin_guard, pra1353_trusted_status

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
TRUSTED = WORKFLOWS / "pra1353-trusted.yml"
STATUS_SCRIPT = ROOT / "scripts/ci/pra1353_trusted_status.py"
CONTEXT = "pra1353 gate (trusted)"


def load(path: Path) -> dict[str, Any]:
    parsed = parse_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def _checkouts(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/checkout@")]


def _job_text(path: Path, job: str) -> str:
    text = path.read_text(encoding="utf-8")
    head = re.search(rf"^  {job}:\n", text, re.M)
    assert head
    after = re.search(r"^  \w[\w-]*:\n", text[head.end() :], re.M)
    return text[head.start() : head.end() + after.start()] if after else text[head.start() :]


# --- A. workflow shape ----------------------------------------------------------------------


def test_triggers_and_permissions() -> None:
    doc = load(TRUSTED)
    trigger = doc["on"]
    assert list(trigger) == ["pull_request_target"]
    assert trigger["pull_request_target"]["branches"] == ["main"]
    assert "edited" in trigger["pull_request_target"]["types"]
    assert doc["permissions"] == {}
    jobs = doc["jobs"]
    assert set(jobs) == {"analyze", "publish"}
    assert jobs["analyze"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "read", "statuses": "write"}
    assert jobs["publish"]["needs"] == "analyze"
    assert "statuses" not in _job_text(TRUSTED, "analyze").replace("statuses:write", "")


def test_analyze_has_no_secrets_and_publish_reads_no_pr_content() -> None:
    analyze = _job_text(TRUSTED, "analyze")
    for forbidden in ("environment:", "secrets.", "github.token", "GH_TOKEN", "statuses"):
        assert forbidden not in analyze
    publish = load(TRUSTED)["jobs"]["publish"]
    publish_text = _job_text(TRUSTED, "publish")
    # No head checkout, no path into the head tree, nothing that names it.
    for step in _checkouts(publish):
        assert set(step.get("with", {})) <= {"persist-credentials"}
    assert not re.search(r"(?<![\w.])head(?![\w.])", publish_text.replace("HEAD_SHA", ""))
    assert "head/" not in publish_text and "path: head" not in publish_text


def test_checkouts_and_uv() -> None:
    jobs = load(TRUSTED)["jobs"]
    ref_checkouts = []
    for name, job in jobs.items():
        for step in _checkouts(job):
            assert step["with"]["persist-credentials"] == "false"
            if "ref" in step["with"] or "repository" in step["with"]:
                ref_checkouts.append((name, step["with"]["path"]))
    assert ref_checkouts == [("analyze", "head")]
    analyze = _job_text(TRUSTED, "analyze")
    setup = [s for s in _steps(jobs["analyze"]) if "setup-uv" in str(s.get("uses", ""))]
    assert len(setup) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", setup[0]["with"]["version"])
    assert setup[0]["with"]["enable-cache"] == "false"
    assert re.search(r"@[0-9a-f]{40}\b", setup[0]["uses"])
    for needle in ("--no-cache", "--locked", "--no-install-project", "stop-commands"):
        assert needle in analyze
    assert analyze.count('UV_NO_CONFIG: "1"') == 1 and "UV_NO_CONFIG=1" in analyze
    assert "refs/heads/main" in analyze
    for job in jobs.values():
        for step in _steps(job):
            assert re.search(r"@[0-9a-f]{40}\b", str(step.get("uses", "@" + "0" * 40)))


def test_single_execution_point_and_no_expression_injection() -> None:
    text = TRUSTED.read_text(encoding="utf-8")
    assert not re.search(r"github\.event\.pull_request\.(title|body|head\.ref)", text)
    assert "github.head_ref" not in text
    analyze = load(TRUSTED)["jobs"]["analyze"]
    touching = []
    for step in _steps(analyze):
        for line in str(step.get("run", "")).splitlines():
            if line.strip().startswith(("sudo -n cp", "sudo -n chown", "sudo -n chattr")):
                continue  # copying and ownership of the data tree, nothing is executed
            if re.search(r"(?<![\w.$])head(?![\w.])|/head(?![\w.])", line):
                touching.append(line.strip())
    assert len(touching) == 1
    assert touching[0].endswith('--source "$root/head" || rc=$?')
    assert violations(text) == []


def test_artifact_flow_and_context() -> None:
    jobs = load(TRUSTED)["jobs"]
    up = [s for s in _steps(jobs["analyze"]) if "upload-artifact" in str(s.get("uses", ""))]
    down = [s for s in _steps(jobs["publish"]) if "download-artifact" in str(s.get("uses", ""))]
    assert len(up) == 1 and len(down) == 1
    assert not any("download-artifact" in str(s.get("uses", "")) for s in _steps(jobs["analyze"]))
    assert not any("upload-artifact" in str(s.get("uses", "")) for s in _steps(jobs["publish"]))
    assert CONTEXT == pra1353_trusted_status.CONTEXT
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = ROOT / name
        if path.is_file():
            assert CONTEXT not in path.read_text(encoding="utf-8")


def test_new_files_are_guarded_paths() -> None:
    for path in (
        ".github/workflows/pra1353-trusted.yml",
        "scripts/ci/pra1353_trusted_status.py",
    ):
        assert path in pin_guard.FIXED_PATHS


# --- C. no workflow runs PR head content under pull_request_target --------------------------

PR_HEAD = re.compile(r"github\.event\.pull_request\.head|github\.head_ref|refs/pull/")
HEAD_PATH = r"(?:\./)?(?:\"?\$(?:\{GITHUB_WORKSPACE\}|GITHUB_WORKSPACE)/)?"


def violations(text: str) -> list[str]:
    """Problems in one workflow: PR head checked out and then used as anything but data."""
    doc = parse_yaml(text)
    if "pull_request_target" not in str(doc.get("on", doc.get(True, ""))) and (
        "pull_request_target" not in text
    ):
        return []
    found: list[str] = []
    for name, job in doc.get("jobs", {}).items():
        paths = []
        for step in _checkouts(job):
            with_ = step.get("with", {}) or {}
            if PR_HEAD.search(str(with_.get("ref", "")) + str(with_.get("repository", ""))):
                path = with_.get("path")
                if not path or path in {".", "./"}:
                    found.append(f"{name}: PR head checked out into the workspace root")
                else:
                    paths.append(str(path).strip("/"))
        if not paths:
            continue
        for step in _steps(job):
            if str(step.get("uses", "")).startswith("./"):
                found.append(f"{name}: local action from the checkout")
            for line in str(step.get("run", "")).splitlines():
                for path in paths:
                    p = re.escape(path)
                    if re.search(rf"\bcd\s+[\"']?{HEAD_PATH}{p}\b", line):
                        found.append(f"{name}: cd into the PR head: {line.strip()}")
                    if re.search(rf"(?<![\w.-]){HEAD_PATH}{p}/", line):
                        found.append(f"{name}: path inside the PR head: {line.strip()}")
                    if re.search(rf"(PATH|PYTHONPATH|GITHUB_ENV)\S*.*(?<![\w.-]){p}\b", line):
                        found.append(f"{name}: PR head on a search path: {line.strip()}")
                    if re.search(rf"\b(uv|pip|npm|make|pytest)\b.*(?<![\w.-]){p}\b", line):
                        found.append(f"{name}: tool pointed at the PR head: {line.strip()}")
    return found


def test_all_workflows_treat_pr_head_as_data() -> None:
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert TRUSTED in files
    for path in files:
        assert violations(path.read_text(encoding="utf-8")) == [], path.name


BAD_HEAD = """name: x
on:
  pull_request_target:
    branches: [main]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{sha}
        with:
          ref: ${{{{ github.event.pull_request.head.sha }}}}
          {path}
      - run: {run}
"""


@pytest.mark.parametrize(
    "path,run",
    [
        ("path: head", "cd head && ./run.sh"),
        ("path: head", "head/scripts/build.sh"),
        ("path: head", 'python "$GITHUB_WORKSPACE/head/tool.py"'),
        ("path: head", "uv run pytest head"),
        ("path: head", 'export PYTHONPATH="$PWD/head"'),
        ("", "echo hi"),
    ],
)
def test_scanner_flags_head_execution(path: str, run: str) -> None:
    sha = "0" * 40
    assert violations(BAD_HEAD.format(sha=sha, path=path, run=run))


def test_scanner_flags_local_action_and_accepts_source_arg() -> None:
    sha = "0" * 40
    local = (
        BAD_HEAD.format(sha=sha, path="path: head", run="echo hi") + "      - uses: ./head/act\n"
    )
    assert violations(local)
    ok = BAD_HEAD.format(sha=sha, path="path: head", run='gate --source "$GITHUB_WORKSPACE/head"')
    assert violations(ok) == []


# --- B/D. the status publisher only accepts "0", "1", "3" -----------------------------------

VALID = {"0": "success", "1": "failure", "3": "failure"}
INVALID: list[object] = [
    "2",
    "",
    "0\n1",
    " 0",
    "0 ",
    "0\n",
    "00",
    "01",
    "1;2",
    "-1",
    "4",
    "x" * 100000,
    "0" * 17,
    None,
    0,
    b"0",
    ["0"],
]


def check_whitelist(decide: Any) -> None:
    for raw, state in VALID.items():
        got = decide(raw)
        assert got[0] == state and got[2] is True
    for raw in INVALID:
        got = decide(raw)
        assert got[0] == "failure" and got[2] is False, raw


def test_whitelist_exact() -> None:
    check_whitelist(pra1353_trusted_status.decide)
    assert set(pra1353_trusted_status.ACCEPTED) == set(VALID)


def _mutated(old: str, new: str) -> Any:
    source = STATUS_SCRIPT.read_text(encoding="utf-8")
    assert source.count(old) >= 1
    module = types.ModuleType("mutant")
    exec(compile(source.replace(old, new, 1), "mutant", "exec"), module.__dict__)  # noqa: S102
    return module.decide


LOOKUP = "raw in ACCEPTED:\n        state, description = ACCEPTED[raw]"


@pytest.mark.parametrize(
    "old,new",
    [
        ('    "3": (', '    "2": ("failure", "x"),\n    "3": ('),
        (LOOKUP, LOOKUP.replace("raw", "raw.strip()")),
        (LOOKUP, LOOKUP.replace("raw", "raw[:1]")),
        (LOOKUP, LOOKUP.replace("raw", "(raw.lstrip('0') or '0')")),
    ],
)
def test_mutation_loosening_the_whitelist_is_caught(old: str, new: str) -> None:
    with pytest.raises(AssertionError):
        check_whitelist(_mutated(old, new))


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"0", "0"),
        (b"1", "1"),
        (b"3", "3"),
        (b"0\n", "0\n"),
        (b"\xff", None),
        (b"0" * 17, None),
    ],
)
def test_read_rc(tmp_path: Path, content: bytes, expected: str | None) -> None:
    path = tmp_path / "gate_rc"
    path.write_bytes(content)
    assert pra1353_trusted_status.read_rc(path) == expected
    assert pra1353_trusted_status.read_rc(tmp_path / "missing") is None


class _Run:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)


SHA = "a" * 40


@pytest.mark.parametrize(
    "content,state,code",
    [
        (b"0", "success", 0),
        (b"1", "failure", 0),
        (b"3", "failure", 0),
        (b"2", "failure", 1),
        (b"", "failure", 1),
        (b"0\n1", "failure", 1),
        (None, "failure", 1),
    ],
)
def test_cli_posts_fixed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes | None, state: str, code: int
) -> None:
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    path = tmp_path / "gate_rc"
    if content is not None:
        path.write_bytes(content)
    argv = ["--rc-file", str(path), "--repo", "o/r", "--sha", SHA, "--run-url", "https://x/y"]
    assert pra1353_trusted_status.main(argv) == code
    (call,) = run.calls
    assert call[:2] == ["gh", "api"] and call[2] == f"repos/o/r/statuses/{SHA}"
    assert f"state={state}" in call and f"context={CONTEXT}" in call


@pytest.mark.parametrize(
    "sha", ["", "A" * 40, "a" * 39, "a" * 41, "a" * 40 + "\n", "../" + "a" * 37]
)
def test_bad_sha_posts_nothing(sha: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    path = tmp_path / "gate_rc"
    path.write_bytes(b"0")
    assert pra1353_trusted_status.main(["--rc-file", str(path), "--repo", "o/r", "--sha", sha]) == 1
    assert run.calls == []


def test_status_script_reads_no_pr_paths() -> None:
    source = STATUS_SCRIPT.read_text(encoding="utf-8")
    assert "head/" not in source and "GITHUB_OUTPUT" not in source
    assert pra1353_trusted_status.read_rc.__name__ == "read_rc"
