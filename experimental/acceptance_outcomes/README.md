# Acceptance follow-up ledger (experimental, default off)

Owner and expiry: see OWNER and EXPIRES. Step 1: `record`, `event`, `report`.

It links each acceptance call to what was found later. It never changes a verdict and is not a
measure of false completion: a rate is only meaningful with its denominator and coverage, which
the report always prints.

    python -m experimental.acceptance_outcomes record --ledger F --result verify.json \
      --repo R --base B --candidate C --version V [--test-commit T] [--notary-db DB | --notary-lost REASON]
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

A result that reached Core (VERIFIED, FAILED, or UNCOMPARABLE/REFUSED whose output carries a
non-empty `core_status`) needs its notary row: without one `record` refuses it with
`OUTCOME_RECORD_MISMATCH`. `no_notary_row` is only for results before Core (a pre-Core error, or
REFUSED/UNCOMPARABLE with an empty `core_status`). If the notary database is gone, pass
`--notary-lost REASON` (and no `--notary-db`): the line is marked `notary_lost` with the reason,
counts as reached Core, and the report prints "unchecked binding N" next to each rate. Such lines
are outside every rate (numerator, denominator and coverage) and are listed per verdict under
`cases notary_lost` (#809). The flag is refused for a pre-Core result or together with a database. Lines written
before this change keep their format and still read; to fix an old ledger start a new file.
`core_status` is taken as given from the result JSON; without `--notary-db` there is no way to
check whether it was altered.

One notary row backs one call (#809): a second call for the same task and attempt that is bound
to a row is refused with `OUTCOME_DUPLICATE_ROW`, even with another reason or `call_ref`. A
VERIFIED line is bound only when the notary verdict itself is verified for the candidate
(`verify_record`); a row whose raw status says VERIFIED but whose verdict does not is refused
with `OUTCOME_RECORD_MISMATCH`.

When present, result candidate, binding base and test commit, and repo identities are checked against
the CLI inputs before recording, including with `--notary-lost`.
