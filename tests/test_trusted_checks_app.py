"""Static guard for the trusted-checks GitHub App wiring (Issue #1444).

The App private key lives only in the deployment environment ``trusted-checks``, whose branch
policy allows ``main`` only. These checks keep the workflow side of that boundary: only workflows
whose every trigger runs with GITHUB_REF on the default branch may reference the environment or
its secrets, and the jobs that hold the key never check out PR content.
"""

from __future__ import annotations

import pytest

from tests.trusted_checks_rules import (
    APP_TOKEN_ACTION,
    ENV_NAME,
    EXPECTED,
    MAIN_ONLY_TRIGGERS,
    WORKFLOWS,
    _has_unclosed_quoted_scalar,
    app_token_permissions,
    jobs,
    problems,
    triggers,
    uses_trusted_env,
)


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


def test_non_subject_workflow_rejects_computed_environment_and_indexed_secret() -> None:
    attack = """\
name: unrelated
on: push
jobs:
  build:
    environment: ${{ format('trusted{0}checks', '-') }}
    steps:
      - run: echo ${{ secrets[format('TRUSTED_{0}', 'CHECKS_PRIVATE_KEY')] }}
"""
    assert problems("unrelated.yml", attack)


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        (
            GOOD.replace("  pull_request_target:", "    pull_request_target:"),
            "indentation",
        ),
        (
            GOOD.replace(
                "    environment: trusted-checks",
                "    environment: ${{ format('trusted{0}checks', '-') }}",
            ).replace(
                "secrets.TRUSTED_CHECKS_PRIVATE_KEY",
                "secrets[format('TRUSTED_{0}', 'CHECKS_PRIVATE_KEY')]",
            ),
            "plain word",
        ),
        (GOOD + '    name: "x\n  publish:\n    runs-on: y\n', "multi-line quoted"),
        (
            GOOD.replace(
                "    steps:\n",
                "    steps:\n      - run: |\n          echo safe\n        env: {A: b}\n",
            ),
            "flow values",
        ),
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


@pytest.mark.parametrize(
    ("line", "unclosed"),
    [
        ('    name: "x', True),
        ('    name: "x"', False),
        ("    name: 'x", True),
        ("    name: 'abc'", False),
        ("    name: 'it''s'", False),
        ("    name: 'it''s", True),
    ],
)
def test_quoted_scalar_must_close_on_its_line(line: str, unclosed: bool) -> None:
    assert _has_unclosed_quoted_scalar(line) is unclosed
