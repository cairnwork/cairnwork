"""Acceptance casebook (experimental, default off): an append-only, hash-chained list of acceptance
mistakes. Nothing reads or writes it unless its command is called. See store.py."""

from experimental.acceptance_casebook.store import CasebookError, add_case, list_cases

__all__ = ["CasebookError", "add_case", "list_cases"]
