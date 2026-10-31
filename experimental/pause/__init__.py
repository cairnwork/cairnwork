"""PAUSE (experimental, default off; #476): the local worker stops before its first edit, a
stronger model writes one plan, a gate model reviews it, and the worker resumes with it.

Not imported by the core. schema.py is the one source of reason codes and receipt field names;
receipt.py builds and verifies receipts; interfaces.py holds what later steps implement;
``python -m experimental.pause.cli run`` without a planner configuration is the bare
``local_worker run``."""
