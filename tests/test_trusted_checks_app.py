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
APP_TOKEN_ACTION = re.compile(
    r"['\"]?uses['\"]?:\s*['\"]?actions/create-github-app-token@([0-9a-f]{40})",
    re.IGNORECASE,
)
APP_TOKEN_SHA = "64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
# The App token may carry exactly this and nothing else (non-empty: an empty set inherits all).
ALLOWED_APP_PERMISSIONS = {("permission-checks", "write")}
EXPECTED = {
    "pyright-trusted.yml": "pyright (trusted)",
    "pin-guard-trusted.yml": "pin guard (trusted)",
}
SUBJECT_MARKERS = (
    ENV_NAME,
    "trusted_checks_",
    "create-github-app-token",
    APP_TOKEN_SHA,
)


def _is_subject(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in SUBJECT_MARKERS)


def strict_syntax_problems(name: str, text: str) -> list[str]:
    """Reject YAML forms outside the deliberately small trusted-workflow subset."""
    found: list[str] = []
    run_indent: int | None = None
    mapping = re.compile(r"^( *)(?:- )?([A-Za-z0-9_-]+):( .*)?$")
    sequence = re.compile(r"^( *)- ([^\s#&*!|>'\"%@`{}\[\],?:][^#]*)$")
    for number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if "input_" in lowered:
            found.append(f"{name}:{number}: strict YAML syntax forbids INPUT_")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if run_indent is not None and indent > run_indent:
            continue
        run_indent = None
        match = mapping.fullmatch(line)
        item = sequence.fullmatch(line)
        if not match and not item:
            found.append(f"{name}:{number}: strict YAML syntax rejects this line")
            continue
        if re.match(r"^ *(?:- )?[?:]", line):
            found.append(f"{name}:{number}: strict YAML syntax rejects explicit keys")
        if "\\" in line:
            found.append(f"{name}:{number}: strict YAML syntax forbids backslashes")
        if "<<" in line:
            found.append(f"{name}:{number}: strict YAML syntax forbids merge keys")
        key = match.group(2).lower() if match else ""
        value = (
            match.group(3).strip()
            if match and match.group(3)
            else item.group(2).strip()
            if item
            else ""
        )
        # Only three inert flow forms: an empty permissions map, and flat lists of plain
        # lowercase words under branches/types (e.g. `types: [opened, synchronize]`).
        flow_ok = (key == "permissions" and value == "{}") or (
            key in {"branches", "types"}
            and re.fullmatch(r"\[ ?[a-z_]+(?:, ?[a-z_]+)* ?\]", value) is not None
        )
        if value.startswith(("{", "[")) and not flow_ok:
            found.append(f"{name}:{number}: strict YAML syntax forbids flow values")
        if value.startswith(("&", "*", "!")) or re.search(
            r"(?<![A-Za-z0-9_-])[&*!][A-Za-z0-9_-]+", line
        ):
            found.append(f"{name}:{number}: strict YAML syntax forbids anchors, aliases, and tags")
        if value.startswith(("|", ">")):
            if key == "run" and value in {"|", ">"}:
                run_indent = indent
            else:
                found.append(
                    f"{name}:{number}: strict YAML syntax allows block scalars only for run"
                )
        if " #" in line and not re.fullmatch(
            r" *-? *uses: +[^#\s]+@[0-9a-fA-F]{40} +# v[^\s]+", line
        ):
            found.append(f"{name}:{number}: strict YAML syntax forbids inline comments")
    return found


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
    return ENV_NAME in job.lower()


def app_token_permissions(job: str) -> list[set[tuple[str, str]]]:
    """The permission-* inputs of every create-github-app-token step, one set per step."""
    lines = job.splitlines()
    result: list[set[tuple[str, str]]] = []
    for index, line in enumerate(lines):
        if not _is_token_reference(line) or line.lstrip().startswith("#"):
            continue
        dash = line.find("- ")
        indent = dash if dash >= 0 and line[:dash].strip() == "" else None
        body = [line]
        for follow in lines[index + 1 :]:
            if indent is not None and re.match(rf"^ {{{indent}}}- ", follow):
                break
            body.append(follow)
        step = "\n".join(body)
        result.append(
            set(
                re.findall(
                    r"['\"]?(permission-[a-z-]+)['\"]?\s*:\s*['\"]?([^,}\s'\"]+)",
                    step,
                    re.I,
                )
            )
        )
    return result


def _is_token_reference(text: str) -> bool:
    return "create-github-app-token" in text.lower() or APP_TOKEN_SHA in text.lower()


