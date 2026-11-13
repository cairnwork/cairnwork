"""Issue #1145: gitcmd.stderr_tail must cost linear time and keep its redaction exactly."""

from __future__ import annotations

import random
import re
import time

import pytest

from cairnwork import gitcmd
from cairnwork.textclean import replace_hidden

_OLD = re.compile(r"(?<=://)[^ \t]*@")  # the pre-fix regex, quadratic


def _old_stderr_tail(raw: str) -> str:
    text = _OLD.sub("", replace_hidden(raw or "", "", keep="\n")).strip()
    return text.replace("\n", " | ")[-gitcmd.STDERR_TAIL_CHARS :]


@pytest.mark.parametrize(
    "raw",
    [
        "fatal: https://user:tok@github.com/o/r.git",
        "fatal: https://u:p@ss@host/x",
        "a://b@c d://e f@g",
        "https://u:t\n@host/x",  # newline inside the userinfo does not split it
        "https://u:t@h\nhttps://v:s@h2",
        "x://a://b@c",
        "@://a\n://b@\t://@",
        "no credentials here",
        "ssh://git@host/path and https://h/p@q",
        "://",
        "@",
        "://@",
        "a@b://c",
        "",
        "\n\n",
    ],
)
def test_redaction_equivalent_to_old_regex(raw: str) -> None:
    assert gitcmd.stderr_tail(raw) == _old_stderr_tail(raw)


def test_random_strings_equivalent_to_old_regex() -> None:
    rng = random.Random(1145)
    alphabet = ["://", "@", " ", "\t", "\n", "a", "b", "x:y", "/"]
    for _ in range(3000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert gitcmd.stderr_tail(raw) == _old_stderr_tail(raw), repr(raw)


@pytest.mark.parametrize(
    "unit",
    ["a://", "a://\n", "://\n"],
)
def test_cost_is_bounded_on_a_long_stderr(unit: str) -> None:
    raw = unit * (64 * 1024 // len(unit))
    gitcmd.stderr_tail("://")  # warm up
    t0 = time.monotonic()
    gitcmd.stderr_tail(raw)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"64 KiB of {unit!r} took {elapsed:.3f}s"
