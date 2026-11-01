"""Core's rule for a list of ids (nexus-core ``_require_ids``): a non-empty list of distinct,
non-empty, normalized strings, each at most 512 UTF-8 bytes (Core's wire validator, product/
adapters ``_MAX_TEXT``, refuses a longer one; #453). Offline verify checks a response's ids with
it (#445) and intake checks a request's verifier ids with it before anything runs (#490)."""

from __future__ import annotations

from typing import Any

MAX_ID_BYTES = 512
ID_RULE = "ids must be distinct, non-empty, unpadded, without NUL and at most 512 UTF-8 bytes"


def _fits(text: str) -> bool:
    try:
        return len(text.encode("utf-8")) <= MAX_ID_BYTES
    except UnicodeEncodeError:  # a lone surrogate has no UTF-8 form; Core cannot encode it either
        return False


def core_ids(ids: Any) -> bool:
    """True when Core would accept ``ids`` as a required-id list."""
    return (
        isinstance(ids, list)
        and bool(ids)
        and all(type(v) is str and v and v == v.strip() and "\x00" not in v for v in ids)
        and all(_fits(v) for v in ids)
        and len(set(ids)) == len(ids)
    )
