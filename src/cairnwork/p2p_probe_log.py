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
    r"(?:access_|refresh_|id_|auth_|session_)?token|password|passwd|pwd|secret|client_secret|"
    r"api_?key"
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
# the pattern for the value that follows each form's name (group 1), read on the original text
_FORMS = (
    (_HEADER, re.compile(r"[^\n]+")),
    (_BEARER, re.compile(r"\S+")),
    (_COLON, re.compile(r"[^\n]+")),
    (_FLAG, re.compile(r"(?!-)\S+")),
    (_ENCODED, re.compile(r"[^&\s]+")),
)
_HID = "\x00"  # stands for a hidden character until the field pass has read the name (#744)
_LOW = "\ufe33\ufe34\ufe4d-\ufe4f\uff3f"  # non-ASCII low-line look-alikes that fold to '_' (#1313)
_LOW_RE = re.compile(f"[{_LOW}]")
# a name: a run of word characters and markers, not after a word character or a marker; only the
# name is consumed, so a secret field inside another field's value is still found. After '-' a name
# counts only when it holds a marker (as '?' started one there before #744; my-token stays shown)
_NAME = re.compile(rf"(?<![\w{_HID}{_LOW}])([\w{_HID}{_LOW}]+)[=⁼₌⩵⩶﹦＝]")
_VALUE = re.compile(r"[^&\s]+")
_MARKS = frozenset({"Mn", "Me"})  # combining marks, dropped from the matching view (#1329)
_IGNORABLE = "\u034f" + "".join(map(chr, range(0xFE00, 0xFE10)))  # CGJ, variation selectors
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
    for rx, value_rx in _FORMS:
        text = rx.sub(r"\1<redacted>", text)  # as written
        text = _redact(rx, value_rx, text)  # and as NFKC folds it (#1329): the union is masked
    return _mask_fields(text).replace(_HID, "?")


def _redact(rx: re.Pattern[str], value_rx: re.Pattern[str], text: str) -> str:
    """Mask what ``rx`` finds on the NFKC view of ``text`` without marks (#1329). The view only
    locates the name and separator (group 1); the value is read from the original characters with
    ``value_rx``, so normalization can neither shorten nor split it. Other characters stay."""
    view, idx = _view_index(text)
    out, pos = [], 0
    for m in rx.finditer(view):
        if m.end() == m.end(1):  # nothing after the name: no value to mask
            continue
        start = idx[m.end(1)] if idx else m.end(1)
        value = value_rx.match(text, start)
        if value is not None and start >= pos:
            out += [text[pos:start], "<redacted>"]
            pos = value.end()
    return "".join([*out, text[pos:]])


def _view_index(text: str) -> tuple[str, list[int]]:
    """The NFKC view used for matching, and each view character's index in ``text`` (empty for
    pure ASCII, where the two agree). Combining marks and variation selectors are left out of the
    view, so a mark inside or after a name no longer hides it."""
    if text.isascii():
        return text, []
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) in _MARKS or ch in _IGNORABLE:
            continue
        norm = unicodedata.normalize("NFKC", ch)
        out.append(norm)
        idx.extend([i] * len(norm))
    return "".join(out), idx


def _is_key(name: str) -> bool:
    """A secret word, or a name ending in one after '_' (GITHUB_TOKEN); NFKC + case folded."""
    name = unicodedata.normalize("NFKC", name)

    def _candidates(n: str) -> Iterator[str]:
        yield n
        pos = len(n)
        start = max(0, len(n) - 1 - _MAX_KEY_LEN)
        while True:
            pos = n.rfind("_", start, pos)
            if pos == -1:
                break
            yield n[pos + 1 :]

    for n in (name, name.casefold()):
        if any(_KEY.fullmatch(x) for x in _candidates(n)):
            return True
    return False


def _is_secret_name(
    norm: str, start: int, name: str, raw_name: str, names: tuple[str, str]
) -> bool:
    if start > 0 and norm[start - 1] == "-" and _HID not in name:
        flag = _FLAG_HEAD.search(norm, max(0, start - 70), start)
        if flag is not None:  # a flag: --token=, --github-token=, --aws-secret-access-key=
            full = (flag.group(1) + name).replace("-", "_")
            return any(_is_key(n) for n in (*names, full))
        # a dashed name counts only as a whole known one: client-secret, api-key, x-auth-token
        word = re.search(r"\w{1,64}$", norm[max(0, start - 65) : start - 1])
        dashed = word is not None and bool(_KEY.fullmatch(f"{word.group()}_{name}".casefold()))
        low_parts = _LOW_RE.split(raw_name, 1)
        low_suffix = len(low_parts) > 1 and _is_key(low_parts[1])
        return dashed or low_suffix
    return any(_is_key(n) for n in names)


def _nfkc_index(text: str) -> tuple[str, list[int]]:
    """Per-character NFKC text and the original index of each normalized character (#1313);
    marks are left out as in ``_view_index`` (#1329)."""
    return _view_index(text)


def _field_matches(source: str, idx: list[int] | None, text: str) -> Iterator[tuple[int, int]]:
    for m in _NAME.finditer(source):
        orig_start = idx[m.start()] if idx else m.start()
        orig_end = (idx[m.end() - 1] if idx else m.end() - 1) + 1
        name = m.group(1)
        raw_name = text[orig_start : orig_end - 1]
        names = (name.replace(_HID, ""), name.rsplit(_HID, 1)[-1])
        if _is_secret_name(source, m.start(), name, raw_name, names):
            yield orig_start, orig_end


def _mask_fields(text: str) -> str:
    """Mask the value of every secret-named field. A name counts with its hidden characters
    dropped, or by its last piece after one; case is ignored (as before #744, plus case folding)."""
    matches = list(_field_matches(text, None, text))
    if not text.isascii():
        norm, idx = _nfkc_index(text)
        matches.extend(_field_matches(norm, idx, text))
        matches.sort()
    out, pos = [], 0
    for orig_start, orig_end in matches:
        if orig_start < pos:
            continue
        value = _VALUE.match(text, orig_end)
        if value is not None:
            out += [text[pos:orig_end], "<redacted>"]
            pos = value.end()
    return "".join([*out, text[pos:]])
