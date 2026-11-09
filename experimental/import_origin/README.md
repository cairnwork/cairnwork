# import_origin (experimental, shadow measurement, Issue #1065 step 1)

This records import origins while the verifier's pytest command runs against a rebuilt candidate
and deferred-test checkout. The audit hook is installed before pytest starts. It records each
import event immediately to a parent-opened descriptor, then makes a best-effort dump of module
`__file__` values at interpreter exit. The parent classifies origins and flags protected names
from the candidate tree.

## Threat model

The hook runs in the candidate's process. It is a measurement, not a boundary. Candidate code
can close the descriptor, monkeypatch writes, or forge lines; every row therefore says
`same_process: true`. The measurement checks whether origin capture sees known shapes. It does
not stop them and does not change a Core verdict, receipt, or reason code. The tool writes only
under its new output directory. It is default off, and Core never imports `experimental`.
