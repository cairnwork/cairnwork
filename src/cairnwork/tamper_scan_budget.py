"""The work tamper_scan.py may do on one patch (#668), and the alias spelling it bounds (#665).

The per-line cap (``MAX_LINE``, #642) bounds one added line as written, but the patterns also read
the line with known aliases spelled out (``m`` bound to ``sys.modules`` reads as ``sys.modules``,
and spellings chain), Python's reading of the whole committed file is tokenized, unchanged
lines included, and many lines near the cap add up. So, counted in characters, not wall time:

* no text the patterns read, after alias spelling, is longer than ``MAX_LINE``;
* one alias table of a file holds at most ``MAX_ALIASES`` names (every bound alias costs a
  substitution over each later line that holds it);
* one patch reads at most ``MAX_READ`` characters, every reading of a line counted, and tokenizes
  at most ``MAX_TOKENIZED`` bytes of committed files, a row longer than ``MAX_LINE`` counted once
  per started ``MAX_LINE`` (on some Pythons tokenize time grows with the square of a row's length);
  every line piece, every reading and every row also costs one, empty or skipped, since each runs
  the rules or the tokenizer once whatever its length.

Past any of them the scan stops reading and the patch is refused with the finding ``scan_budget``:
the budget can only add a refusal, never let a refused change pass. ``canonical`` (``_canonical``
there) and ``_plain`` are moved here from tamper_scan.py; both now skip the substitution of a name
the text does not hold, a no-op (canonical_pytest likewise), so the result is the same (#665).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from cairnwork.tamper_scan_diff import MAX_LINE
from cairnwork.tamper_scan_pytest import canonical_pytest

SCAN_BUDGET = "scan_budget"
MAX_ALIASES = 64  # names in one alias table of a file, all kinds; honest code binds a handful
MAX_READ = 1 << 21  # characters read per patch, every reading counted
MAX_TOKENIZED = 1 << 24  # bytes of committed files tokenized per patch, long rows weighted
Table = dict[str, set[str]]  # alias names by what they are bound to (sys, os, modules, pytest...)


class ScanBudget:
    """What is left of one patch's budget; ``over`` stays True once any limit is passed."""

    def __init__(self, read: int = MAX_READ, tokenized: int = MAX_TOKENIZED) -> None:
        self.read_left, self.tokenized_left, self.over = read, tokenized, False

    def read(self, texts: Iterable[str], aliases: Table | None = None) -> bool:
        """Spend ``texts`` (and check the ``aliases`` they are read with); False when over."""
        lengths = [len(text) for text in texts]
        self.read_left -= sum(lengths) + len(lengths)
        many = aliases is not None and sum(map(len, aliases.values())) > MAX_ALIASES
        self.over = self.over or self.read_left < 0 or many or max(lengths, default=0) > MAX_LINE
        return not self.over

    def tokenize(self, data: bytes) -> bool:
        """Spend tokenizing ``data`` (a whole file); False when over."""
        rows = data.split(b"\n")
        self.tokenized_left -= sum(
            (len(row) + 1) * max(1, -(-len(row) // MAX_LINE)) for row in rows
        )
        self.over = self.over or self.tokenized_left < 0
        return not self.over

    def piece(self) -> bool:
        """Spend one line piece (``\\r``-separated), however short; False when over."""
        self.read_left -= 1
        self.over = self.over or self.read_left < 0
        return not self.over


def canonical(text: str, aliases: Table) -> set[str]:
    """The line with every known alias spelled as the plain name, so main's rules read it: as main
    does, then unwrapped with pytest's aliases last and first (a name bound twice reads as each)."""
    plain = _plain(text, aliases)
    unwrapped = {canonical_pytest(plain, aliases), _plain(canonical_pytest(text, aliases), aliases)}
    return {canonical_pytest(plain, aliases, unwrap=False), *unwrapped}


def _plain(text: str, aliases: Table) -> str:
    # Not after "." or a word character: obj.s.modules or ms.modules is not the alias s. A name
    # the text does not hold (as it is at that step) has nothing to substitute: skipped (#665).
    for name in sorted(aliases["sys"]):
        if name in text:
            text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*modules\b)", r"sys\1", text)
    for name in sorted(aliases["os"]):
        if name in text:
            text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*_exit\b)", r"os\1", text)
    for name in sorted(aliases["gc"]):
        if name in text:
            text = re.sub(rf"(?<![\w.]){re.escape(name)}(\s*\.\s*get_)", r"gc\1", text)
    for name in sorted(aliases["modules"]):
        if name in text:
            text = re.sub(rf"(?<![\w.]){re.escape(name)}\b", "sys.modules", text)
    for name in sorted(aliases["_exit"]):  # a call; "def _exit(self):" defines a method instead
        if name in text:
            text = re.sub(rf"(?<![\w.])(?<!def ){re.escape(name)}(\s*\()", r"os._exit\1", text)
    return text
