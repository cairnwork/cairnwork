"""Acceptance follow-up ledger (experimental, default off): every acceptance call, its later
independently confirmed outcome, and a report that prints coverage beside every rate. Nothing
reads or writes it unless its command is called. See store.py, ledger.py, report.py."""

from experimental.acceptance_outcomes.ledger import add_event, record_call
from experimental.acceptance_outcomes.report import build_report
from experimental.acceptance_outcomes.store import OutcomesError, read_ledger

__all__ = ["OutcomesError", "add_event", "build_report", "read_ledger", "record_call"]
