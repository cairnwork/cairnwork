"""Decoded views of a text, for the secret scan (#1556).

A secret that is escaped or encoded (URL, ``\\u``, HTML entity, base64, base32, hex) must be seen
as the secret it is. ``decoded_views`` returns the extra texts to scan. Every bound fails closed:
when one is hit it returns ``None`` and the caller refuses; it never skips. Limits: a text that
needs more than ``_DEPTH`` decoding layers, more than ``MAX_VIEWS`` views, a decoded view over
``MAX_VIEW_CHARS`` characters, ``MAX_CHARS`` characters in all, or an encoded run (base64 or
base32 characters; pieces joined by whitespace count as one run) longer than ``MAX_CHARS``, is
refused. A decoded stretch (ASCII, whitespace, invisible characters, letters NFKC folds to
ASCII; at least 16 visible characters once cleaned) is scanned as written, so an encoded
(also nested) secret gets the same result as the same plain text, except: a stretch over
``MAX_VIEW_CHARS`` is scanned in its cleaned form only, and a letter that NFKC does not fold
to ASCII (accented, non-Latin) ends a stretch, so a decoded secret is scanned only up to it.
Encoded text of fewer than ``_MIN_SEGMENT`` characters is not decoded (too short to tell from
noise). Base64 cut into
pieces shorter than 16 characters is not rejoined (MIME 64/76 columns are); any whitespace
rejoins pieces. Known false refusals (fail closed, by design, rate not measured): an encoded
run of 32K+ characters (a long list of hashes or UUIDs, a hex dump, a large data URI) and a
decoded readable stretch of 4096+ characters.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

from experimental.pause.secrets_clean import drop_invisible

MAX_VIEWS = 32
MAX_VIEW_CHARS = 4096  # the assignment and flag patterns cost more than linear per view
MAX_CHARS = 32 * 1024
_DEPTH = 4
_MIN_SEGMENT = 16
_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
# base64 cut into lines or words (each piece of 16 or more characters, the last of 4 or more)
_B64_WRAPPED = re.compile(
    r"(?<![A-Za-z0-9+/_-])(?:[A-Za-z0-9+/_-]{16,}\s+){1,}[A-Za-z0-9+/_-]{4,}={0,2}"
)
_B32 = re.compile(r"(?<![A-Za-z2-7])[A-Za-z2-7]{16,}={0,6}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_HEX_SEP = re.compile(r"(?:[0-9a-fA-F]{2}[:\s-]){7,}[0-9a-fA-F]{2}")
_UNI = re.compile(
    r"(?:\\{1,2}u|%u)([0-9a-fA-F]{4})|\\{1,2}U([0-9a-fA-F]{8})|\\{1,2}x([0-9a-fA-F]{2})"
    r"|\\{1,2}u\{([0-9a-fA-F]{1,6})\}"
)
_PRINTABLE = re.compile(r"[\x20-\x7e\r\n\t]{16,}")  # a readable stretch: 16 or more characters


def _codepoint(match: re.Match[str]) -> str:
    try:
        return chr(int(next(g for g in match.groups() if g), 16))
    except (ValueError, OverflowError):  # beyond U+10FFFF: leave it as written
        return match.group(0)


class _Refuse(Exception):
    """A bound was hit."""


_RUN_KEEP = 4  # invisible characters kept from one unbroken run (a run of zeros stays short)


def _pieces(text: str) -> list[str]:
    """The stretches of ``text`` made of characters the scan can see through: ASCII, whitespace,
    invisible characters and letters NFKC folds to ASCII. Anything else (replacement characters
    for binary bytes, other scripts) ends a stretch. The stretch is kept as written, so the scan
    treats it exactly like the same text in plain form."""
    out: list[str] = []
    piece: list[str] = []
    run = 0
    spaced = False  # the current run already kept a whitespace character
    for ch in text:
        if ch.isascii() or ch.isspace() or not drop_invisible(ch):
            seen = not ch.isprintable() or ch.isspace()
            run = run + 1 if seen else 0
            if not seen:
                spaced = False
            # past the cap a run still keeps its first whitespace: patterns need a separator
            if run <= _RUN_KEEP or (ch.isspace() and not spaced):
                piece.append(ch)
                spaced = spaced or ch.isspace()
        elif unicodedata.normalize("NFKC", ch).isascii():
            run = 0
            piece.append(ch)
        else:
            out.append("".join(piece))
            piece, run, spaced = [], 0, False
    out.append("".join(piece))
    return out


def _segments(raw: bytes) -> list[str]:
    """The readable stretches of ``raw`` (16 or more visible characters once cleaned and folded
    as the scanner does): each stretch as written, so the scan finds in it what it finds in the
    same plain text, and its cleaned form."""
    text = raw.decode("utf-8", errors="replace")
    found: list[str] = []
    for piece in _pieces(text):
        cleaned = drop_invisible(unicodedata.normalize("NFKC", drop_invisible(piece)))
        stretches = _PRINTABLE.findall(cleaned)
        if stretches:
            found += [piece, *stretches] if len(piece) <= MAX_VIEW_CHARS else stretches
    return list(dict.fromkeys(found))


def _b64_decode(run: str, offset: int) -> bytes | None:
    std = run[offset:].rstrip("=").replace("-", "+").replace("_", "/")
    std = std[: len(std) - len(std) % 4 or None] if len(std) % 4 == 1 else std
    try:
        return base64.b64decode(std + "=" * (-len(std) % 4))
    except (binascii.Error, ValueError):
        return None


def _decode_blobs(text: str) -> list[str]:
    """Readable text hidden in base64 (any alignment, split over lines), base32 or hex runs. Each
    run is decoded in full and every readable stretch of it is a view, wherever it sits."""
    raws: list[bytes] = []
    runs = [*_B64.findall(text)]
    runs += [re.sub(r"\s+", "", w) for w in _B64_WRAPPED.findall(text)]
    for run in runs:
        if len(run) > MAX_CHARS:  # refuse before decoding, so a huge run costs nothing
            raise _Refuse
        for offset in range(4):  # the run may start inside another word
            full = _b64_decode(run, offset)  # the whole run: junk bytes in front hide nothing
            if full is not None:
                raws.append(full)
    for run in _B32.findall(text):
        if len(run) > MAX_CHARS:
            raise _Refuse
        body = run.rstrip("=").upper()
        try:
            raws.append(base64.b32decode(body + "=" * (-len(body) % 8)))
        except (binascii.Error, ValueError):
            pass
    raws += [bytes.fromhex(r) for r in _HEX.findall(text)]
    raws += [bytes.fromhex(re.sub(r"[:\s-]", "", r)) for r in _HEX_SEP.findall(text)]
    return [seg for raw in raws for seg in _segments(raw)]


def _unescape(text: str) -> list[str]:
    """The text with escapes undone (URL-escape up to 3 rounds, \\u \\x entities)."""
    out, current = [], text
    if not any(c in text for c in "%\\&"):
        return out  # nothing that can be an escape
    for _ in range(3):
        step = html.unescape(_UNI.sub(_codepoint, unquote(current)))
        if step == current:
            break
        out.append(step)
        current = step
    return out


def decoded_views(sources: Iterable[str]) -> tuple[str, ...] | None:
    """Every distinct decoded form of the texts in ``sources`` (the text as written and its
    normalized forms), nested up to ``_DEPTH`` layers, or None when a bound is hit (refuse)."""
    layer = list(dict.fromkeys(sources))
    seen, views, total = set(layer), [], 0
    try:
        for _ in range(_DEPTH):
            nxt: list[str] = []
            for source in layer:
                # a whole-text unescape is as long as its source: it is not a decoded blob
                for view, blob in (
                    *((v, False) for v in _unescape(source)),
                    *((v, True) for v in _decode_blobs(source)),
                ):
                    if view in seen:
                        continue
                    seen.add(view)
                    if blob:
                        total += len(view)
                        if len(view) > MAX_VIEW_CHARS or total > MAX_CHARS:
                            raise _Refuse
                    if len(views) >= MAX_VIEWS:
                        raise _Refuse
                    views.append(view)
                    nxt.append(view)
            if not nxt:
                return tuple(views)
            layer = nxt
    except _Refuse:
        return None
    return None  # still decoding after the last allowed layer
