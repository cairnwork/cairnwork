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
LINE = re.compile(r"^(Worker|Requested-Model|Observed-Model): (.*)$")


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
    """The three fields from the final trailer block of a commit message (after its last blank
    line). A key that is missing, malformed or given twice there is unknown: nothing picks
    between duplicates, and a trailer earlier in the message is never read."""
    block = message.rstrip().rsplit("\n\n", 1)[-1] if "\n\n" in message.rstrip() else ""
    seen: dict[str, list[str]] = {}
    for line in block.splitlines():
        found = LINE.match(line)
        if found:
            seen.setdefault(found.group(1), []).append(found.group(2))
    return {key: clean(seen[label][0]) if len(seen.get(label, ())) == 1 else UNKNOWN
            for key, label in KEYS}  # fmt: skip
