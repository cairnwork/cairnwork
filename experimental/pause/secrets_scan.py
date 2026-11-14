"""Secret and host identity scanning for PAUSE requests (#476 step 2)."""

from __future__ import annotations

import re

# abbreviations that make a following key word a credential name even inside a longer word
# (myAPIKey, xAPIKEY, clientapitoken, dbPassword); plain words such as monkey stay unblocked.
_ABBRS = "api|aws|ssh|jwt|oauth|auth|access|client|private|secret|db"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(
            r"(?:sk-ant-|sk-|gh[pousr]_|github_pat_|AKIA|ASIA|AIza|xox[abpres]-)[A-Za-z0-9_\-]{12,}"
        ),
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    # NAME_KEY=..., GITHUB_TOKEN: "...", "client_secret": "...", key => "...", TOKEN := ...
    # (separator = : => :=, optional quotes or backticks around name and value)
    (
        "assigned_secret",
        re.compile(
            r"(?:(?<![A-Za-z0-9])(?i:apikey|key|token|secret|passw(?:or)?d|pass|passphrase)"
            r"|(?<=[A-Za-z0-9])(?:Key|Token|Secret|Passw(?:or)?d|Pass|Pwd|Passphrase)"
            r"|(?<=[_-])(?i:pwd)"
            r"|(?i:(?:" + _ABBRS + r")(?:key|token|secret|passw(?:or)?d))"
            r"|(?i:token|secret|passw(?:or)?d|passphrase))"
            r"[\"'`]?\s*(?:=>|:=|[=:])\s*[\"'`]?[A-Za-z0-9+/=_-]{16,}"
        ),
    ),
    # pwd is also the shell's working directory: block pwd=VALUE only when VALUE is not a path
    (
        "pwd_secret",
        re.compile(r"(?i:pwd)[\"'`]?\s*(?:=>|:=|[=:])\s*[\"'`]?(?![/~.])[A-Za-z0-9+/=_-]{16,}"),
    ),
    # command line: --api-key VALUE (space separated flag value)
    (
        "flag_secret",
        re.compile(
            r"(?i:--(?:[a-z0-9_-]*(?:apikey|key|token|secret|passw(?:or)?d|passphrase)"
            r"|[a-z0-9_-]*pwd|(?:[a-z0-9_-]*[_-])?pass))\s+[A-Za-z0-9+/=_-]{16,}"
            r"|--[A-Za-z0-9_-]*[a-z0-9](?:Pass|Pwd)\s+[A-Za-z0-9+/=_-]{16,}"
        ),
    ),
    # host paths: /Users/x, /home/x, /root/, C:\Users\x (any case)
    (
        "host_path",
        re.compile(r"(?i)(?:/(?:users|home)/[^/\s]+|/root(?:/|\b)|[a-z]:\\users\\[^\\\s]+)"),
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)


def scan(text: str, names: tuple[str, ...] = ()) -> list[str]:
    """Return matched categories only; never include the matched text. ``names`` are configured
    account or host names (AC4): a whole-word, case-insensitive hit is ``account_name``."""
    hits = [name for name, pattern in _PATTERNS if pattern.search(text)]
    for name in names:
        word = r"(?i)(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
        if name and re.search(word, text):
            hits.append("account_name")
            break
    return hits


class Scanner:
    """Injectable wrapper for secret scanning; ``names``: configured account/host names."""

    def __init__(self, names: tuple[str, ...] = ()) -> None:
        self.names = tuple(names)

    def scan(self, text: str) -> list[str]:
        return scan(text, self.names)
