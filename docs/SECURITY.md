# Security notes

## Verifier sandbox backends

Candidate code runs confined by an OS sandbox; when no working backend exists nothing runs
(there is no unconfined fallback).

- **macOS Seatbelt** (`src/cairnwork_sandbox/seatbelt.py`): default deny. Reads only from an
  allow-list (system libraries, the interpreter, the checkout); writes only to the checkout
  and the per-run directory; no network; credential stores denied last. Behaviour unchanged.
- **Linux bubblewrap** (`src/cairnwork_sandbox/bwrap.py`): a read-only bind of the whole root,
  read-write binds for the write roots only, every namespace unshared (no network), all
  capabilities dropped, nested user namespaces disabled. The masked set (all under `$HOME` unless
  an absolute path is given):
    - **Credential directories** emptied with a tmpfs: `.ssh`, `.aws`, `.gnupg`, `.config`,
      `.docker`, `.kube`, `.claude`, `.codex`, `.gemini`, `.nexus`, `.local/state`,
      `.local/share/keyrings`, `.terraform.d`.
    - **Credential files** replaced with `/dev/null`: `.netrc`, `.git-credentials`, `.npmrc`,
      `.pypirc`, `.pgpass`, `.my.cnf`, `.vault-token`, `.Xauthority`.
      A credential path that is, or runs through, a symlink is masked at its real target, inside
      or outside `$HOME` on the launching-account path; under the dedicated account a target
      inside or outside `$HOME` is masked when the account can reach it (every directory down to
      its parent other-traversable, `bwrap._account_can_reach`; an outside target is read through
      its own path even from a `0700` home, #358), and one it cannot reach is not masked, since it
      cannot leak and a mount over it would fail. A dangling link, or one whose target
      is a system root, `$HOME`, an ancestor of it or of a write root, makes the policy refuse
      (#341); under the dedicated account only for a link it selects, so a dangling link whose
      target's parent the account cannot reach, or that sits in a home the account cannot enter
      and points inside it, is ignored. Residual: the policy is built before the run, so a credential link repointed while
      a run is in progress leaves its new target readable (main already had this for relative
      links); tracked in #353.
    - **Socket trees** emptied with a tmpfs (the whole tree, covering every socket under it):
      `/run/user` (systemd user bus — a `systemd-run --user` would start a process outside the
      sandbox — and session agents), `/tmp/.X11-unix` (an X server lets a process inject input and
      run commands outside the sandbox), and `/run/libvirt` (every libvirt daemon socket, the
      monolithic `libvirtd` and the modular `virtqemud`/`virtnetworkd`/…, each root-equivalent).
    - **Root-equivalent daemon sockets** replaced with `/dev/null`: Docker, Podman, containerd,
      the system D-Bus, LXD and Incus (each is root-equivalent; a read-only bind does not stop
      `connect()` to a pathname `AF_UNIX` socket, and the network namespace covers only abstract
      sockets).

  Only paths that exist are masked. Write roots that are `/`, a system or temp directory, `$HOME`
  or an ancestor of `$HOME` are refused.

  POSIX semaphores are allowed so multiprocessing-based test suites can be verified. macOS: names
  matching `^/mp-` only; if `sandbox-exec` definitely rejects the regex form the rule widens to all
  semaphore names, and if the probe is inconclusive (timeout, failure) no semaphore rule is emitted
  and semaphores stay denied. Linux: `/dev/shm` is a fresh private tmpfs (writable, so in-sandbox
  shared-memory objects work but never reach the host) capped at 64 MiB, and it is mounted ONLY
  when the installed bwrap supports `--size`; without that support `/dev/shm` is not mounted at all
  (reason `BWRAP_TMPFS_SIZE_UNSUPPORTED`, carried as `Policy.notes` and shown in the run reason as
  `(shm withheld: ...)`; not part of `profile.sb`, so no bound digest changes) and multiprocessing targets stay unverifiable (fail
  closed) rather than getting an uncapped tmpfs. Mach IPC and host shared memory remain denied.

  **The blackbox run gets no named IPC (#311).** A declared blackbox check (#303) runs with
  `allow_ipc=False`: on macOS its profile has no semaphore rule, so deny-by-default refuses IPC (the
  trailing `(deny ipc*)` only states it) and the run can neither create nor open a named POSIX
  semaphore (any name, `/mp-` included), POSIX shared memory, or an existing SysV semaphore, segment
  or queue. A declared function that needs these (`multiprocessing` locks, queues or shared memory)
  therefore always fails its check on macOS (`MISMATCH`): declare such code through a function that
  does not start them. Within one verify the blackbox also runs
  before the verifiers and P2P, the first run of the candidate there, so nothing a pytest phase of
  the same verify learnt from the tests can be waiting for it. Linux bwrap needs no option: every
  run has its own IPC namespace (`--unshare-all`) and a fresh `/dev`, so named IPC never outlives
  or leaves a run.

  **What stays open on macOS: whether a name exists, and file access times.** These namespaces are global to the host,
  across users. Opening an absent semaphore name fails with `ENOENT` before the sandbox is asked,
  a present one with `EPERM`, and creating a new SysV object (`semget`/`shmget`/`msgget` with
  `IPC_CREAT`) is not mediated by Seatbelt at all, after which its key answers `EEXIST`. So a run
  can still learn which names or keys exist. Candidate code in an *earlier* verify on the same host
  (owner mode or account) can leave names that encode what it saw in that verify's tests, and a
  later blackbox run can read them back bit by bit. No Seatbelt rule we tried closes this (a
  `syscall-unix` number filter had no effect on these calls on macOS 27, and `_ctypes` and
  `_multiprocessing` are built into the interpreter). A second channel of the same kind: reading a
  file updates its access time on APFS, and files every run may read (the interpreter's, the
  system's) can be read by one run and `stat`-ed by a later one. Until these are closed: on macOS,
  do not let a declaration's answers, or the logic that produces them, appear in the deferred or
  visible tests of any earlier run on that host, whatever the channel. "Earlier run" means any
  earlier `cairnwork-verify` (owner mode or account) and any earlier pipeline attempt, whose
  verifier profile still allows `/mp-` semaphores with their contents. Linux bwrap is not affected
  by the IPC channel. `tests/test_sandbox_ipc.py` pins the IPC residual (#316).

  **Deployment note:** a verifier write root or state directory must not live under a masked path
  (a masked credential dir such as `~/.config` or `~/.local/state`, or a masked socket tree such as
  `/run/user`, `/tmp/.X11-unix`, `/run/libvirt`). The tmpfs is applied after the write binds, so
  inside the sandbox the directory is replaced by an empty tmpfs: any state already there is hidden
  from the run, and anything written there is discarded when the run ends. (The tmpfs is itself
  writable, so a single run that writes and reads back within itself still sees its own data — the
  loss is of *pre-existing* and *persisted* state, not an in-run write.) Put Cairnwork's state under
  a dedicated location such as `/var/lib/cairnwork` (or `$XDG_STATE_HOME` set outside the masked
  trees), not under a masked directory. The same holds on macOS, where Seatbelt denies the masked
  home paths (`seatbelt.CREDENTIAL_HOME_PATHS`). Without a verifier account, `cairnwork-verify run`
  refuses a state directory or run root under a masked home path (`STATE_UNDER_SANDBOX_MASK`,
  naming the path and the prefix) before it consumes the deferred tests (#324; on Linux a masked
  path reached through a symlink, relative or absolute, is masked at its real target, so a state
  under the target is refused too, #339, #341); with an account
  every sandboxed run lives under the account's own run root, never under the state.

### What a VERIFIED rests on (#303)

**Boundary.** Cairnwork guarantees tamper resistance only for acceptance observations that are
obtained across an isolation boundary and judged independently by a trusted process. A PASS from
an arbitrary pytest test that runs in the same process as the candidate's code carries no such
guarantee: candidate code imported by the test can change what the test reads or compares
(`sys.modules`, import hooks, `sys.path`, an `__eq__` that always holds), and no pytest-level rule
in that process can rule it out.

Today the only protected observation is a declared blackbox check (`cairnwork-verify --blackbox`,
#303): the owner holds the answers, the candidate's function runs in the sandbox under the verifier
account with the inputs only, and the owner compares. Core's definition of VERIFIED is unchanged,
so a verdict can still be VERIFIED on same-process tests alone. It is never silent about it: every
`cairnwork-verify run` result (`status` VERIFIED, FAILED, REFUSED or UNCOMPARABLE) has
`protection`, the level of the verifier ids Core was asked to require (`cairnwork/protection.py`).
An error output (`{"error": ...}`: a usage error, a task refused before it ran, an internal error)
has no `protection`, and no verdict either:

- `"blackbox"`: every required observation is a declared blackbox check;
- `"partial"`: some are, the rest (pytest verifiers, P2P) are same-process;
- `"same_process_only"`: none are;
- `""`: Core was never asked (refused or uncomparable).

On macOS the protected levels are `"blackbox_macos"` and `"partial_macos"`: protected, with the
macOS cross-run IPC residual above (#316), so a lower grade than the same level on Linux bwrap. A
minimum without the suffix (`"partial"`, `"blackbox"`) accepts only the Linux grade; one with it
accepts either.

A receipt with deferred tests (v0.2) records the level in its binding (docs/ATTESTATION.md). The
receiver checks it against the signed Core request (the ids show the level without the host grade;
the `_macos` suffix is the producer's statement), and a level above `"same_process_only"` must
come with the declaration's `blackbox_commitment` in the binding (`PROTECTION_UNCOMMITTED`
otherwise). So the `blackbox:*` ids stand for a declaration, not for the signer's naming alone: a
receiver that pins `blackbox_commitment` (computed from its own copy of the declaration) knows
which one. Without that pin the commitment is itself only the signer's value. Even with it, that
the checks really ran as blackbox runs remains the signer's claim, like every verifier run
(`assurance.verifier_runs` is `signer_claim`). A v0.1 receipt (no deferred tests) has no binding and carries no label: its level is
`protection.level(required_verifier_ids, macos=False)` over the signed request, the coverage
without a host grade, and nothing in the receipt ties those ids to a declaration.

**Strict mode** (off by default): `cairnwork-verify run --require-protection` asks for at least
`"partial_macos"`, so a protected run passes on either host (without `--blackbox` it is refused
before anything runs, error `UNPROTECTED`), and the Python API takes any protected level as the
minimum (`require_protection="partial"` for the Linux grade). Core is then asked for one more
observation, `protection`, which passes only when the level is met, so an unprotected run is not
VERIFIED (`protection: VERIFICATION_INCOMPLETE: UNPROTECTED [...]` in the reason). A receiver asks
for the same with its policy's `protection` key (`UNPROTECTED`). Verifier ids `blackbox:*` and
`protection` are reserved for this.

### The dedicated verifier account (Linux P4, Issue #84)

**Run adversarial or untrusted third-party code on Linux only under the dedicated verifier
account.** It is now available: `scripts/setup_verifier_user_linux.sh` creates an unprivileged
`cairnwork-verifier` user (the counterpart of the macOS `setup_verifier_user.sh`), and with it
the readable set inside the sandbox is that account's, which owns nothing. Unlike the macOS
Seatbelt allow-list this is still a whole-root read bind, so it closes the read-isolation gap
below only for the owner's files that their own permissions keep private (`0600`/`0700`); a
world-readable file is readable whatever the account (see the note after the trade-off). The CI
job `test-linux-verifier-account` proves it on a real runner.

The shared root (`/var/lib/cairnwork`) is group-readable by the verifier (that is how the
account reads its Python and each run's inputs), so **keep owner secrets out of it** — it is for
verifier inputs and the account's interpreter, never credentials. The per-run verifier log stays
owner-only (`0600`). The installer refuses to adopt a pre-existing `cairnwork-verifier` name that
maps to root or the owner, has a login shell, or carries extra group memberships.

Under the dedicated account the owner-home credential masks (the `--tmpfs`/`/dev/null` list that
the launching-account path applies) are **not** applied: the sandbox runs as a uid that cannot
enter the owner's `0700` home, so those mounts would fail, and the uid boundary already denies
the owner's credentials. This relies on the owner's secrets having ordinary private permissions
(`0600`/`0700`). A **world-readable** secret in the owner's home (e.g. a `0644` token under a
`0755` home) stays readable by the verifier — as it already is by every local process — so the
account is not a substitute for keeping the owner's credentials private. Masking only the
reachable (and thus leakable) owner-home paths under the account is tracked as a hardening
follow-up.

### Trade-off without the dedicated account (the launching-account fallback)

Without the account, the bwrap sandbox runs as the account that launches it, for code that is
your own or already semi-trusted. **Everything that account can read is then readable inside the
sandbox**; only the named credential list above is masked, so this is not complete read
isolation (unlike Seatbelt's allow-list). A candidate can therefore read an unlisted plaintext
secret the account owns and copy it into the retained verifier log, so **never hand a raw
verifier log to a worker or a model** (`src/cairnwork/verifiers.py` says the same). Other users'
`0700` home directories are protected by ordinary POSIX permissions.

Two consequences of the whole-root read-only bind are mitigated, not fully closed, before P4:

- **Pathname sockets.** A read-only bind does not stop `connect()` to an `AF_UNIX` pathname
  socket, and the network namespace covers only abstract sockets. A reachable privileged local
  socket hands the candidate the host — the container daemons (Docker/Podman/containerd), the
  system D-Bus, and the **systemd user bus** under `/run/user/<uid>` (a `systemd-run --user`
  there starts a process *outside* the sandbox). The daemon and system-bus sockets are replaced
  with `/dev/null` (`_DANGEROUS_SOCKETS`); the whole `/run/user` runtime tree is emptied with a
  tmpfs — every uid's subdir at once, so it holds under a P4 account uid too — which also removes
  the SSH/keyring **agent** socket that lives there.
  An agent socket an operator put at some *other* path is still reachable by a candidate that
  guesses it (scrubbing `SSH_AUTH_SOCK` from the environment hides the usual pointer but not the
  path itself) — this residual, and any privileged socket not in the list above, closes with the
  dedicated account above.
- So "writes and network are confined" holds for ordinary filesystem writes and IP networking;
  a filesystem socket to a privileged local service is the residual the masks above and the
  dedicated account address. **Run genuinely adversarial third-party code only under the
  dedicated account**, not the launching account: the whole-root read bind means the deny-list
  cannot be proven complete, and without the account its own files and sockets are the trust
  boundary.

CI on `ubuntu-24.04` lifts the AppArmor `apparmor_restrict_unprivileged_userns` restriction
with sysctl; an end user's machine needs an AppArmor profile that grants `userns` to bwrap.

### Processes that escape the run's group

A candidate test can spawn a child that leaves the run's process group (`start_new_session`),
trying to outlive the verdict and do work after it. Both backends stop that, by different means:

- **Seatbelt** cannot reap an escaped session, so `confined.py` sweeps the host process table
  for a process carrying the run token (`CAIRNWORK_VERIFIER_RUN`) outside the run's group, kills
  it, and counts it as a survivor — **a surviving escapee fails the run**, so a worker that
  leaves a process behind gets no passing verdict.
- **bwrap** runs inside an unshared PID namespace (`--unshare-all` implies `--unshare-pid`),
  where bwrap itself is init (PID 1) and the verifier its child. `confined.py` SIGKILLs the
  whole bwrap process group after the verifier exits; killing bwrap (PID 1) tears the namespace
  down and the kernel SIGKILLs every process still in it — the escapee included, since a
  `setsid` child stays in the same PID namespace (leaving one needs `CAP_SYS_ADMIN`, which
  `--cap-drop ALL` + `--disable-userns` deny). So the escapee is already dead before
  `_sweep_survivors` runs — nothing is left to sweep and the run may pass. Containment is by
  construction, not detected after the fact; it simply does not surface as a failed run. Making
  bwrap also fail the run for symmetry is the P3 reaper follow-up, not a security gap.

The invariant both enforce is that the escapee does no work. The witness test
(`test_a_process_that_leaves_the_group_is_contained`) asserts its marker stays absent; note
that because the marker path is outside the write roots, on bwrap the read-only root alone
already forbids the write, so that assertion does not by itself exercise the PID-namespace kill
(the separate write-outside and no-network witnesses, plus the reasoning above, cover it). A
dedicated witness that proves the kill directly is a P3 follow-up.

## What the worker can reach (hidden-test confidentiality)

The sandbox above confines candidate code while it is verified. The worker that writes the
candidate does not run in it: `src/cairnwork/workers/agy.py` starts agy with agy's own
`--sandbox`, which is not a security boundary (docs/DRILLS.md), on the owner's account or, with
`agy_run_as_user`, on a worker account. Cairnwork does not cut the worker's network either (a
hosted model needs it). The deferred tests (`deferred_tests.py`) stay hidden from the worker only
when all three of these hold:

1. **A separate worker account.** The private store `<state>/deferred-tests/` is closed only by
   the state directory's mode 0700 under the account that runs Cairnwork (`deferred_store.py`).
   A worker on that same account (no `agy_run_as_user`) can read it.
2. **`test_source: "bundle"`.** Only then must a test commit come from the store. Under the
   default `"auto"` a commit the store does not hold is fetched from `origin`, so a test commit
   pushed to the target repository can be fetched from there by a networked worker too.
3. **Tasks that are not public upstream.** If a task's tests or its fix exist in a public
   repository, nothing in Cairnwork stops a networked worker from finding them.

When one of these does not hold, the tests still judge the candidate, but a VERIFIED verdict no
longer shows that the worker fixed the task without having seen them.
