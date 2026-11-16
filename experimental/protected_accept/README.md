# Protected acceptance (experimental)

## Threat model

- Expected answers stay in the owner process and are compared from strict JSON.
- Candidate code never supplies the expected values or the protected verdict.
- A verifier account prevents candidates from reading owner-only declarations.
- A sandbox profile is required; account and sandbox status are reported separately.
- Entropy checks refuse checks that a constant or guessed report can satisfy.
- One declaration commitment may be attempted once in a store.
- Candidate reports remain untrusted, including their module path and outcomes.
- This package is default off and is never imported by core.

## Statuses

`PROTECTED_PASS` means the protected checks matched. `MISMATCH` means an observed answer disagreed.
`REFUSED` means policy or account requirements were not met. `INCOMPLETE` means execution or judging
could not establish an outcome. None of these statuses is `VERIFIED`.

## Limits

C10 runner-sniffing is a known residual. A worker still receives 1-bit PASS/MISMATCH per check per
attempt. macOS Seatbelt and Linux bwrap protection levels are reported separately by the runner.
This diagnostic result never claims Core verification and never marks a task VERIFIED.
