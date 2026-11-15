"""Secret and host identity scanning for PAUSE requests (#476 step 2)."""

from __future__ import annotations

import re

# Leak guard, recall first: a name (assignment or --flag) whose lower-cased form, with - and _
# removed, ends in a credential word is a secret name, glued prefixes included (masterkey,
# adminpass). The only exemption is this closed list of ordinary words. Domain names such as
# render_pass or compilePass stay blocked: an extra refusal is the safe side.
_SAFE_NAMES = frozenset(
    "monkey donkey turkey hockey jockey whiskey bypass compass overpass underpass trespass "
    "surpass keynote publickey primarykey sortkey partitionkey foreignkey cachekey "
    "idempotencykey nexttoken pagetoken".split()
)
_SUFFIX = re.compile(r"(?:key|token|secret|pass(?:word|wd|phrase)?|pwd)$")
_QUALIFIER = re.compile(r"(?:value|hash|old|new|prod|dev|test|base|pem|v\d+)$")
_WORD = re.compile(r"(?:key|token|secret|pass(?:word|wd|phrase)?|pwd)")
_PIECE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
# NAME_KEY=..., GITHUB_TOKEN: "...", "client_secret": "...", key => "...", TOKEN := ...
# (separator = : => :=, optional quotes or backticks around name and value); the name must
# start a word so each run of name characters is tried once
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z_][A-Za-z0-9_.]*(?:\s*\[\s*[\"'`][A-Za-z_][A-Za-z0-9_-]*[\"'`]\s*\])*\s*\[\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_-]*)[\"'`]?\s*\]?\s*(?:=>|:=|[=:])[ \t]*"
    r"(?=(?P<value>\"[^\"\n]{6,}\"|'[^'\n]{6,}'|`[^`\n]{6,}`|"
    r"(?=[^\s]*[^A-Za-z0-9_])[^\s]+|[A-Za-z0-9+/=_-]{16,}))"
)
# command line: --api-key VALUE or --api-key=VALUE
_FLAG = re.compile(
    r"(?<![A-Za-z0-9_-])--(?P<name>[A-Za-z_][A-Za-z0-9_-]*)(?:\s+|\s*=\s*)"
    r"(?=(?P<value>\"[^\"\n]{6,}\"|'[^'\n]{6,}'|`[^`\n]{6,}`|"
    r"(?=[^\s]*[^A-Za-z0-9_])[^\s]+|[A-Za-z0-9+/=_-]{16,}))"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(
            r"(?:sk-ant-|sk-|gh[pousr]_|github_pat_|AKIA|ASIA|AIza|xox[abpres]-)[A-Za-z0-9_\-]{12,}"
        ),
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    # host paths: /Users/x, /home/x, /root/, C:\Users\x (any case)
    (
        "host_path",
        re.compile(r"(?i)(?:/(?:users|home)/[^/\s]+|/root(?:/|\b)|[a-z]:\\users\\[^\\\s]+)"),
    ),
    ("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)


def scan(text: str, names: tuple[str, ...] = ()) -> list[str]:
    """Return matched categories only; never include the matched text. ``names`` are configured
    account or host names (AC4): a whole-word, case-insensitive hit is ``account_name``."""
    normalized = re.sub(r"[\u00ad\u200b-\u200d\u2060\u2061-\u2064\ufeff]", "", text)
    joined = re.sub(r"(=>|:=|[=:])[ \t]*(?:\r\n|\n)+[ \t]*", r"\1", normalized)
    compact = re.sub(r"\s+", "", normalized)
    wrapped = re.sub(r"\r?\n", "", normalized)
    hits = []
    for name, pattern in _PATTERNS:
        variants = (text, normalized)
        if name in {"api_key", "bearer_token"}:
            variants += (compact, wrapped)
        if any(pattern.search(variant) for variant in variants):
            hits.append(name)
    for pattern, category in ((_ASSIGNMENT, "assigned_secret"), (_FLAG, "flag_secret")):
        for candidate in (text, joined):
            for match in pattern.finditer(candidate):
                word = _secret_word(match.group("name"))
                value = match.group("value")
                if (
                    word is None
                    or (word == "pwd" and _is_path(value))
                    or re.fullmatch(r"[A-Za-z_]\w{0,14}\(\)", value)  # old 16-char floor kept
                ):
                    continue
                hits.append(category)
                break
            else:
                continue
            break
    for name in names:
        word = r"(?i)(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
        if name and (re.search(word, text) or re.search(word, normalized)):
            hits.append("account_name")
            break
    return hits


def _forms(word: str) -> tuple[str, ...]:
    """word without trailing digits, then also without a plural s or es."""
    word = word.rstrip("0123456789")
    return word, word.removesuffix("s"), word.removesuffix("es")


def _secret_word(name: str) -> str | None:
    """The credential word a name carries, or None. Plural and trailing digits are ignored. The
    glued name must end in the word; a separator or camelCase piece may be the word anywhere
    (token_value, DB_PASSWORD_PROD). The closed ordinary-word list exempts whole names."""
    glued = re.sub(r"[-_]", "", name).lower()
    forms = _forms(glued) + _forms(_QUALIFIER.sub("", glued, count=1))
    if any(f in _SAFE_NAMES for f in forms):
        return None
    for form in (glued, *forms):
        found = _SUFFIX.search(_QUALIFIER.sub("", form, count=1))
        if found:
            return found.group()
    for part in re.split(r"[-_]", name):
        for piece in _PIECE.findall(part):
            for form in _forms(piece.lower()):
                if _WORD.fullmatch(form):
                    return form
    return None


def _is_path(value: str) -> bool:
    """A pwd-named value starting with / is accepted as a path, even if it could be a secret."""
    value = value.strip("\"'`")
    return value.startswith(("/", "~", ".")) or bool(re.match(r"(?i)^[a-z]:[\\/]", value))


class Scanner:
    """Injectable wrapper for secret scanning; ``names``: configured account/host names."""

    def __init__(self, names: tuple[str, ...] = ()) -> None:
        self.names = tuple(names)

    def scan(self, text: str) -> list[str]:
        return scan(text, self.names)
