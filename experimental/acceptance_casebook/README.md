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
`confirmed` cases whose origin re-checks are listed by `--eligible`. Threat model: one local user owns every file; this is record integrity
and honest attribution, not protection against that user.

Confirmation (#772 step 2):

    python -m experimental.acceptance_casebook confirm --casebook F --case LINE_SHA256 \
      --conclusion confirmed|rejected --check-method M --check-evidence-file E \
      [--lineage root|A,B] [--model-family F] [--dispatch-id ID] [--github-login L \
      --source-ref URL] [--source-file S] [--display-name N]

Identity comes from the harness env `CLAUDE_CODE_SESSION_ID` (family anthropic), a non-Claude
dispatch id with its family and output file, or a GitHub login with the URL and saved body of
its comment; `add` records the adder the same way. A confirmation counts only when the confirmer
is not the adder's session, not in its lineage (and the adder not in the confirmer's), is a human
or another model family, and both identities and the check evidence are complete. `confirm`
refuses otherwise (`SAME_SESSION`, `SAME_LINEAGE`, `SAME_FAMILY`, `IDENTITY_INCOMPLETE`,
`NO_CHECK_EVIDENCE`), and `list` applies the same rule to every confirmation line it reads.

Identity limits: ids, logins and lineage items are normalised (NFKC, invisible characters removed,
stripped, casefolded, then plain `[a-z0-9._:@/-]` only) and families must be spelled exactly as
one of `anthropic google openai deepseek xai meta mistral alibaba`; a human login is refused while
`CLAUDE_CODE_SESSION_ID` is set, and source and check files must be non-empty, with their digests
recomputed on every read. Human and other-family identities and every lineage are self-declared and
not verified; lineage is compared by direct membership, so a copy that names only an intermediate
ancestor is not caught. A hand-written confirmation line that passes every rule counts: the chain
and head stop edits, not appends.
