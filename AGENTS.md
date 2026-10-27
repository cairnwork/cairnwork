# Cairnwork repository contract

Read `README.md` and `docs/LESSONS_FROM_NEXUS.md` first.

## Before you change anything

- Get scope from the integration owner before starting; at most two agent sessions develop Cairnwork at the same time. Find the owner in `README.md` or a CODEOWNERS file; if nothing names one, open an Issue and mention @James3014 (the maintainer) to get scope before starting.
- One worktree per session; the main clone at `~/workspace/cairnwork` is read-only.
- Do not copy Nexus-new code wholesale. Every capability enters under one of three classes (README): safeguard, experimental (`experimental/`, default off, owner + expiry), or formal (beat the bare core in Cairnwork's own A/B).
- Experimental capabilities live in `experimental/<name>/` with `__init__.py`, the module, `OWNER` and `EXPIRES` (ISO date); default off, 14-day expiry, never imported by core. Not-yet-wired code belongs there, not in `src/cairnwork`: `experimental/` still obeys the file and function limits but does not count toward the 5,000-line core budget.
- Never put the names or task ids of private evaluation task banks in public Issues, PRs or comments; refer to them only by Issue number.
- No experimental capability is moved in until the bare core passes the four drills: normal, failure, restart, rollback.
- Never modify an evaluation's measurement environment (its pinned task set, harness, models or environment) without the maintainer's explicit approval, whether or not a run is in progress.

## Rules the code must follow

- A failure in a safeguard, in verification or in a Core call must never produce a success state or an unauthorised side effect. Allowed degradations are listed in the contract, not improvised in `except` blocks.
- Every task has exactly one owning system, bound before dispatch by a durable, atomic record (idempotency key, request digest, owner, task id). Resend, query, cancel and recovery route by that record; unknown ownership stops dispatch.
- Only a nexus-core VERIFIED verdict marks a task complete.
- Limits enforced in CI (blocking): file ≤400 lines, function ≤80 lines, the sandbox package cap (`cairnwork_sandbox` ≤1,200), no import cycles, core never imports `experimental`.
- `src/cairnwork` 5,000 lines is a warning budget, not a hard limit: above it CI prints WARNING (not a failure) and the PR must briefly state the reason, alternatives considered (existing implementation, contract or adapter), responsibility (no second authority or duplicated state), cost, and verification. Placement follows responsibility: never create a new `cairnwork_*` package to avoid the counter; a new package needs an explainable boundary, one-way dependencies and an independently verifiable interface. `scripts/check_size.py` prints the per-package table.

## Verification

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run python scripts/check_size.py
uv run pytest -q
```

Run the sandbox tests for real on macOS: `CAIRNWORK_REQUIRE_SANDBOX=1 uv run pytest -q`. Without it the `sandbox`-marked tests are skipped locally, while CI's `test-macos` job sets it and fails if `sandbox-exec` is missing, so a local green run can hide a real failure. Local verification always runs the full `uv run pytest -q` (with `CAIRNWORK_REQUIRE_SANDBOX=1` on macOS). Separately, for Issue-intake tasks the verifier counts a run as passed only if every collected test passed, so a skipped test fails it: when choosing the verification commands or hidden tests of such a task, pick targets that have zero skips on the host (`scripts/env_smoke.py` reports `SKIPPED_TESTS`).

## Merging

Same as the other Nexus repos: independent review (either a record from a Claude-family agent that is not the author, named in the record, or a submitted GitHub APPROVED review by a human maintainer; high-risk paths additionally need a cross-model Codex or Gemini record), every review record posted on the PR as a comment with head SHA and verdict (a human APPROVED review counts as the independent review itself), required checks green, then the author merges with `gh pr merge N --squash --match-head-commit <sha>`. PR bodies carry the NEXUS_ISSUE_INTENT_V1 block when they close an Issue.

### PR body and review record

- Every PR body carries one `Cairnwork:` line (`Cairnwork: 受影響`, `不受影響` or `尚無對應`, optionally with a short reason) and, when it closes an Issue, the `NEXUS_ISSUE_INTENT_V1` block. `Closes #N` in the body, the commit message and the intent block must agree; a custom squash message cannot fix a mismatch, so fix the commit message.
- The independent review is either (a) a record from a Claude-family agent that is not the author, named in the record, or (b) a submitted GitHub APPROVED review by a human maintainer. A record is a PR comment whose section header names the reviewer family, with a verdict line naming the full head SHA:
  ```
  ## Independent review (Claude Opus)
  Verdict: ACCEPT@<full 40-character head sha>
  ```
- High-risk paths additionally need a cross-model (Codex or Gemini) record (same format with that family in the header; at most three rounds). High risk means verification or gate logic, `.github/workflows/`, `scripts/ops/`, `scripts/ci/`, `pyproject.toml`, `uv.lock`, `ruff.toml`, `pytest.ini`, `pyrightconfig.json` and this file. Headless reviewers cannot use tools, so put the full diff in the prompt.
- A record is bound to one head SHA. Any commit that changes the PR's content needs a fresh review of the new head. Only a merge of `main` with zero diff on the PR's own paths may be carried forward, and the new record must say so: "delta since review: merge of main only, zero diff on PR paths". Re-posting an old record or pushing an empty commit does not satisfy review.
- Every record after the first lists each REJECT item of the previous round with how it ended: `fixed in <short sha>`, `moved to #<issue>` (minor findings, as under "Review speed"), or `disputed: <evidence>`. Do not write "rebutted" or "rebuttal" here: the review-record checker reads those words as a rebuttal at the round cap. When the previous round had no open items, write `No open items from the previous round` (no uppercase verdict word: the checker reads one after a colon as a verdict). When the PR comes from a task that reached VERIFIED, every record, from the first on, adds one line: `Deferred tests cover: <what>; not covered: <what>`. A headless reviewer cannot look these up, so the prompt must carry the previous round's REJECT items and the task's deferred tests. (The 2026-10-25 provenance audit: 3 of 5 VERIFIED pilot fixes still needed substantive changes because the deferred tests were narrower than the request, and 5 of 25 records said only "fixed".)
- When a pilot Issue is closed, the closing comment carries two plain-text fields. A pilot Issue is one that Cairnwork's issue runner worked on during the 2026-10-20 to 11-16 pilot. The fields are `Cause: <exactly one of ENV, TEST-GAP, REQ-GAP, CODE, UNKNOWN>` and `Changed after VERIFIED: no`, or `yes — <why>`. The author of the PR that closes the Issue posts this comment on the Issue just before merging, because the merge closes the Issue through the intent block. If an Issue closes without a PR, the person closing it posts it.
- After posting a record, wait a minute or two: `Review record present (advisory)` re-evaluates on PR comments and usually turns green by itself.
- To close an Issue, put the Issue marker and intent block in the PR body:
  ```
  Closes #N
  <!-- NEXUS_CORE_ISSUE: N -->
  <!-- NEXUS_ISSUE_INTENT_V1
  [{"issue": N, "on_merge": "CLOSE"}]
  NEXUS_ISSUE_INTENT_V1 -->
  ```
  To leave the Issue open, write `Refs #N` instead of `Closes #N` (in the body and commit messages) and use `"on_merge": "KEEP_OPEN"`.

### Review speed and risk tiers

Approved by the maintainer on 2026-10-21. These rules speed review up; they do not relax the record format, head-SHA binding or carry-forward rules above.

- Reviewers follow `REVIEW.md`: what counts as broken, which paths need no line-by-line reading, and how to report findings.
- Keep PRs small. Before requesting review run `ruff check`, `ruff format --check`, `lint-imports`, `scripts/check_size.py` and the full test suite. A cheap first-pass model scan of the diff is encouraged to catch obvious problems before the reviews.
- Review in parallel: send the same head to the independent Claude-family reviewer and, for HIGH risk, the cross-model reviewer at the same time, and merge both results into one fix round. Do not queue them one after the other.
- Round 1 reviews the whole PR. From round 2, review only the delta since the reviewed head plus whether the previous round's findings are really fixed. Minor findings that are not about safety become follow-up Issues instead of another round. Maximum three rounds. If findings of the same class are still open in round 2, the author fixes the whole class at once (every instance in the PR, not only the cited lines); if the class is still open in round 3, the change goes back to design (rethink the approach, split or close the PR) instead of a written argument that pushes it through. A finding shown to be wrong is answered with `disputed: <evidence>` as above. Real defects (correctness or security) are the exception: a defect the PR introduces is fixed before merge; only pre-existing or unrelated defects may go to an Issue.
- HIGH risk (independent Claude review plus a cross-model Codex or Gemini record): anything under `src/` (core `src/cairnwork/`, `src/cairnwork_protocol/`, `src/cairnwork_verify/`, `src/cairnwork_sandbox/` and any other package there); permissions; dispatch and ownership; protected paths; everything under `scripts/` (gates, drills and verifier or sandbox setup); and every path the review-record check treats as high-risk (its list in the Nexus-new check is authoritative; today it includes `.github/workflows/`, `scripts/ops/`, `scripts/ci/`, `.nexus-core/`, `pyproject.toml`, `uv.lock`, `ruff.toml`, `pytest.ini`, `pyrightconfig.json`, `MUSE_PROTO.md` and `AGENTS.md`). The CI check enforces the cross-model record only for its own list; the other HIGH paths here are policy that authors and reviewers must apply themselves.
- LOW risk (the independent Claude review only): `docs/` (but not `AGENTS.md`), `experimental/`, demo material, and changes only under `tests/` that add or change no `conftest.py` or shared fixture and do not touch tests of a safeguard (permissions, dispatch, ownership, protected paths, sandbox, verification, notary). When a PR fits both tiers, HIGH wins; when in doubt, treat the PR as HIGH.
- A PR that increases the core (`src/cairnwork`) line count and leaves it above 6,000 lines must carry one line in its body with the maintainer's explicit confirmation (who and when). 5,000 stays a warning and `check_size.py` is unchanged.
- Quota fallback: if both cross-model families (Codex and Gemini) are out of quota, a review that blocks a merge the maintainer is actively waiting for may fall back to a second, separate Claude-family reviewer, and the record must say so and name the exhausted quotas. For paths on the review-record check's own list the required check will still read the cross-model record as missing, so those PRs wait for Codex or Gemini. Models under measurement and models already assigned to dispatched work are never substituted.

### Required checks and merging

- The ruleset requires `test (3.11)`, `test (3.12)`, `test (3.13)`, `test-macos`, `Review record present (advisory)`, `Bindings preflight (default branch)` and `pyright (trusted)` (posted from the default branch by `pyright-trusted.yml`; no new Pyright findings versus the merge base). Other jobs (`hypothesis`, CodeQL) are not required but should not be red without a written disposition (pre-existing or accepted) in a PR comment (the review record, or a separate comment when the independent review is a human APPROVED review).
- When the required checks are green, merge with `gh pr merge N --squash --match-head-commit <head sha>`. A transient 502 may still have started the merge, and an immediate retry then says it is already in progress: poll the PR state until `MERGED` instead of retrying blindly.
- After merging, delete the remote branch and remove your worktree.

### Common failure modes

- Core over the 5,000-line budget: this is a warning, not a failure. State reason, alternatives, responsibility, cost and verification in the PR; move not-wired code to `experimental/`; do not refactor or split packages just to get under the number.
- Sandbox tests silently skipped locally: set `CAIRNWORK_REQUIRE_SANDBOX=1`.
- macOS temp directories under `/var/folders/...` are symlinks into `/private`: call `.resolve()` on run roots, or the sandbox sees a different real path and denies access.
- A stale `.venv` can fail with an `xcode-select`/`xcrun` error: run `uv sync --locked`, or work from a fresh worktree.

Merge on current main, not a stale base. A PR's checks run against main as it was when they last ran, but some limits are additive across PRs: the per-package caps and any other whole-repo total (the core 5,000-line budget now only warns, but it was a hard limit when #29 and #30 each measured under 5000 but summed to 5048 and turned main red). So before you merge, if main has advanced since your checks last ran, merge `origin/main` into the branch (same rule as "whoever merges second rebases" in the other repos) and let CI re-run; the `--match-head-commit` SHA is that re-run head. `ci.yml` also runs `check_size` on every push to `main`, so a blocking breach that slips through still turns main red at once rather than silently — prevent it by re-running on current main first, especially when a package is near its cap.
