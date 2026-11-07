# Plain result summaries (experimental)

A read-only adapter for Cairnwork verifier and attestation JSON records. It uses fixed
Traditional Chinese templates and never determines or changes a Cairnwork verdict. Core does
not import this package. The summary is an experimental presentation aid and expires on
2026-11-20 unless its owner establishes its value.

Callers that show a summary to a person pass `current_commit`, `task_id` and `attempt_id`; without them a VERIFIED record is never shown as a pass for the current work.

The strong `attested` state needs `verifier_runs` other than `signer_claim`. `attest verify --json`
always reports `verifier_runs: signer_claim` today (`verify_binding._assurance`), so real
attestation output always renders the weak text; this fails closed on purpose.

`card.card()` (#898 follow-up, same expiry) splits one record into four fixed cells: tested,
confirmed problem, not covered, undetermined. Each tested or confirmed line names the record
field it comes from. Only a record `summarize` shows as verified fills "tested", and only with the
version, Core status, protection level, verifier ids and deferred test paths, never a feature.
"Not covered" always says the overall coverage is unknown (no requirement-to-test record, #699).
A FAILED run is a confirmed problem only when its reason has none of the environment phrases
`reasons.py` writes, and even then it reads "not yet cleared of the environment"; otherwise it is
undetermined, like UNCOMPARABLE, REFUSED, a pre-Core `error`, and attestation.
