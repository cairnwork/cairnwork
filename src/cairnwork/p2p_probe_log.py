"""Helper for reading the sanitized tail of a P2P base-probe log."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
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
# The longest matching alternative in _KEYS is 'secret_access_key' (17 chars); a longer suffix
# can never match _KEYS.
_MAX_KEY_LEN = len("secret_access_key")
# colon form (#402): 'token: x', 'DB-PASSWORD : x', '"password": "x"'; value to line end
_COLON = re.compile(rf"(?im)((?<!\w)(?:[\w-]{{0,64}}[_-])?(?:{_KEYS})[\"']?[ \t]*:[ \t]*)[^\n]+")
# '--password x' with a space (#402): a flag whose name ends in a secret word, and its next word
_FLAG_WORDS = r"(?:token|password|passwd|pwd|secret|api[-_]?key|private[-_]key)"
_FLAG = re.compile(rf"(?i)((?<![\w-])--[\w-]{{0,64}}{_FLAG_WORDS}[ \t]+)(?!-)\S+")
_FLAG_HEAD = re.compile(r"(?<![\w-])--([\w-]{0,64})$")  # the flag name up to a name's start
# a URL-encoded 'name%3Dvalue' (#402)
_ENCODED = re.compile(rf"(?i)((?<!\w)[\w-]{{0,64}}(?:{_KEYS})%3d)[^&\s]+")
_HID = "\x00"  # stands for a hidden character until the field pass has read the name (#744)
# a name: a run of word characters and markers, not after a word character or a marker; only the
# name is consumed, so a secret field inside another field's value is still found. After '-' a name
# counts only when it holds a marker (as '?' started one there before #744; my-token stays shown)
_NAME = re.compile(rf"(?<![\w{_HID}])([\w{_HID}\uFF3F]+)=")
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
    names in colon form, a value after ``%3A``, a value starting with '-' after ``--flag``, spaces
    around '=', ``Authorization=`` with a non-Bearer value, a value on the next line,
    ``api-key :``, scp-style ``user:pass@host:`` and bare high-entropy strings.
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
    return mask_secrets(raw.decode("utf-8", errors="replace"))[-TAIL_CHARS:]


_SPLIT_RUNS = re.compile(r"([ \t\n]+)")


def _mask_userinfo(text: str) -> str:
    """Remove URL credentials (up to the last '@' of a whitespace-free run) in one pass."""
    parts = _SPLIT_RUNS.split(text)
    for i in range(0, len(parts), 2):
        run = parts[i]
        idx = run.find("://")
        if idx != -1:
            after_idx = idx + 3
            last_at = run.rfind("@")
            if last_at >= after_idx:
                parts[i] = run[:after_idx] + run[last_at + 1 :]
    return "".join(parts)


def mask_secrets(text: str) -> str:
    """``text`` without ANSI codes, hidden characters, URL credentials, or the masked forms listed
    in ``probe_log_tail``; the same rules for any text that may carry a secret (#814 argv)."""
    text = _mask_userinfo(
        replace_hidden(_ANSI.sub("", text).replace("\r\n", "\n"), _HID, keep="\n\t")
    )
    for rx in (_HEADER, _BEARER, _COLON, _FLAG, _ENCODED):
        text = rx.sub(r"\1<redacted>", text)
    return _mask_fields(text).replace(_HID, "?")


def _is_key(name: str) -> bool:
    """A secret word, or a name ending in one after '_' (GITHUB_TOKEN); full-width letters fold."""

    def _candidates(n: str) -> Iterator[str]:
        yield n
        pos = len(n)
        start = max(0, len(n) - 1 - _MAX_KEY_LEN)
        while True:
            pos = n.rfind("_", start, pos)
            if pos == -1:
                break
            yield n[pos + 1 :]

    for n in (name, name.casefold(), unicodedata.normalize("NFKC", name).casefold()):
        if any(_KEY.fullmatch(x) for x in _candidates(n)):
            return True
    return False


def _is_secret_name(text: str, start: int, name: str, names: tuple[str, str]) -> bool:
    if start > 0 and text[start - 1] == "-" and _HID not in name:
        flag = _FLAG_HEAD.search(text, max(0, start - 70), start)
        if flag is not None:  # a flag: --token=, --github-token=, --aws-secret-access-key=
            full = (flag.group(1) + name).replace("-", "_")
            return any(_is_key(n) for n in (*names, full))
        # a dashed name counts only as a whole known one: client-secret, api-key, x-auth-token
        word = re.search(r"\w{1,64}$", text[max(0, start - 65) : start - 1])
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
