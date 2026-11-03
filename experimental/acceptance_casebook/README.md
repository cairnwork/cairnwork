# Acceptance casebook (experimental, default off)

Owner and expiry: see OWNER and EXPIRES. Step 1: `add` and `list`.

    python -m experimental.acceptance_casebook add --casebook F --notary-db DB --task-id T \
      --attempt-id A --candidate C --base B --version V --kind accepted_bad|refused_good \
      --found-by WHO --evidence REF
    python -m experimental.acceptance_casebook list --casebook F

The casebook path is always passed by the caller; nothing in Cairnwork reads or writes it.
Each line carries `prev`, the sha256 of the previous line, so an edit or removal is refused with
`CASEBOOK_NOT_APPEND_ONLY`. `add` checks candidate and base against the notary row (read only).
