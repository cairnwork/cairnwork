You write the DEFERRED acceptance tests for a bug report. You play the role of a maintainer who receives a bug report
and writes the acceptance test that a fix must pass, WITHOUT knowing the fix. Your working directory is {D}. It holds:
- ISSUE.md: the bug report;
- CONVENTIONS.md: a summary of this repository's existing conventions related to the bug (empty values, types, case,
  ranges, error handling, output order/wording), each with its source in the repository, or "no basis";
- TASK.md: the library and the test directory;
- src/: the repository source at the buggy base (read-only).
That is all the information you get; use only it. You can read files in {D} and write files only under work/. You cannot run any code.

1. Write a pytest file work/test_deferred.py that checks the behaviour the issue asks for, through the public API,
   2-6 tests. Do not assert implementation details or private names.
2. For every edge behaviour (empty value, input type, case, range/boundary, error handling style, output order or wording),
   follow CONVENTIONS.md: assert what the repository's existing convention says. If CONVENTIONS.md says "no basis" for an
   edge behaviour you still want to test, put that check in its own test function whose name ends with `_nobasis`
   (those tests are never required to pass). Do not mix a no-basis check into another test.
3. At the buggy base at least one test should fail with an assertion or expected-exception failure (not ImportError, not a
   collection error). If the issue asks for a NEW public name that does not exist at the base, import it INSIDE each test
   function (not at module top).
4. The file will be placed in the test directory named in TASK.md and run with pytest from the repository root.
If the issue is too unclear to write a determinate test, write work/SKIP.txt with a one-line reason instead.
Final reply: one line: `written` or `skipped <reason>`.
