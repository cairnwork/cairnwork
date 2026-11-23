"""Decoded views of a text, for the secret scan (#1556).

A secret that is escaped or encoded (URL, ``\\u``, HTML entity, base64, base32, hex) must be seen
as the secret it is. ``decoded_views`` returns the extra texts to scan; it is bounded, and when a
bound is hit it returns ``None`` so the caller refuses (fail closed), never skips.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from urllib.parse import unquote

MAX_VIEWS = 64
MAX_CHARS = 256 * 1024
_DEPTH = 2
_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_B32 = re.compile(r"[A-Z2-7]{16,}={0,6}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_UNI = re.compile(r"(?:\\u|%u)([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})|\\x([0-9a-fA-F]{2})")


def _codepoint(match: re.Match[str]) -> str:
    try:
        return chr(int(next(g for g in match.groups() if g), 16))
    except (ValueError, OverflowError):  # beyond U+10FFFF: leave it as written
        return match.group(0)


def _printable(raw: bytes) -> str | None:
    """The text ``raw`` holds when it reads as text (UTF-8, at least 90% printable), else None."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) < 8 or sum(c.isprintable() or c.isspace() for c in text) < 0.9 * len(text):
        return None
    return text


def _decode_blobs(text: str) -> list[str]:
    found: list[bytes] = []
    for run in _B64.findall(text):
        std = run.rstrip("=").replace("-", "+").replace("_", "/")
        try:
            found.append(base64.b64decode(std + "=" * (-len(std) % 4), validate=True))
        except (binascii.Error, ValueError):
            pass
    for run in _B32.findall(text):
        try:
            found.append(base64.b32decode(run.rstrip("=") + "=" * (-len(run.rstrip("=")) % 8)))
        except (binascii.Error, ValueError):
            pass
    for run in _HEX.findall(text):
        found.append(bytes.fromhex(run))
    return [t for raw in found if (t := _printable(raw)) is not None]


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


def decoded_views(text: str) -> tuple[str, ...] | None:
    """Every distinct decoded form of ``text`` (nested up to two levels), or None when over a
    bound (too many views or too many decoded characters: refuse)."""
    seen, views, total = {text}, [], 0
    layer = [text]
    for _ in range(_DEPTH):
        nxt: list[str] = []
        for source in layer:
            for view in (*_unescape(source), *_decode_blobs(source)):
                if view in seen:
                    continue
                seen.add(view)
                total += len(view)
                if len(views) >= MAX_VIEWS or total > MAX_CHARS:
                    return None
                views.append(view)
                nxt.append(view)
        layer = nxt
    return tuple(views)
