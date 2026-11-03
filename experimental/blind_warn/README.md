# blind_warn (experimental, off by default)

When a candidate fix is checked, two independent blind tests (A and B) are written by a model that
sees only the ticket and the repository's conventions, never the fix. If the candidate makes an
assertion fail in BOTH A and B, the result gets one warning line. It never changes a verdict and
never blocks anything.

```
python -m experimental.blind_warn.cli --ticket ISSUE.md --base <git checkout> \
    --candidate-diff cand.patch --state <cairnwork state> [--model claude-sonnet-5-5] [--out r.json]
```

Output JSON: `{"warn", "a", "b", "line", "cost_usd", "error"}`; `a`/`b` hold `written`, `failed`,
`undecided`, and `failed` split in two by the base run: `regression` (passed on the base, fails on
the candidate) and `not_fixed` (did not pass on the base either: a test written from the ticket is
meant to fail on the base, so the candidate did not fix it, or the test is wrong). The split is for
display and counting only; `warn` does not depend on it. `line` names the failing functions and
the count of each kind, and is empty when there is nothing to say.

Rules
- Step 1 reads the repository conventions (prompts/BRIEF-conv.md); step 2 writes A and B from the
  same brief in two separate calls (prompts/BRIEF-test.md).
- Both files run on the base and on the candidate through Cairnwork's own verifier path
  (`verifiers.run_verifiers`, the state's account and Python), one fresh checkout per run.
- A function counts as failed only if it fails on the candidate and was collected on the base.
  A file whose candidate run exits with a code other than 0 or 1, or has a collection error, is
  undecided as a whole. `warn` is "A has a failed function and B has a failed function".
- Any model, network or sandbox error gives `warn=false`, an empty `line` and an `error` text.
- The model key comes only from `ANTHROPIC_API_KEY`; without it the call returns an error at once.
- The `outcome` the guard reports does not separate an assertion failure from a failed fixture, so
  a setup error on the candidate also counts as a failure. Cost is an estimate from token counts.

Comparison with R-G: R-G used a tool-using agent that read the whole tree. This is one Messages
API call per step with files inlined (at most 150 KB of tracked text, chosen by name overlap with
the ticket; symlinks are skipped). R-G's numbers therefore do not transfer to this version.

The candidate's own test configuration (conftest, pytest settings it changes) takes part in the
candidate run, so it can hide or produce a warning; acceptable for an experimental warning line.
A candidate that puts a link at a blind-test path makes the run refuse (`unsafe test path`).

Owner and expiry are in OWNER and EXPIRES. Core never imports this package.
