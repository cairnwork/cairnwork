# Acceptance casebook (experimental, default off)

Owner and expiry: see OWNER and EXPIRES. Step 1a: `add` and `list`.

This is an experimental record of acceptance mistakes, not a measure of false completion or
false green: a recorded case is not a confirmed one (independent confirmation is #772), and a
confirmed case is not evidence that an acceptance change helped (replay is #751 step 3).

    python -m experimental.acceptance_casebook add --casebook F --notary-db DB --task-id T \
      --attempt-id A --candidate C --base B --version V --kind accepted_bad|refused_good \
      --found-by WHO --evidence-file EV.json
    python -m experimental.acceptance_casebook list --casebook F [--notary-db DB [--eligible]]

The casebook path is always passed by the caller; nothing in Cairnwork reads or writes it.
Each line carries `prev`, the sha256 of the previous line, and `F.head` holds the last line's
digest, so an edited or removed line (the last one or the whole file included) is refused with
`CASEBOOK_NOT_APPEND_ONLY`. `add` holds a lock on `F.lock` while it reads and appends. `add` checks
candidate and base against the notary row (read only). This shows accidental edits, not a
deliberate rewrite of both the file and `F.head`.

Original evidence (#772): `add` stores `origin`, derived from the notary row (row digest, request
hash, status, integrity, HTTP status, recorded_at, Core identity digest, acceptance contract hash,
binding digest), apart from `after`, the later evidence (`EV.json` names candidate, task_id,
attempt_id, kind and ref; its sha256 is kept). `accepted_bad` needs an accepted original verdict
and `refused_good` a refused one (`CASE_ORIGIN_MISMATCH`); evidence about another claim is
`CASE_EVIDENCE_MISMATCH`. `list` shows each case's state (`unconfirmed`, `confirmed`, `rejected`;
with `--notary-db`, `origin_changed` when the row no longer gives the stored origin). Only
`confirmed` cases whose origin re-checks are listed by `--eligible`. There is no `confirm`
command yet (#772 step 2). Threat model: one local user owns every file; this is record integrity
and honest attribution, not protection against that user.

Round 2 (#825 review): `refused_good` needs a real Core refusal (a verification with integrity
VALID, HTTP 200 and status FAILED, REJECTED or REFUSED); an error row (502, `CORE_UNAVAILABLE`,
integrity UNKNOWN) is `CASE_ORIGIN_MISMATCH` for either kind. `accepted` is the notary's own
verified rule, and the row must pass `check_record_bindings` (request hash and target revision
agree with its columns) or it is `CASE_RECORD_MISMATCH`. With `--notary-db`, the later evidence
file is re-hashed too. A confirmation line without a confirmer never counts. `DUPLICATE` is
decided per (candidate, base, version, attempt); the same claim from another attempt is its own
line, and the other kind is `CASE_CONFLICT`.
