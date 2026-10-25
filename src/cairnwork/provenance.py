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
VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,79}")
LINE = re.compile(r"^(Worker|Requested-Model|Observed-Model): (.*)$", re.MULTILINE)


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
    """The three fields from a commit message; each missing or malformed one is unknown."""
    found: dict[str, str] = {}
    for label, value in LINE.findall(message):
        found.setdefault(label, value)
    return {key: clean(found.get(label)) for key, label in KEYS}
