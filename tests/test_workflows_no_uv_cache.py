"""Every setup-uv step turns its cache off (#1511).

setup-uv v6 restores a main-scoped Actions cache by default on hosted runners; a job that then
runs PR code must not start from a cache a PR could have written. Explicit ``false`` everywhere
also keeps a later v5 -> v6 bump from switching the cache on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def setup_uv_blocks(text: str) -> list[str]:
    """Return the text of each setup-uv step up to the next step or dedent."""
    lines = text.split("\n")
    blocks: list[str] = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*(?:-\s+)?uses:\s*astral-sh/setup-uv@", line):
            indent = len(line) - len(line.lstrip())
            body = [line]
            for nxt in lines[i + 1 :]:
                stripped = nxt.strip()
                if (
                    stripped
                    and len(nxt) - len(nxt.lstrip())
                    <= indent - (2 if line.lstrip().startswith("-") else 0)
                    and (nxt.lstrip().startswith("- ") or len(nxt) - len(nxt.lstrip()) < indent)
                ):
                    break
                body.append(nxt)
            blocks.append("\n".join(body))
    return blocks


def cache_is_off(block: str) -> bool:
    return re.search(r"^\s*enable-cache:\s*false\s*$", block, re.M) is not None


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_setup_uv_step_disables_the_cache(path):
    for block in setup_uv_blocks(path.read_text()):
        assert cache_is_off(block), f"{path.name}: setup-uv without enable-cache: false\n{block}"


def test_the_workflows_do_use_setup_uv():
    assert sum(len(setup_uv_blocks(p.read_text())) for p in WORKFLOWS) >= 8


def _step(with_lines: str = "") -> str:
    head = "      - uses: astral-sh/setup-uv@abc # v6\n"
    return head + with_lines + "      - run: x\n"


_W = "        with:\n"


@pytest.mark.parametrize(
    "text",
    [
        _step(_W + "          python-version: '3.13'\n"),
        _step(),
        _step(_W + "          enable-cache: true\n"),
    ],
    ids=["no-key", "no-with", "cache-on"],
)
def test_a_setup_uv_step_that_leaves_the_cache_on_is_found(text):
    assert not all(cache_is_off(b) for b in setup_uv_blocks(text))


def test_a_step_that_disables_it_passes():
    assert all(
        cache_is_off(b) for b in setup_uv_blocks(_step(_W + "          enable-cache: false\n"))
    )
