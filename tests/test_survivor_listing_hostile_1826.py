"""#1826: ``survivor_argv.listing()`` neutralises its own input, so any caller is safe: bounded
length, no control or hidden characters, no workflow-command prefix, non-UTF-8 tolerated."""

import pytest
from test_collect_reader import fake

from cairnwork import survivor_argv
from cairnwork.collect_reader import read_collected
from cairnwork.survivor_argv import Survivors, listing

HOSTILE = [
    "sh\nrow\r\x1b[31m\x00end",
    "::error::boom",
    "##[add-mask]x",
    "x" * 100_000,
    b"\xff\xfe sh \x80".decode("utf-8", "replace"),
    "a‮b​c d",
    "k[1];v:w",
]


def _safe(text: str) -> bool:
    return not any(c in text for c in "\n\r\x00\x1b‮​ ") and "::" not in text and "##[" not in text


@pytest.mark.parametrize("item", HOSTILE)
@pytest.mark.parametrize("field", ["argv", "reasons"])
def test_a_hostile_item_comes_out_neutralised_and_bounded(item, field):
    got = listing(Survivors(1, *((item,), ()) if field == "argv" else ((), (item,))))
    assert _safe(got) and len(got) < 200


def test_non_text_and_bytes_items_do_not_raise_or_leak():
    got = listing(Survivors(1, (b"sh \xff\n::x",), ("ok",)))  # type: ignore[arg-type]
    assert _safe(got) and "ok" in got
    assert _safe(listing(Survivors(1, (None, 7), ())))  # type: ignore[arg-type]


def test_many_items_are_capped():
    got = listing(Survivors(9, tuple(f"sh {i}" for i in range(500)), ("r",) * 500))
    assert len(got) < 800 and got.count("sh ") <= survivor_argv.MAX_LISTED


def test_a_survivors_look_alike_with_a_hostile_argv_is_neutralised():
    class Fake:
        argv = ("\n::error::x", "y" * 9999)
        reasons = ("\x1b[0m##[group]",)

    assert _safe(listing(Fake())) and len(listing(Fake())) < 400


@pytest.mark.parametrize(
    "survivors",
    [Survivors(2, ("sleep 30", "python -m svc"), ("alive",)), Survivors(1, ("sh <redacted>",))],
)
def test_ordinary_evidence_comes_out_unchanged(survivors):
    expected = {
        2: " (argv: sleep 30, python -m svc; reason: alive)",
        1: " (argv: sh <redacted>)",
    }[int(survivors)]
    assert listing(survivors) == expected


def test_a_hostile_argv_through_the_collect_reader_is_neutralised():
    run = fake(survivors=Survivors(1, ("\n::error::boom\x1b[31m" + "z" * 5000,), ("##[x]\n",)))
    why = read_collected(run).unusable
    assert why.startswith("collect-survivors (argv: ") and _safe(why) and len(why) < 300
