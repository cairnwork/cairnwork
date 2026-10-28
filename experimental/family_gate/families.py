"""The fixed family table, the ```cairnwork-families annotation and the gate's verdict.

Pure functions, no I/O. A declared value is a family name or a model id; it maps to a family by
the table below (the family name itself, or a model id starting with one of its prefixes),
anything else is unknown. Unknown is "cannot compare": the Issue is neither refused nor counted
as a pass (#374, scope from the integration owner 2026-10-28).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

FAMILY_FENCE = re.compile(r"```cairnwork-families[ \t]*\n(.*?)\n```", re.DOTALL)
KEYS = ("issue_author", "test_author")
PREFIXES = {
    "anthropic": ("claude", "opus", "sonnet", "haiku", "fable"),
    "openai": ("gpt", "codex", "o1", "o3", "o4"),
    "google": ("gemini", "gemma"),
    "zhipu": ("glm",),
    "deepseek": ("deepseek",),
    "alibaba": ("qwen",),
    "meta": ("llama",),
    "mistral": ("mistral", "devstral"),
}
# A prefix must end at the value's end or before a separator, so "o3" is not "o3xyz-local"
# and "gptq-quantised-llama" is not openai.
_END = re.compile(r"$|[-_.:/ ]")

PASS, REFUSED, UNCOMPARABLE, NOT_ANNOTATED = "PASS", "REFUSED", "UNCOMPARABLE", "NOT_ANNOTATED"
SAME_AS = {
    "issue_author": "FAMILY_SAME_AS_ISSUE_AUTHOR",
    "test_author": "FAMILY_SAME_AS_TEST_AUTHOR",
}


def family(value: str | None) -> str | None:
    """The family of a declared value or model id; None when the table does not know it."""
    if not value:
        return None
    text = value.strip().lower()
    if text in PREFIXES:
        return text
    for name, prefixes in PREFIXES.items():
        for prefix in prefixes:
            if text.startswith(prefix) and _END.match(text, len(prefix)):
                return name
    return None


@dataclass(frozen=True)
class Annotation:
    declared: dict[str, str]  # only the keys the block has
    sha256: str  # of the block text; "" when there is no block
    invalid: bool = False


def parse_annotation(body: str) -> Annotation | None:
    """None without a block; invalid (never an exception) when the block is malformed."""
    blocks = FAMILY_FENCE.findall(body or "")
    if not blocks:
        return None
    sha = hashlib.sha256("\n".join(blocks).encode()).hexdigest()
    if len(blocks) != 1:
        return Annotation({}, sha, invalid=True)
    try:
        raw = json.loads(blocks[0])
    except ValueError:
        return Annotation({}, sha, invalid=True)
    if (
        not isinstance(raw, dict)
        or set(raw) - set(KEYS)
        or not all(isinstance(v, str) and v.strip() for v in raw.values())
    ):
        return Annotation({}, sha, invalid=True)
    return Annotation(dict(raw), sha)


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    families: dict[str, str | None]  # issue_author, test_author, worker


def decide(annotation: Annotation | None, worker_model: str) -> Verdict:
    worker = family(worker_model)
    declared = annotation.declared if annotation else {}
    families = {key: family(declared.get(key)) for key in KEYS} | {"worker": worker}
    if annotation is None:
        return Verdict(NOT_ANNOTATED, "FAMILY_BLOCK_ABSENT", families)
    if annotation.invalid:
        return Verdict(UNCOMPARABLE, "FAMILY_BLOCK_INVALID", families)
    if not declared:
        return Verdict(NOT_ANNOTATED, "FAMILY_BLOCK_EMPTY", families)
    if worker is None:
        return Verdict(UNCOMPARABLE, "WORKER_FAMILY_UNKNOWN", families)
    for key in KEYS:  # a same-family author refuses even when the other one is unknown
        if families[key] == worker:
            return Verdict(REFUSED, SAME_AS[key], families)
    if any(key in declared and families[key] is None for key in KEYS):
        return Verdict(UNCOMPARABLE, "AUTHOR_FAMILY_UNKNOWN", families)
    return Verdict(PASS, "FAMILY_DIFFERENT", families)
