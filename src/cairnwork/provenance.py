"""Who wrote a candidate, as Cairnwork can vouch for it (never a default, never Claude).

Three fields go into the candidate commit message as trailers and from there into the PR
body: the worker (the executor Cairnwork launched), the model Cairnwork requested (its own
config) and the model the dispatcher reported. Anything missing, odd or not vouched for is
``unknown``. The commit is bound by the notary, so the PR body reads the trailers back from
the commit instead of any file a worker could write."""

from __future__ import annotations

import re

UNKNOWN = "unknown"
KEYS = (("worker", "Worker"), ("requested_model", "Requested-Model"),
        ("observed_model", "Observed-Model"))  # fmt: skip
SUBJECT = "Cairnwork attempt "
UNKNOWN3 = {key: UNKNOWN for key, _ in KEYS}
VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,79}")


def clean(value: object) -> str:
    return value if isinstance(value, str) and VALUE.fullmatch(value) else UNKNOWN


def make(worker: object, requested: object, observed: object) -> dict[str, str]:
    return {"worker": clean(worker), "requested_model": clean(requested),
            "observed_model": clean(observed)}  # fmt: skip


def trailers(info: dict[str, str] | None) -> str:
    """The trailer block for a commit message (starts with a blank line)."""
    info = info if isinstance(info, dict) else {}
    return "\n\n" + "\n".join(f"{label}: {clean(info.get(key))}" for key, label in KEYS)


def parse(message: str) -> dict[str, str]:
    """The three fields from a message in exactly the shape Cairnwork writes (``trailers``,
    after the subjects of snapshot.py and deferred_tests.py). Stripping trailing newlines
    only (the ones git adds), it must be exactly five lines: a one-line subject that starts
    with ``Cairnwork attempt `` (so never a trailer line), an empty line, then
    ``Worker: v``, ``Requested-Model: v`` and ``Observed-Model: v`` in that order. Anything
    else (a longer subject, a body paragraph, an earlier block, extra blank lines or
    trailers, a trailer after, a blank line holding spaces) makes all three unknown, and so
    does a CR anywhere. This departs from git's trailer rules on purpose: nothing is
    normalised (CRLF included, Cairnwork never writes it), so a message is read only when it
    is exactly ours. Each value still goes through ``clean``."""
    if "\r" in message:
        return dict(UNKNOWN3)
    lines = message.rstrip("\n").split("\n")
    if len(lines) != 5 or not lines[0].startswith(SUBJECT) or lines[1] != "":
        return dict(UNKNOWN3)
    out: dict[str, str] = {}
    for (key, label), line in zip(KEYS, lines[2:], strict=True):
        if not line.startswith(f"{label}: "):
            return dict(UNKNOWN3)
        out[key] = clean(line[len(label) + 2 :])
    return out
