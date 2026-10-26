# Blackbox coverage and declaration suggestions (#303)

A `cairnwork-verify --blackbox` check is protected: the answers never enter the run and the
owner compares what the candidate's function returned. An ordinary pytest test that runs in the
candidate's process is not. This note defines how much of a task's acceptance is protected, and
describes `tools/blackbox_suggest`, which proposes declarations from existing tests.

## The metric

Adopted from the council of 2026-10-27 (item 4, Astra's definition).

- **Obligation.** Each test id pytest would collect from the task's required acceptance tests,
  with every parametrized case counted on its own. The set is fixed before any candidate runs,
  and tests that cannot be protected stay in it. Tests skipped or deselected at run time do not
  shrink it.
- **Protected obligation.** Every check in the test can be declared as a blackbox case, of a
  kind the running cairnwork version supports, with the same inputs and the same expected value.
  A test that is only partly declarable is not protected: the rest of it still runs only in pytest.
- **Task coverage** = protected obligations / obligations.
- **Reported numbers.** Over tasks, weighted equally:
  1. the mean task coverage;
  2. the share of tasks whose obligations are all protected.

  Report both with the number of tasks and the main reasons obligations are unprotected. A task
  with no obligations is counted separately and is left out of both numbers.
- **Auxiliary: wrong-fix interception.** On known wrong fixes, the share the blackbox alone
  would block. It needs, for each wrong fix, which declared cases it gets wrong. Measure it only
  where those results already exist; never run a candidate to produce them for this metric.
- No pass threshold is set. One is chosen after measuring.

This is protected **coverage**, not a safety rate. A protected obligation still has the
blackbox's own limits (see `src/cairnwork/blackbox.py`): low-entropy answers can be guessed, a
declaration that is used again and again turns into an oracle, owner mode gives no guarantee,
and a candidate can hard-code the declared answers.

## The tool

```bash
uv run python -m tools.blackbox_suggest --project ROOT [--source-root DIR ...] \
    [--kinds function] [--json out.json] [--markdown out.md] [--declaration decl.json] TEST_FILE...
```

Run it from a cairnwork checkout. It parses the test files, the conftest files above them and
the project's modules with `ast`. It never imports or runs any of them, and it never runs the
candidate to work out an answer. Each expected value comes from the test's own literals.

For every obligation the report gives `file:line`, the kinds it needs, and either draft cases or
the reason it is refused. The shapes it reads:

- `assert f(args) == literal` (either side), `assert f(args) is None/True/False`, and
  `self.assertEqual` / `assertIsNone` in `unittest` classes;
- `assert obj.method(args) == literal`, where `obj = Cls(literal args)` is built in the test.
  This needs the method kind; several calls on the same object need the sequence kind;
- `with pytest.raises(T): f(args)` and `self.assertRaises(T, f, *args)`, which need the raises
  kind;
- float values, and `pytest.approx` with its tolerances, which need the float kind;
- parametrize tables, including stacked ones and `pytest.param`. Literal assignments in the test
  and in the module are resolved.

Anything else refuses the whole test, with the reason. The reasons fall into three groups.

- **Things that run around the test.** The tool uses allow-lists here, not a list of known dangers.
  - The test module, every conftest and every test package `__init__.py` above it may hold only inert top-level statements. Allowed are:
    - docstrings;
    - imports of pytest, unittest, the standard library or the project; a test helper is not the project. Importing a project module runs its top-level code, so a test keeps its draft only if every project module imported around it is one the blackbox runner imports anyway: a drafted module, its parent packages, or what their top-level imports name, transitively;
    - literal assignments;
    - `def` and `class` whose decorators, default values and bases call nothing but pytest. A class may not derive from a project class, whose `__init_subclass__` could run. The names `mark`, `fixture` and `param` may not be bound to anything but pytest's;
    - `if TYPE_CHECKING:` imports.

    Anything else refuses every test in its scope. That includes `try` imports, other `if` blocks, hooks and `pytest_plugins`.
  - Each pytest config file from the test's directory up to the project root (pytest.ini, .pytest.ini, pytest.toml, .pytest.toml, pyproject.toml, tox.ini, setup.cfg) may hold only options known to leave a test's outcome alone. Allowed are:
    - `filterwarnings` entries other than `error`;
    - reporting `addopts` (verbosity, tracebacks, coverage reports, `-p no:...`);
    - markers, paths and logging.

    Any other option refuses the tests. That covers a plugin's own option, `-W`, `-p plugin`, `--cov-fail-under` and `usefixtures`.
  - Fixtures, including autouse fixtures and their params; setup and teardown hooks; any mark except `parametrize`; other decorators.
- **Values the blackbox cannot carry.**
  - Values that are not literals.
  - Tuples, sets and bytes.
  - A name that is rebound in the test.
  - A list or dict passed to several calls, because one call may change it. That includes one reached through another name or container (`e = [d]`), and a module-level list or dict, which every test in the module shares.
  - An assignment whose value runs a call (`_ = [reset()]`).
- **Shapes the tool cannot read.**
  - Calls whose result is never checked.
  - Control flow.
  - `match=`.
  - Helpers defined, or imports rebound, in the test file.
  - Modules that are not the project's.

`--kinds` chooses which kinds count as protected. The default is what this version declares: `function`, meaning top-level functions with JSON values and no floats. `--declaration` writes the protected function cases as a new file, mode 0600, with a fresh salt. It checks the file first with `cairnwork.blackbox.parse`. The reason categories in the coverage output are fixed labels, so they never carry a project's names or values.

## Review every draft before declaring it

- The blackbox compares exact JSON types. If a function returns `3.0`, `True` or a tuple, it
  does not match `3`, `1` or a list, although pytest's `==` passes. Confirm the return type.
- `pytest.raises(T)` accepts subclasses of `T`. A declared exception may be compared exactly.
- Several cases of one check run one after another in one process, so state can carry from one
  case to the next. The order may differ from the test's order.
- Once a case is declared, remove its answers from the deferred and visible tests that the
  candidate's code can read. This matters for #311, and because a candidate can also copy the
  expected logic out of such tests.
- Wherever the tool cannot know what pytest collects, the count is marked inexact (`count_exact` false) and is a lower bound. That covers parametrize values that are not literals, collection hooks, `pytest_plugins`, collection or selection options in the configuration (`python_files`, `-m`, `-k`, `--ignore`), fixtures defined nowhere visible, tests defined under `if` or by assignment, and classes whose bases are defined elsewhere. A file whose only tests may come from code the reader cannot see gets one placeholder obligation, marked inexact. Use pytest's own collected count for the denominator where you have it. Plugins installed in the environment are not visible to a static reader.
- Test ids number parametrized cases by index (`test[0]`), not by pytest's generated ids.
- Anything that runs around a test refuses it, because it can change what the test sees. That includes an autouse fixture, a hook, a mark or the configuration. If the owner has checked that it does not matter, the owner can still declare those cases.
