"""Strict-subset and environment-name counterexamples for the trusted-checks guard (#1444)."""

from __future__ import annotations

import pytest

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
