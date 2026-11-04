"""Helper for reading the sanitized tail of a P2P base-probe log."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from cairnwork.textclean import replace_hidden

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # terminal colour and cursor codes, dropped whole
_URL_USERINFO = re.compile(r"(?<=://)[^ \t\n]*@")  # credentials in a URL, as gitcmd.stderr_tail
# header values (any scheme), Bearer tokens and secret-named key=value fields (#366); the value runs
# to the end of the line, the next whitespace, or '&', so trailing punctuation is masked with it
_HEADER = re.compile(
    r"(?im)(\b(?:(?:proxy-)?authorization|x-api-key|x-auth-token|private-token|cookie)"
    r"[\"']?[ \t]*:[ \t]*)[^\n]+"  # also a quoted key, and a space before the colon (#402)
)
_BEARER = re.compile(r"(?i)(\bbearer[ \t]+)\S+")
_KEYS = (
    r"(?:access_|refresh_|id_|auth_|session_)?token|password|passwd|pwd|secret|client_secret|api_?key"
    r"|secret_access_key|secret_key|private_key"
)
_KEY = re.compile(_KEYS, re.IGNORECASE)
# colon form (#402): 'token: x', 'DB-PASSWORD : x', '"password": "x"'; value to line end
_COLON = re.compile(rf"(?im)((?<!\w)(?:[\w-]*[_-])?(?:{_KEYS})[\"']?[ \t]*:[ \t]*)[^\n]+")
# '--password x' with a space (#402): a flag whose name ends in a secret word, and its next word
_FLAG = re.compile(
    r"(?i)((?<![\w-])--[\w-]*(?:token|password|passwd|secret|api-?key)[ \t]+)(?!-)\S+"
)
# a URL-encoded 'name%3Dvalue' (#402)
_ENCODED = re.compile(rf"(?i)((?<!\w)[\w-]*(?:{_KEYS})%3d)[^&\s]+")
_HID = "\x00"  # stands for a hidden character until the field pass has read the name (#744)
# a name: a run of word characters and markers, not after a word character or a marker; only the
# name is consumed, so a secret field inside another field's value is still found. After '-' a name
# counts only when it holds a marker (as '?' started one there before #744; my-token stays shown)
_NAME = re.compile(rf"(?<![\w{_HID}])([\w{_HID}]+)=")
_VALUE = re.compile(r"[^&\s]+")
TAIL_CHARS = 2048
# any character fits in 4 bytes; twice that, so the window holds more than the tail. A window that
# does not start the file drops its first, partial line: a character or URL cut there never reaches
# the tail, however much sanitizing shrinks the rest (#335)
_WINDOW_BYTES = 2 * 4 * TAIL_CHARS


def probe_log_tail(path: str | Path | None) -> str:
    """Return the last TAIL_CHARS characters of ``path`` sanitized, with URL credentials removed
    and the forms above masked, or ``(no log)`` on error/missing.

    Masked (#402): env-style names ending in a secret word (``GITHUB_TOKEN=``), ``--token=`` and
    ``--password x`` flags, ``client-secret=``/``api-key=``, colon forms (``token: x``), the headers
    Authorization, X-Api-Key, X-Auth-Token, Private-Token and Cookie (quoted keys too), ``%3D``
    encoded fields and full-width names in ``name=value`` form.

    Not masked: credentials split by a raw control byte (it becomes '?'), a quoted value with a
    space in it after ``=``, other dashed names (``my-token=``), full-width or hidden-character
    names in colon form, a value after ``%3A``, scp-style ``user:pass@host:`` and bare high-entropy
    strings.
    """
    if not path:
        return "(no log)"
    try:
        p = Path(path)
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - _WINDOW_BYTES)
            f.seek(start)
            raw = f.read(_WINDOW_BYTES)
    except OSError:
        return "(no log)"
    if start:  # URL credentials never span a newline; with no newline, nothing is known safe
        raw = raw.partition(b"\n")[2]
        if not raw:
            return f"[tail omitted: no complete line in the last {_WINDOW_BYTES // 1024} KB]"
    text = raw.decode("utf-8", errors="replace")
    text = _URL_USERINFO.sub(
        "", replace_hidden(_ANSI.sub("", text).replace("\r\n", "\n"), _HID, keep="\n\t")
    )
    for rx in (_HEADER, _BEARER, _COLON, _FLAG, _ENCODED):
        text = rx.sub(r"\1<redacted>", text)
    return _mask_fields(text).replace(_HID, "?")[-TAIL_CHARS:]


def _is_key(name: str) -> bool:
    """A secret word, or a name ending in one after '_' (GITHUB_TOKEN); full-width letters fold."""
    for n in (name, name.casefold(), unicodedata.normalize("NFKC", name).casefold()):
        pieces = [n, *(n[i + 1 :] for i, c in enumerate(n) if c == "_")]
        if any(_KEY.fullmatch(x) for x in pieces):
            return True
    return False


def _is_secret_name(text: str, start: int, name: str, names: tuple[str, str]) -> bool:
    if start > 0 and text[start - 1] == "-" and _HID not in name:
        if text[start - 2 : start] == "--":  # a flag: --token=, --client-secret=
            return any(_is_key(n) for n in names)
        # a dashed name counts only as a whole known one: client-secret, api-key, x-auth-token
        word = re.search(r"\w+$", text[: start - 1])
        return word is not None and bool(_KEY.fullmatch(f"{word.group()}_{name}".casefold()))
    return any(_is_key(n) for n in names)


def _mask_fields(text: str) -> str:
    """Mask the value of every secret-named field. A name counts with its hidden characters
    dropped, or by its last piece after one; case is ignored (as before #744, plus case folding)."""
    out, pos = [], 0
    for m in _NAME.finditer(text):
        name = m.group(1)
        names = (name.replace(_HID, ""), name.rsplit(_HID, 1)[-1])
        if m.start() < pos or not _is_secret_name(text, m.start(), name, names):
            continue
        value = _VALUE.match(text, m.end())
        if value is not None:
            out += [text[pos : m.end()], "<redacted>"]
            pos = value.end()
    return "".join([*out, text[pos:]])
