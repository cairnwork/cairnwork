# Public entry points

Every place a stranger first meets Cairnwork, and the exact steps that check it. The weekly
workflow `.github/workflows/entrypoint-smoke.yml` (Monday 01:00 UTC, or run it by hand with
`workflow_dispatch`) walks these as a stranger: a clean runner, the tool installed from the
public git URL (never from a checkout of this repository), fixtures read from public repositories
pinned by commit. A failure opens or updates one Issue titled "entrypoint smoke failed". It is
advisory, not a required check. CI (`ci.yml`) tests the code; this tests the front door.

| # | Entry point | Where a stranger meets it | Steps that check it (workflow step) | Automated |
|---|---|---|---|---|
| 1 | One-line install | README "快速開始", docs/QUICKSTART.md §1, <https://cairnwork.github.io/> | `uv tool install --from git+https://github.com/cairnwork/cairnwork.git cairnwork` exits 0 (step 1); `cairnwork --help` lists `demo` and `cairnwork-verify --help`, `cairnwork-attest --help` exit 0 (step 2). Ubuntu 24.04 and macOS. | yes |
| 2 | `cairnwork init` | docs/QUICKSTART.md §3 | In a fresh temp dir: `cairnwork init --dir <dir>/state` exits 0 and writes a valid `config.json` (step 3). Default flags only; the account setup it points to is not part of `init`. | yes |
| 3 | `cairnwork demo` | README, docs/QUICKSTART.md §2, website "Try it in five minutes" | `cairnwork demo` exits 0 and prints `core status:  VERIFIED` (step 4). Needs a sandbox: Seatbelt (built in) on macOS; bubblewrap plus the AppArmor userns lift on Ubuntu 24.04 (docs/SECURITY.md), installed in a labelled prerequisite step. | yes |
| 4 | `cairnwork-verify` | docs/QUICKSTART.md §1 (listed as installed), docs/ISSUE_INTAKE.md | `cairnwork-verify --help` exits 0 (step 2). A real `run` needs a target repo and verifier command; the same verification engine is exercised end to end by entry point 3. | help only |
| 5 | `cairnwork-attest verify` | docs/ATTESTATION.md, README, website | On the published fixture (verify-action `fixtures/demo-target-pr4`, candidate in cairnwork/demo-target): `cairnwork-attest verify <bundle> --public-key <pem> --repo <demo-target> --candidate <sha> --base <sha> --json` returns `ok`, `code` OK, `core_status` VERIFIED, keyid as published; the same bundle with the wrong candidate must fail (step 5). | yes |
| 6 | Website public-key page | <https://cairnwork.github.io/keys/> and `/keys/<keyid>.pem` | The page returns 200 and lists the keyid; `/keys/<keyid>.pem` returns 200, is byte-identical to the fixture key that step 5 verifies with, and its raw-key sha256 equals the keyid (step 6). | yes |
| 7 | `cairnwork/verify-action` | <https://github.com/cairnwork/verify-action>, README, website | The action, pinned by commit, runs in its own job with the fixture bundle and key, `repo-path` a full clone of the demo-target candidate, `cairnwork-ref` this repository's current commit; outputs must be `verdict` VERIFIED and `code` OK (step 7). The git-notes path (`fetch-notes`) is not used: the fixture is passed with `bundle-path`. | yes, fixture mode |

## Not automated

- **`cairnwork-verify run` on a real repository** and **`cairnwork-issues`** (real Issue intake):
  need a target repository, a verifier command, and for adversarial code a dedicated verifier
  account. Checked manually (and by the test suite plus the `demo-linux` job in `ci.yml`).
- **`install.sh` fallback** (<https://cairnwork.github.io/install.sh>): installs uv by script and
  is deliberately not on the main path; checked manually when the script or its sha256 changes.
- **verify-action from a pull request** (`pull_request_target`, notes ref
  `refs/notes/cairnwork`): needs a live PR with a published note; checked manually.

## Keeping the fixture pinned

The workflow pins `cairnwork/verify-action` (`VERIFY_ACTION_SHA`), `cairnwork/demo-target`
(`DEMO_TARGET_SHA`, `DEMO_TARGET_BASE`) and the published demo keyid (`KEYID`) by commit. If
verify-action moves to a new fixture or the key is rotated, update those values and this page in
one PR. `tests/test_entrypoint_smoke_workflow.py` pins the workflow's permissions and triggers.