def problems(name: str, text: str) -> list[str]:
    """Every rule violation in one workflow file, as short messages."""
    found = strict_syntax_problems(name, text) if _is_subject(text) else []
    mentions = ENV_NAME in text or "TRUSTED_CHECKS_" in text or _is_token_reference(text)
    if not mentions:
        return found
    extra = triggers(text) - MAIN_ONLY_TRIGGERS
    if extra:
        found.append(f"{name}: references {ENV_NAME} but has triggers {sorted(extra)}")
    if re.search(r"&[A-Za-z0-9_-]+|\*[A-Za-z0-9_-]+|(?:^|\s)<<\s*:", text):
        found.append(f"{name}: YAML anchors or aliases may hide trusted token configuration")
    for job_name, job in jobs(text).items():
        in_env = uses_trusted_env(job)
        if "TRUSTED_CHECKS_" in job and not in_env:
            found.append(f"{name}:{job_name}: uses TRUSTED_CHECKS_* outside the environment")
        if not in_env:
            continue
        if re.search(r"^\s*(ref|repository):", job, re.M):
            found.append(f"{name}:{job_name}: environment job checks out PR content")
        if _is_token_reference(job) and not APP_TOKEN_ACTION.search(job):
            found.append(f"{name}:{job_name}: create-github-app-token is not pinned to a SHA")
        if _is_token_reference(job) and re.search(
            r"(?:^|\n)\s*(?:['\"]?INPUT_[A-Za-z0-9_-]+['\"]?)\s*:", job
        ):
            found.append(f"{name}:{job_name}: token step uses INPUT_* environment keys")
        if _is_token_reference(job) and re.search(
            r"['\"](?:uses|permission-[a-z-]+)['\"]\s*:", job, re.I
        ):
            found.append(
                f"{name}:{job_name}: quoted permissions or uses key in token configuration"
            )
        if _is_token_reference(job) and re.search(
            r"&[A-Za-z0-9_-]+|\*[A-Za-z0-9_-]+|(?:^|\s)<<\s*:", job
        ):
            found.append(f"{name}:{job_name}: token configuration uses YAML anchors or aliases")
        # Old rule, kept: every permission-* anywhere in the job must be exactly the allow-list.
        job_wide = set(re.findall(r"^\s*(permission-[a-z-]+):\s*(\S+)", job, re.M | re.I))
        if job_wide and job_wide != ALLOWED_APP_PERMISSIONS:
            found.append(f"{name}:{job_name}: job-wide App token permissions {sorted(job_wide)}")
        # New rule, added: each token step on its own is non-empty and exactly the allow-list.
        for perms in app_token_permissions(job):
            if perms != ALLOWED_APP_PERMISSIONS:
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
    # Without explicit permission-* inputs the token action inherits every installed permission.
    assert app_token_permissions(publish) == [{("permission-checks", "write")}]
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in publish
    context = EXPECTED[name]
    # The old commit status stays until the ruleset is switched; then a separate PR drops it.
    assert f'-f context="{context}"' in publish
    assert f'-f name="{context}"' in publish
    # Only an admin-set repository variable turns the legacy status off (switch and rollback).
    assert publish.count("if: vars.TRUSTED_CHECKS_LEGACY_STATUS != 'off'") == 1
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
        (GOOD.replace("          permission-checks: write\n", ""), "permissions"),
        (GOOD.replace("permission-checks: write", "permission-statuses: write"), "permissions"),
        (GOOD.replace("permission-checks: write", "permission-checks: read"), "permissions"),
        (
            GOOD
            + "      - uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc4"
            "5510\n        with:\n          private-key: x\n",
            "permissions",
        ),
        (
            GOOD.replace(
                "permission-checks: write",
                "permission-checks: write\n          permission-contents: write",
            ),
            "permissions",
        ),
        (
            GOOD
            + "      - uses: Actions/Create-GitHub-App-Token@64ab729a6294000f5aa4a4bc0b73d1f32bc4"
            "5510\n        with:\n          private-key: x\n",
            "permissions",
        ),
        (
            GOOD
            + "      - uses: Actions/Create-GitHub-App-Token@64ab729a6294000f5aa4a4bc0b73d1f32bc4"
            "5510\n        with:\n          permission-checks: write\n"
            "          permission-contents: write\n",
            "permissions",
        ),
        (
            GOOD.replace(
                "actions/create-github-app-token@64ab", "Actions/Create-GitHub-App-Token@v3 #"
            ),
            "not pinned",
        ),
        (
            GOOD.replace(
                "permission-checks: write",
                "permission-checks: write\n          Permission-Contents: write",
            ),
            "permissions",
        ),
        (
            GOOD.replace(
                "        with:\n          private-key:",
                "        with: {permission-checks: write, permission-contents: write}\n"
                "        # private-key below remains present\n          private-key:",
            ),
            "permissions",
        ),
        (
            GOOD.replace(
                "          permission-checks: write",
                '          "permission-contents": write',
            ),
            "permissions",
        ),
        (
            GOOD.replace(
                "      - uses: actions/create-github-app-token@",
                '      - "uses": "actions/create-github-app-token@',
            ).replace(" # v3", '" # v3'),
            "quoted permissions or uses key",
        ),
        (
            GOOD.replace(
                "        with:\n          private-key:", "        with: &w\n          private-key:"
            ),
            "anchors or aliases",
        ),
        (
            GOOD.replace(
                "          permission-checks: write",
                "          permission-checks: write\n        env:\n"
                "          INPUT_PERMISSION_CONTENTS: write",
            ),
            "INPUT_*",
        ),
        (
            GOOD.replace(
                "actions/create-github-app-token@",
                "example/other-action@",
            ),
            "not pinned to a SHA",
        ),
        (
            GOOD.replace(
                "  analyze:\n",
                "  analyze:\n    env:\n      K: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}\n",
            ),
            "outside the environment",
        ),
        (
            GOOD.replace(
                "          permission-checks: write",
                '          "permission\\x2Dcontents": write',
            ),
            "strict YAML syntax",
        ),
        (
            GOOD.replace(
                "    environment: trusted-checks\n",
                "    environment: trusted-checks\n    env: {INPUT_PERMISSION-CONTENTS: write}\n",
            ),
            "strict YAML syntax",
        ),
        (
            GOOD.replace("jobs:\n", "env:\n  INPUT_PERMISSION-CONTENTS: write\njobs:\n"),
            "strict YAML syntax",
        ),
        (
            GOOD.replace(
                "          permission-checks: write",
                "          ? permission-contents\n          : write",
            ),
            "strict YAML syntax",
        ),
        (
            GOOD.replace(
                "    environment: trusted-checks\n",
                "    environment: trusted-checks # comment\n",
            ),
            "strict YAML syntax",
        ),
    ],
)
def test_checker_flags_each_boundary_break(bad: str, needle: str) -> None:
    found = problems("bad.yml", bad)
    assert any(needle in item for item in found), found
