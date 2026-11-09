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

Known limits of same-process measurement include executing source from a non-`.py` file into
`sys.modules['fractions']`, loading `spec_from_file_location` under another file name and then
calling `os._exit` to skip the exit dump, and patching `json.dumps` or closing the capture
descriptor before the hook's bindings are installed. These cases can evade or disrupt capture;
the result is evidence about observed import origins, not a trust boundary.

A `sys.meta_path` finder that imports another candidate module just before it returns the spec
for a protected name can make the file read and the latest import event disagree; with
`os._exit` skipping the exit dump, such a run can then go unflagged. The import-time flag for a
shadowed protected name holds only when no such finder is installed.

Origins are judged against the install paths that the verifier's interpreter reports, not the
observer's own. A checkout file that the candidate did not change is `checkout-base` (never flagged) only when
its path below the checkout or an ini `pythonpath` root spells the module name, i.e. base itself
imports it under that name (a root `secrets.py` already in base). Loaded under another name, for
example base's `pkg/fractions.py` as `fractions` after a candidate `sys.path` edit, it is
`checkout-shadow` and flagged. A candidate symlink is judged by the link's own path.
