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

Anything else refuses the whole test, with the reason. That covers fixtures (autouse ones
included), setup hooks, skip/xfail marks, other decorators, non-literal values, tuples, sets,
bytes, calls whose result is never checked, control flow, `match=`, helpers defined in the test
file, and modules that are not the project's. `--kinds` chooses which kinds count as protected.
The default is what this version declares: `function`, meaning top-level functions with JSON
values and no floats. `--declaration` writes the protected function cases as a new file, mode
0600, with a fresh salt. It checks the file first with `cairnwork.blackbox.parse`.

## Review every draft before declaring it

- The blackbox compares exact JSON types. If a function returns `3.0`, `True` or a tuple, it
  does not match `3`, `1` or a list, although pytest's `==` passes. Confirm the return type.
- `pytest.raises(T)` accepts subclasses of `T`. A declared exception may be compared exactly.
- Several cases of one check run one after another in one process, so state can carry from one
  case to the next. The order may differ from the test's order.
- Once a case is declared, remove its answers from the deferred and visible tests that the
  candidate's code can read. This matters for #311, and because a candidate can also copy the
  expected logic out of such tests.
- The static count is exact for literal tables. When parametrize values are not static, the test
  counts once and `count_exact` is false. The count is then a lower bound: use pytest's own
  collected count for the denominator where you have it.
- An autouse fixture can change what a test sees, so it refuses every test it applies to. If the
  owner has checked that the fixture does not matter, the owner can still declare those cases.
