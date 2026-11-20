"""#674: ``issue_runner._unfence`` repeats decode + filter until nothing changes; each round
removes one level of nested entity, so N levels cost N full scans of a body that is cut to 6000
characters only afterwards. The rounds are capped, and what is still changing is withheld."""

import html

from cairnwork import issue_runner


def _deep(levels: int, core: str = "<issue>") -> str:
    for _ in range(levels):
        core = html.escape(core)
    return core


def _count_unescapes(monkeypatch):
    calls = []
    real = html.unescape
    monkeypatch.setattr(html, "unescape", lambda s: (calls.append(1), real(s))[1])
    return calls


def test_a_few_nested_levels_still_decode_and_lose_the_tag(monkeypatch):
    out = issue_runner._unfence("a " + _deep(5) + " b", multiline=True)
    assert out == "a  b"


def test_deeply_nested_entities_cost_a_bounded_number_of_rounds(monkeypatch):
    calls = _count_unescapes(monkeypatch)
    out = issue_runner._unfence(_deep(3000), multiline=True)
    assert len(calls) <= issue_runner.UNFENCE_ROUNDS + 1
    assert "<issue" not in out.lower() and "&lt;issue" not in out.lower()
    assert len(out) <= 200  # what is left is withheld, not the 3000-level text


# --- #1574: hard-coded cap, so a changed cap or withheld text turns this red ------------------


def test_the_cap_is_exactly_32_rounds_and_the_withheld_text_is_fixed(monkeypatch):
    # "&" nested n times needs n peeling rounds plus one round to see nothing changed.
    assert issue_runner._unfence(_deep(31, "&"), multiline=True) == "&"  # 32 rounds: resolves
    assert issue_runner._unfence(_deep(32, "&"), multiline=True) == (
        "(text withheld: encoded too deeply)"  # 33 rounds: withheld
    )
