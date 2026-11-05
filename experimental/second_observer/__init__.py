"""A second observer outside pytest, in shadow mode (Issue #845, step 1).

It re-runs one target test in a bare interpreter (no pytest, conftest, ini or plugins) and
records next to Cairnwork's verdict whether the two agree. It has no authority: nothing here
accepts or refuses anything. Core never imports this package. See README.md for the threat model.
"""

PASS, FAIL, NA = "PASS", "FAIL", "NA"
OBS_ERROR, TIMEOUT, UNCOMPARABLE = "OBS_ERROR", "TIMEOUT", "UNCOMPARABLE"
VERDICTS = (PASS, FAIL, NA, OBS_ERROR, TIMEOUT, UNCOMPARABLE)
