# Plain result fixture provenance

Synthetic values only. The verifier records mirror `verify_candidate`'s returned object at
`src/cairnwork/verify_candidate.py:143-161`, with `status` and `verdict` produced by
`_status_fields` at `:165-169`. Attestation JSON uses `_print_json`'s exact key set at
`src/cairnwork_attest/cli.py:128-149`; the success and failure examples correspond to the
`_cmd_verify` call at `:207-220`. The malformed example is intentionally truncated to exercise
missing-field handling and is not claimed to be a successful emitted output.
