# counterfactual_ps (experimental, default off, expires 2026-11-27)

cw#1068. A candidate can FAIL only because the sandbox denies exec of `ps` (a setuid platform binary). This module
compares the scored (in-sandbox) failures with an outside-the-sandbox re-run of the same candidate and tests.

- FAILED -> UNCOMPARABLE only when the outside run is fully clean (passed, no failed/error/skipped) and the sandbox log
  carries a `ps` denial. Anything else stays FAILED. It never yields VERIFIED, changes no score, no denominator, no gate.
- The result is a diagnostic only, never formal acceptance.
- Missing pytest summary or a tripped guard in the outside run -> ERROR (never a pass).
- Pure functions + an offline CLI that reads two existing files. Off unless `CAIRNWORK_EXPERIMENTAL_COUNTERFACTUAL_PS=1`.
- Not imported by `src/cairnwork` (import-linter). Delete the directory to revert.

## Isolation limits (read before using)
1. **This module never runs a candidate.** Producing the outside run means executing untrusted code without the
   sandbox. Do that only in a separate isolated environment (container, VM or a controlled `ps` shim) with isolated HOME
   and TMPDIR, wall and RSS guards. Never on a host that holds credentials or on the verifier account.
2. A candidate can detect the sandbox and print a `ps` string; the outside run (not the string) is the evidence, and a
   diagnostic must not be promoted to a verdict without a design that closes this.
3. Only an 18-item agent-coord sample exists (C arm 9 of 9 converted, W arm 0 of 9 converted).
