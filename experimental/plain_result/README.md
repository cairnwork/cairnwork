# Plain result summaries (experimental)

A read-only adapter for Cairnwork verifier and attestation JSON records. It uses fixed
Traditional Chinese templates and never determines or changes a Cairnwork verdict. Core does
not import this package. The summary is an experimental presentation aid and expires on
2026-11-20 unless its owner establishes its value.

Callers that show a summary to a person pass `current_commit`, `task_id` and `attempt_id`; without them a VERIFIED record is never shown as a pass for the current work.
