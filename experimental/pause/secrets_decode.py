"""Decoded views of a text, for the secret scan (#1556).

A secret that is escaped or encoded (URL, ``\\u``, HTML entity, base64, base32, hex) must be seen
as the secret it is. ``decoded_views`` returns the extra texts to scan. Every bound fails closed:
when one is hit it returns ``None`` and the caller refuses; it never skips. Limits: a text that
needs more than ``_DEPTH`` decoding layers, more than ``MAX_VIEWS`` views, a decoded view over
``MAX_VIEW_CHARS`` characters, ``MAX_CHARS`` characters in all, or an encoded run (base64 or
base32 characters; pieces joined by whitespace count as one run) longer than ``MAX_CHARS``, is
refused. Decoded bytes are cleaned by the scanner's own ``drop_invisible`` plus NFKC before
stretches are cut, so invisible, combining, control and look-alike characters inside an encoded
(also nested) secret do not hide it; whitespace that NFKC does not fold still splits it, as in
plain text. Encoded text of fewer than ``_MIN_SEGMENT`` characters is not decoded (too short to
tell from noise). Base64 cut into pieces shorter than 16 characters is not rejoined (MIME 64/76
columns are); any whitespace rejoins pieces. Known false refusals (fail closed, by design, rate
not measured): an encoded run of 32K+ characters (a long list of hashes or UUIDs, a hex dump, a
large data URI) and a decoded readable stretch of 4096+ characters.
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


def _segments(raw: bytes) -> list[str]:
    """The readable stretches of ``raw``, cut from the text as the scan sees it: decoded as UTF-8,
    cleaned of invisible characters and folded (NFKC) by the scanner's own function, so a secret
    split by any invisible character, or written in look-alike letters, is whole again. The plain
    ASCII stretches of the bytes are kept too (the union)."""
    text = raw.decode("utf-8", errors="replace")
    cleaned = drop_invisible(unicodedata.normalize("NFKC", drop_invisible(text)))
    found = [*_PRINTABLE.findall(cleaned), *_PRINTABLE.findall(raw.decode("latin-1"))]
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
