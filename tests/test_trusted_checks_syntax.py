"""Strict-subset and environment-name counterexamples for the trusted-checks guard (#1444)."""

from __future__ import annotations

import pytest

from tests.test_trusted_checks_app import GOOD
from tests.trusted_checks_rules import problems
from tests.trusted_checks_syntax import _has_unclosed_quoted_scalar


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


WORKFLOW_RUN_LEAK = """on:
  workflow_run:
    workflows: [ci]
jobs:
  leak:
    runs-on: ubuntu-latest
    environment:
           name: ${{ format('trusted{0}checks', '-') }}
    steps:
      - run: echo ${{ toJSON(secrets) }}
"""


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (WORKFLOW_RUN_LEAK, "environment name must be a plain word"),
        (WORKFLOW_RUN_LEAK.replace("${{ format('trusted{0}checks', '-') }}", "x"), "whole secrets"),
        (
            "on:\n  pull_request:\njobs:\n  a:\n"
            "    environment: Trusted-Checks\n    steps:\n      - run: echo hi\n",
            "triggers",
        ),
    ],
)
def test_checker_flags_deep_env_names_whole_secrets_and_case(text: str, needle: str) -> None:
    assert any(needle in problem for problem in problems("evil.yml", text))


REUSABLE_CALL = """on:
  pull_request_target:
    branches: [main]
jobs:
  call:
    uses: someone/repo/.github/workflows/x.yml@0123456789abcdef0123456789abcdef01234567
"""
WITH = "        with:\n          private-key"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (REUSABLE_CALL, "unreviewed remote reusable workflow"),
        (
            REUSABLE_CALL.replace("someone/repo", "James3014/Nexus-new").replace(
                "x.yml", "sibling-merge-checks.yml"
            ),
            "unreviewed remote reusable workflow",
        ),
        (
            REUSABLE_CALL.replace("someone/repo/.github", "./.github/../.github"),
            "unreviewed remote reusable workflow",
        ),
        (
            GOOD.replace(WITH, "        env:\n          private-key"),
            "outside the token private-key",
        ),
        (
            GOOD.replace(WITH, "        env:\n          with:\n            private-key"),
            "outside the token private-key",
        ),
    ],
)
def test_checker_flags_reusable_calls_and_key_outside_with(text: str, needle: str) -> None:
    assert any(needle in message for message in problems("bad.yml", text))


def test_local_reusable_workflow_call_is_allowed() -> None:
    local = REUSABLE_CALL.replace(
        "someone/repo/.github/workflows/x.yml@0123456789abcdef0123456789abcdef01234567",
        "./.github/workflows/ci.yml",
    )
    assert problems("ok.yml", local) == []


FAKE_TOKEN_IN_WITH_SCRIPT = """\
      - uses: someone/exfil@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        with:
          run: |
            - uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510
            permission-checks: write
          private-key: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}
"""
FAKE_TOKEN_IN_RUN_SCRIPT = """\
      - run: |
          cat <<'EOF' >/dev/null
          - uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510
          permission-checks: write
          EOF
          printenv PRIVATE-KEY
        env:
          PRIVATE-KEY: ${{ secrets.TRUSTED_CHECKS_PRIVATE_KEY }}
"""


@pytest.mark.parametrize(
    ("step", "needle"),
    [
        (FAKE_TOKEN_IN_WITH_SCRIPT, "outside the token private-key"),
        (FAKE_TOKEN_IN_WITH_SCRIPT, "unpinned or unlisted action"),
        (FAKE_TOKEN_IN_RUN_SCRIPT, "outside the token private-key"),
        (
            "      - uses: someone/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            "unpinned or unlisted action",
        ),
    ],
)
def test_run_script_text_never_counts_as_step_structure(step: str, needle: str) -> None:
    assert any(needle in message for message in problems("bad.yml", GOOD + step))
