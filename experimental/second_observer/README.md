# second_observer (experimental, shadow mode, Issue #845 step 1)

A second observer that re-runs one target test in a **bare interpreter** (`python -I -B`, no
pytest, conftest, ini or plugins) and records, next to Cairnwork's verdict, whether the two
agree. It has **no authority**: it never accepts or refuses anything, adds no reason code to a
receipt and changes no Core, `verify_cli`, `report_guard` or `reasons` behaviour. Default off;
core never imports it. `OWNER` / `EXPIRES` as for every experimental directory (14 days).

## Threat model (read this first)

The observer process imports the test module, the test module imports the candidate's source,
and the candidate can run arbitrary code at import time. So the observer is **still
candidate-controllable** (the same limit as `protection.py` and `report_guard.py`). It only
defends against cheats that **depend on the pytest harness**: hooks, reports, collection and
other work done in the pytest process (a `pytest_plugins` module that flips the report,
swallows the exception or skips the test body). It does not defend against semantic cheats,
cheats that notice they are being tested, or code that acts at import time. Every row is
marked `same_process: true`; this is a raised bar, not a trust boundary.

## Use

```
python -m experimental.second_observer --input shadow-input.json --out <new dir> \
    [--protect <receipt dir>]...
```

`--out` must not exist, and must not be, contain or sit inside the project repository, the input
file or any `--protect` path (receipts, verdicts, the store): otherwise exit 2 with
`SHADOW_OUTPUT_PATH` before anything is created. Everything the CLI writes lives in `--out`:
`shadow.jsonl` (one JSON line per target), a private bare repository, the run directory, the
observer log. Verdicts: `PASS FAIL NA OBS_ERROR TIMEOUT UNCOMPARABLE`. A sandbox, observer or
rebuild failure is `OBS_ERROR` or `UNCOMPARABLE`, never a pass.

## Open 1 (inputs): record at verify time, then re-derive

`shadow-input.json` is written when the verifier runs (`inputs.describe_verifier`): the
interpreter path, the installed plugin set (with its sha256) and the ini text (with its sha256),
next to the base, candidate, test commit and the tested tree the verifier recorded. The CLI does
not trust the file alone: it also re-derives the plugin set from the live interpreter
(`pytest_config.installed_plugins`) and refuses on any difference. A missing interpreter, plugin
set or ini, or any hash that differs, is `UNCOMPARABLE`, never `NA`. Restricting the CLI to the
verifier's venv was rejected: account-mode runs use another interpreter and a venv can change
after the run, which would silently compare unlike things. No verify-time hook is added here
(manual trigger only); the hook that writes the file comes after the pre-registered experiment.

## Rebuild (AC5)

Uses the verifier's own code: `deferred_tests.lay_tests_over` (overlay-v1) and
`workspace.create_attempt_clone` (the same call `verifiers` makes), inside a private bare
repository in `--out` that reads the project's objects through an alternates file, so the
project repository is not written. The rebuilt tree must equal the recorded tree. A candidate
that is not a direct child of the base (except the base itself, for the base run), or a test
commit whose parent is not the base, is `UNCOMPARABLE`.

## Sandbox (AC4)

`cairnwork_sandbox.select()` with the verifier's policy: read the interpreter roots, write only
`run_dir` and the checkout's parent, no network, minimal environment. `-I` means `PYTHON*`
variables and the script directory are ignored; the observer adds to `sys.path` only the test
module's prepend root and the entries the verifier's ini `pythonpath` gave (re-rooted in the
rebuilt checkout). Final `sys.path` and environment are recorded in each row.

## NA (AC7)

A target is `NA` (never run, never PASS or FAIL) when:
- its node name has `[...]` (reason `PARAMETRIZED`; the function name before `[` must be a valid name, else `UNCOMPARABLE`, and what follows `[` is used for nothing);
- it has parameters or fixtures, marks such as parametrize, usefixtures, skip or xfail, is async or a generator, or is a method of a class that is not a `unittest.TestCase`;
- its module import needs a name only a `conftest.py` provides;
- an **autouse fixture** is defined in a `conftest.py` on the path from the rootdir to the test module, in the test module, or in a module they load through `pytest_plugins` (reason `AUTOUSE_FIXTURE`). The row lists the files and sets `autouse_in_candidate_change` when one of them is in the candidate's change: a candidate that adds an autouse fixture so that the observation becomes NA is itself a signal worth keeping. NA refuses nothing.

The autouse detection is **static** (AST: `@pytest.fixture(autouse=...)` and `@fixture(autouse=...)`, anything but a literal `False` counts). A fixture registered dynamically (a decorator built at run time, a plugin loaded by a hook) is not seen. This is a known limit, in line with the threat model above: the observer is a raised bar only.

Other import failures are `OBS_ERROR`. The NA rate on real tasks is Open 2 and is measured later, not here. Rule T (the test body must run) is step 2 (the `rule_t` field is `null` until then).
