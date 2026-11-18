"""Static guard for the trusted-checks GitHub App wiring (Issue #1444).

The App private key lives only in the deployment environment ``trusted-checks``, whose branch
policy allows ``main`` only. These checks keep the workflow side of that boundary: only workflows
whose every trigger runs with GITHUB_REF on the default branch may reference the environment or
its secrets, and the jobs that hold the key never check out PR content.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ENV_NAME = "trusted-checks"
# Triggers whose GITHUB_REF is the default branch (pull_request_target: base branch, which the
# workflows restrict to main). pull_request, push, workflow_dispatch, schedule etc. are excluded.
MAIN_ONLY_TRIGGERS = {"pull_request_target", "issue_comment", "workflow_run"}
APP_TOKEN_ACTION = re.compile(r"uses:\s*actions/create-github-app-token@([0-9a-f]{40})\s")
EXPECTED = {
    "pyright-trusted.yml": "pyright (trusted)",
    "pin-guard-trusted.yml": "pin guard (trusted)",
}


def _top_level_block(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if re.match(rf"^['\"]?{re.escape(key)}['\"]?:", line):
            inside = True
            rest = line.split(":", 1)[1].strip()
            if rest:
                out.append(rest)
            continue
        if inside:
            if line and not line[0].isspace() and not line.startswith("#"):
                break
            out.append(line)
    return out


def triggers(text: str) -> set[str]:
    block = _top_level_block(text, "on")
    if block and not block[0].startswith(" "):
        inline = block[0].strip("[] ")
        return {part.strip() for part in inline.split(",") if part.strip()}
    found = set()
    for line in block:
        match = re.match(r"^  ([A-Za-z_]+):", line)
        if match:
            found.add(match.group(1))
    return found


def jobs(text: str) -> dict[str, str]:
    block = _top_level_block(text, "jobs")
    result: dict[str, list[str]] = {}
    current = None
    for line in block:
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(body) for name, body in result.items()}


def uses_trusted_env(job: str) -> bool:
    return bool(re.search(rf"^\s*environment:\s*(name:\s*)?['\"]?{ENV_NAME}['\"]?\s*$", job, re.M))


def problems(name: str, text: str) -> list[str]:
    """Every rule violation in one workflow file, as short messages."""
    found: list[str] = []
    mentions = ENV_NAME in text or "TRUSTED_CHECKS_" in text
    if not mentions:
        return found
    extra = triggers(text) - MAIN_ONLY_TRIGGERS
    if extra:
        found.append(f"{name}: references {ENV_NAME} but has triggers {sorted(extra)}")
    for job_name, job in jobs(text).items():
        in_env = uses_trusted_env(job)
        if "TRUSTED_CHECKS_" in job and not in_env:
            found.append(f"{name}:{job_name}: uses TRUSTED_CHECKS_* outside the environment")
        if not in_env:
            continue
        if re.search(r"^\s*(ref|repository):", job, re.M):
            found.append(f"{name}:{job_name}: environment job checks out PR content")
        if "create-github-app-token" in job and not APP_TOKEN_ACTION.search(job):
            found.append(f"{name}:{job_name}: create-github-app-token is not pinned to a SHA")
        perms = set(re.findall(r"^\s*(permission-[a-z-]+):\s*(\S+)", job, re.M))
        if perms and perms != {("permission-checks", "write")}:
            found.append(f"{name}:{job_name}: App token permissions {sorted(perms)}")
    return found


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_no_workflow_breaks_the_environment_boundary() -> None:
    found: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        found += problems(path.name, path.read_text(encoding="utf-8"))
    assert found == []


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_trusted_workflow_posts_same_name_check_run_from_publish_only(name: str) -> None:
    text = _read(name)
    all_jobs = jobs(text)
    env_jobs = [job for job, body in all_jobs.items() if uses_trusted_env(body)]
    assert env_jobs == ["publish"]
    publish = all_jobs["publish"]
    assert APP_TOKEN_ACTION.search(publish)
    assert "client-id: ${{ vars.TRUSTED_CHECKS_CLIENT_ID }}" in publish
    assert "private-key: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}" in publish
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in publish
    context = EXPECTED[name]
    # The old commit status stays until the ruleset is switched; then a separate PR drops it.
    assert f'-f context="{context}"' in publish
    assert f'-f name="{context}"' in publish
    # Fail closed: only an exact success state maps to a success conclusion.
    assert "conclusion=failure" in publish
    assert 'if [ "$STATE" = "success" ]; then conclusion=success; fi' in publish
    assert "grep -Eqx '[0-9a-f]{40}'" in publish
    assert "TRUSTED_CHECKS_" not in all_jobs["analyze"]


def test_pr_triggered_workflows_do_not_reference_the_environment() -> None:
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if triggers(text) - MAIN_ONLY_TRIGGERS:
            assert ENV_NAME not in text, path.name
            assert "TRUSTED_CHECKS_" not in text, path.name


GOOD = """\
on:
  pull_request_target:
    branches: [main]
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          ref: ${{ github.event.pull_request.head.sha }}
  publish:
    environment: trusted-checks
    steps:
      - uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3
        with:
          private-key: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}
          permission-checks: write
"""


def test_checker_accepts_the_reference_shape() -> None:
    assert problems("good.yml", GOOD) == []


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        (GOOD.replace("pull_request_target", "pull_request"), "triggers"),
        (GOOD.replace("  pull_request_target:", "  push:\n  pull_request_target:"), "triggers"),
        (GOOD.replace("on:\n  pull_request_target:", "on: [pull_request]\nx:"), "triggers"),
        (GOOD.replace("    environment: trusted-checks\n", ""), "outside the environment"),
        (
            GOOD.replace(
                "    steps:\n      - uses: actions/create",
                "    steps:\n      - uses: actions/checkout@x\n        with:\n"
                "          ref: ${{ github.event.pull_request.head.sha }}\n"
                "      - uses: actions/create",
            ),
            "checks out PR content",
        ),
        (GOOD.replace("@64ab729a6294000f5aa4a4bc0b73d1f32bc45510", "@v3"), "not pinned"),
        (
            GOOD.replace(
                "permission-checks: write",
                "permission-checks: write\n          permission-contents: write",
            ),
            "permissions",
        ),
        (
            GOOD.replace(
                "  analyze:\n",
                "  analyze:\n    env:\n      K: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}\n",
            ),
            "outside the environment",
        ),
    ],
)
def test_checker_flags_each_boundary_break(bad: str, needle: str) -> None:
    found = problems("bad.yml", bad)
    assert any(needle in item for item in found), found
