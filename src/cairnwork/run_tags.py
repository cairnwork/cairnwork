"""Run directory tag letters shared by execution, recovery, and process cleanup."""

RUN_TAGS = "vbpqremcosxf"
OPAQUE_TAG = "x"  # the tag every verifier run directory carries (verifier_exec.opaque_clone_id)
EVIDENCE_MARK = ".evidence"  # in a run's private directory: its verify log outlives recovery
