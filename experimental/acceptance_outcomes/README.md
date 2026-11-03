# Acceptance follow-up ledger (experimental, default off)

Owner and expiry: see OWNER and EXPIRES. Step 1: `record`, `event`, `report`.

It links each acceptance call to what was found later. It never changes a verdict and is not a
measure of false completion: a rate is only meaningful with its denominator and coverage, which
the report always prints.

    python -m experimental.acceptance_outcomes record --ledger F --result verify.json \
      --repo R --base B --candidate C --version V [--test-commit T] [--notary-db DB]
    python -m experimental.acceptance_outcomes event --ledger F --call ID --type \
      observed|finding|confirmation|closed ...
    python -m experimental.acceptance_outcomes report --ledger F [--cases] [--json]

The ledger path is always passed by the caller; no Cairnwork command reads or writes it. Each line
carries `prev` (sha256 of the previous line) and `F.head` holds the last digest, so an edited or
removed line is refused with `OUTCOMES_NOT_APPEND_ONLY` and the report prints nothing. This shows
accidental edits, not a deliberate rewrite of both files. Confirmer identity is a placeholder
(different id, not a declared clone) until #772.

Recovery: a crash between the append and the head update leaves the head one line behind; the
next call is refused with `OUTCOMES_NOT_APPEND_ONLY` (fail closed) until an operator checks the
last line and rewrites `F.head` with its sha256. Deleting both the ledger and its head gives an
empty report (a stated limit). `unknown` (window closed without an observed window or without
the agreed checks) is counted separately and is outside the rate's denominator and coverage.
