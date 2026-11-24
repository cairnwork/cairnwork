"""Oracle targets (Issue #1039, steps 2 and 3): decide pass/fail outside the candidate's process.

A probe (plain Python, no pytest) runs in the verifier's sandbox and emits observations; the
runner, which holds the human-written expected values, is the only process that compares. Shadow
mode: it records a verdict next to the pytest one and has no authority. Core never imports it.
See README.md.
"""

PASS, FAIL = "PASS", "FAIL"
# Reason codes: the only detail that may reach a worker, a retry prompt or a public log.
OUTPUT_INVALID = "ORACLE_OUTPUT_INVALID"
NO_OUTPUT = "ORACLE_NO_OUTPUT"
MISMATCH = "ORACLE_MISMATCH"
