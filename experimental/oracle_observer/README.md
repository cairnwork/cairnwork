# oracle_observer (experimental, shadow mode, Issue #1039 steps 2 and 3)

For an **oracle target** the pass/fail comparison runs in the runner, outside the process that
runs candidate code, and the expected values never enter the sandbox. A `probe` (plain Python, no
pytest, conftest, ini or plugins) runs in the verifier's sandbox and emits `observe(key, value)`
and `observe_raises(key, call)`; the runner reads one JSON document after every process of the run
is gone and compares it, type-strictly, with the human-written expected file.

It has **no authority**: it records a verdict next to the pytest one, adds no reason to a receipt
and changes no Core verdict. Default off; core never imports it. `OWNER` / `EXPIRES` as for every
experimental directory (14 days).

- Observations are only the code under test's return values and exception classes. Subprocess
  output and written files are not observations (layer1 ruling, 2026-11-23).
- Only the reason codes `ORACLE_OUTPUT_INVALID`, `ORACLE_NO_OUTPUT` and `ORACLE_MISMATCH` leave
  the runner; never expected values, key names, per-key results or counts.
- Known limits (design v3): a candidate that guesses the expected values can emit them; the
  probe's fixed inputs are readable inside the sandbox; pytest-form targets are unchanged.
- Covered here: owner mode under Seatbelt (bwrap where the selected backend is bwrap). Account
  mode uses the shared verifier account and is not exercised by these tests.

## Trust of what leaves the sandbox (Codex R1)

- The base exception chain is read only from a chain file the runner writes last and closes with
  its own nonce (kept out of argv); stderr text and a file without that nonce count as no record.
  A base run has no candidate code, so this is a guard against a forged marker, not against code
  that reads the runner's frames.
- The runner serializes each observation when `observe` is called, with functions bound before the
  probe ran and exact JSON types only (a subclass is refused), so patching `json` changes nothing.
- A reused work directory starts from no output, chain or log file and no old runner copies.
