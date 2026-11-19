"""Every setup-uv step turns its cache off (#1511).

setup-uv v6 restores a main-scoped Actions cache by default on hosted runners; a job that then
runs PR code must not start from a cache a PR could have written. Explicit ``false`` everywhere
also keeps a later v5 -> v6 bump from switching the cache on.

PyYAML is not a dependency, so steps are split by indentation: a step runs from its ``- `` line
to the next line at the same or a shallower indent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))
_DASH = re.compile(r"^(\s*)-\s")
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*[\"']?astral-sh/setup-uv@", re.M)
_OFF = re.compile(r"^\s*(?:-\s+)?enable-cache:\s*[\"']?false[\"']?\s*(?:#.*)?$", re.M)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def steps(text: str) -> list[str]:
    """Return the text of every list item (a ``- `` line and its deeper lines)."""
    lines = text.split("\n")
    found: list[str] = []
    for i, line in enumerate(lines):
        match = _DASH.match(line)
        if not match:
            continue
        base = len(match.group(1))
        body = [line]
        for nxt in lines[i + 1 :]:
            if nxt.strip() and _indent(nxt) <= base:
                break
            body.append(nxt)
        found.append("\n".join(body))
    return found


def setup_uv_steps(text: str) -> list[str]:
    """Return the innermost list item that holds each setup-uv ``uses:`` line."""
    holders = [s for s in steps(text) if _USES.search(s)]
    return [s for s in holders if not any(o != s and o in s and _USES.search(o) for o in holders)]


def with_block(step: str) -> str:
    """Return the lines under the step's ``with:`` key (deeper than it), or ""."""
    lines = step.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^\s*(?:-\s+)?with:\s*$", line):
            base = _indent(line)
            body: list[str] = []
            for nxt in lines[i + 1 :]:
                if nxt.strip() and _indent(nxt) <= base:
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def cache_is_off(step: str) -> bool:
    return _OFF.search(with_block(step)) is not None


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_setup_uv_step_disables_the_cache(path):
    for step in setup_uv_steps(path.read_text()):
        assert cache_is_off(step), f"{path.name}: setup-uv without enable-cache: false\n{step}"


def test_the_workflows_do_use_setup_uv():
    assert sum(len(setup_uv_steps(p.read_text())) for p in WORKFLOWS) >= 9


GOOD = "      - uses: astral-sh/setup-uv@abc # v6\n        with:\n          enable-cache: false\n"
BAD = "      - uses: astral-sh/setup-uv@abc # v6\n        with:\n          python-version: '3.13'\n"
NEXT = "      - run: x\n"


def _all_off(text: str) -> bool:
    found = setup_uv_steps(text)
    return bool(found) and all(cache_is_off(s) for s in found)


@pytest.mark.parametrize(
    "text",
    [
        BAD + NEXT,
        "      - uses: astral-sh/setup-uv@abc # v5\n" + NEXT,
        BAD.replace("python-version: '3.13'", "enable-cache: true") + NEXT,
        BAD + GOOD,
        GOOD + BAD,
        BAD + "        env:\n          enable-cache: false\n",
        "      - uses: astral-sh/setup-uv@abc # v6\n        env:\n          enable-cache: false\n"
        "        with:\n          python-version: '3.13'\n",
        BAD.replace("uses: astral-sh/setup-uv@abc", 'uses: "astral-sh/setup-uv@abc"') + GOOD,
        BAD.replace("uses: astral-sh/setup-uv@abc", "uses: 'astral-sh/setup-uv@abc'") + GOOD,
        "      - name: Install uv\n        uses: astral-sh/setup-uv@abc\n        with:\n"
        "          python-version: '3.13'\n" + GOOD,
    ],
    ids=[
        "no-key",
        "no-with",
        "cache-on",
        "bad-then-good",
        "good-then-bad",
        "false-under-env-after-with",
        "false-under-env-before-with",
        "double-quoted-uses",
        "single-quoted-uses",
        "named-step-then-good",
    ],
)
def test_a_setup_uv_step_that_leaves_the_cache_on_is_found(text):
    assert not _all_off(text)


@pytest.mark.parametrize(
    "text",
    [
        GOOD + NEXT,
        GOOD.replace("false", '"false"') + NEXT,
        "      - name: Install uv\n        uses: astral-sh/setup-uv@abc\n        with:\n"
        "          enable-cache: false\n" + NEXT,
        NEXT,
    ],
    ids=["plain", "quoted-false", "named-step", "no-setup-uv-at-all"],
)
def test_steps_that_disable_it_pass(text):
    assert _all_off(text) or not setup_uv_steps(text)
