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

## AC2 is measured, not assumed

The observer is a **shadow measurement only** and is never a basis for refusing anything. It is
deliberately a bare call that stays independent of pytest: it does not imitate pytest behaviour,
because imitating it would make it share the failure modes it exists to cross-check. So a
correct fix can still make the two disagree. AC2 is therefore not "a correct fix never
disagrees" but a measured quantity: the pre-registered pilot, on a stratified sample of real
targets, reports the disagreement rate on correct candidates and the NA rate per reason, and a
disagreement rate above 10% of judged targets stops expanding this line.

Known residual classes. Each row carries a `residual` list naming the ones present, so
disagreements can be stratified (it records, it never changes a verdict; `unknown:<CLASS>`, or
`["unknown"]` for a row with no rebuilt checkout, means the item could not be read):
- `INI_KEYS`: the ini the observer's input carries has a key outside the neutral set (testpaths,
  python_files, python_classes, python_functions, markers, norecursedirs, minversion, pythonpath,
  console_output_style, cache_dir);
- `STRICT_ARGS`: the base config yields strict-mode arguments;
- `TABLE_PLUGINS`: a table plugin is loaded with `-p` for the verifier;
- `IMPORTED_NAMES`: a conftest or the test module has a from-import or star-import;
- `CONFTEST_CODE`: a conftest has top-level statements other than import, def, class, docstring;
- `PYTEST_PLUGINS_NONLITERAL`: `pytest_plugins` is not a literal;
- `IMPORT_MODE`: an import mode is configured.

Not judged NA (known remainder): pytest before 8 ran nose-style setup/teardown hung on a function's attributes; the observer does not read them.

Always-on pytest behaviour is also not reproduced and not recorded per row: the per-test warning
filters, the conftest directories put on `sys.path`, the `PYTEST_CURRENT_TEST` variable, and
assertion rewriting.

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
- it is a class-level node (`file::Class`, reason `TARGET_SHAPE`; decided statically and again in the runner);
- it has xunit-style setup that pytest runs around it (reason `XUNIT_SETUP`): module-level setup_module, teardown_module, setup_function, teardown_function, setup, teardown, setUpModule, tearDownModule for a function target; setup_method, teardown_method, setup_class, teardown_class on the class and module-level setup_module / setup_function for a unittest target (unittest's own setUpModule and setUpClass do not count);
- it has parameters or fixtures, marks such as parametrize, usefixtures, skip, xfail or the built-in filterwarnings, is async or a generator, or is a method of a class that is not a `unittest.TestCase`;
- its module import needs a name only a `conftest.py` provides;
- an **autouse fixture** is defined in a `conftest.py` on the path from the rootdir to the test module, in the test module, or in a module they load through `pytest_plugins` (reason `AUTOUSE_FIXTURE`). The row lists the files and sets `autouse_in_candidate_change` when one of them is in the candidate's change: a candidate that adds an autouse fixture so that the observation becomes NA is itself a signal worth keeping. NA refuses nothing.

The autouse detection is **static** (AST: `@pytest.fixture(autouse=...)` and `@fixture(autouse=...)`, anything but a literal `False` counts). A fixture registered dynamically (a decorator built at run time, a plugin loaded by a hook) is not seen. This is a known limit, in line with the threat model above: the observer is a raised bar only.

Marks are read in two ways: statically from the module's AST (so NA holds on a host with no sandbox backend) and again by the runner on the imported objects (forms the AST misses: an alias such as `uses = pytest.mark.usefixtures(...)` then `@uses`, an annotated or conditional `pytestmark`, a base class's marks). The rule is conservative: `pytestmark = pytest.mark.skipif(False, ...)` makes the whole module NA even though the condition is false.

Other import failures are `OBS_ERROR`. The NA rate on real tasks is Open 2 and is measured later, not here. Rule T (the test body must run) is step 2 (the `rule_t` field is `null` until then).
