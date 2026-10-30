# Security notes

## Verifier sandbox backends

Candidate code runs confined by an OS sandbox; when no working backend exists nothing runs
(there is no unconfined fallback).

- **macOS Seatbelt** (`src/cairnwork_sandbox/seatbelt.py`): default deny. File contents are
  readable only from an allow-list (system libraries, the interpreter, the checkout), but file
  metadata (`file-read-metadata`: whether a path exists, its type, size, mode and times) is
  allowed for every path, and `sysctl-read` and `process-exec` are allowed with no path filter
  (#461); writes only to the checkout and the per-run directory; no network; credential stores
  denied last. Behaviour unchanged.
  Reading bytecode (`*.pyc`) under the write roots is denied, and so are creating a symlink with
  such a name there (Seatbelt matches a symlink by its target) and unlinking a `.pyc` there (so
  no symlink is swapped in for one by RENAME_SWAP), so bytecode the run plants for
  a protected file through `sys.pycache_prefix` is not loaded even when it is removed before the
  after-run scan (#368; probed spellings in `tests/test_seatbelt_pyc.py`). Writing bytecode stays
  allowed and an honest import compiles from source; a test that reads back a `.pyc` it wrote
  (py_compile output, a sourceless import from tmp) fails on macOS, and so does deleting or
  replacing a `.pyc` there (an `rmtree` of a directory holding bytecode). A `.pyc` in the candidate
  commit itself is left to the after-run scan. Linux has no such rule.
- **Linux bubblewrap** (`src/cairnwork_sandbox/bwrap.py`): a read-only bind of the whole root,
  read-write binds for the write roots only, every namespace unshared (no network), all
  capabilities dropped, nested user namespaces disabled. `/run`, `/tmp` (and `/var/run` where
  it is its own directory, not a link to `/run`) are private to each run: an empty tmpfs, made
  read-only last, with only what a run needs put back — the trees' top-level symlinks that
  resolve inside the trees, recreated at the canonical path that was checked (a link leading
  anywhere else, such as Rancher Desktop's `/var/run/docker.sock -> ~/.rd/docker.sock` or NixOS
  `/run/current-system -> /nix/store/...`, is not recreated; NixOS is #469), and each top-level
  `/etc` symlink to a regular file under one (`/etc/resolv.conf ->
  /run/systemd/resolve/stub-resolv.conf`; under the dedicated account only one it may reach; a file
  its daemon swaps between the policy build and the run start is bound as it then is, and the file
  of such a link into `/tmp`, which needs root to have made the link, any local user may swap the
  same way), and the run's own read and write roots under `/tmp` (production runs live under the
  required `--state` directory; only tests put them in `/tmp`, under pytest's temp directory): a
  read root read-only, and only one that exists when the policy is built (none another user could
  plant there before the mount), a write root writable (bwrap's `--remount-ro` does not recurse into
  a bind under the tree; Linux CI writes the run's `TMPDIR` and work directory under `/tmp`) (#391,
  `src/cairnwork_sandbox/bwrap_private.py`). Refused (`SandboxError`): a read or write root that is
  a whole tree or lies inside `/run` or `/var/run` (no caller puts one there, and binding it would
  bring host content back), a write root that holds a tree (it would cover it), and trees whose real
  paths overlap, such as `/tmp` linked into `/run` (a root under `/tmp` would then be bound into
  `/run`; `/var/run` linked to `/run` itself is one tree). **Behaviour change:** a run no longer
  sees the host's `/tmp`: a test that reads a host file under `/tmp` outside its own roots now gets
  `ENOENT` (macOS Seatbelt already denies those reads), and it writes temporary files in its
  `TMPDIR` (`tmp_path`, `tempfile`) as before.
  The masked set (all under `$HOME` unless an absolute path is given):
    - **Credential directories** emptied with a tmpfs: `.ssh`, `.aws`, `.gnupg`, `.config`,
      `.docker`, `.kube`, `.claude`, `.codex`, `.gemini`, `.nexus`, `.local/state`,
      `.local/share/keyrings`, `.terraform.d`.
    - **Credential files** replaced with `/dev/null`: `.netrc`, `.git-credentials`, `.npmrc`,
      `.pypirc`, `.pgpass`, `.my.cnf`, `.vault-token`, `.Xauthority`.
      The mount follows what the path really is, not its name: a directory gets the tmpfs and
      anything else `/dev/null` (a `.netrc` directory or an `.ssh` file no longer fails the run at
      mount, #378). A mask that would cover a read root (the interpreter, the run's guard files)
      makes the policy refuse with the root named, linked or not (#378).
      A credential path that is, or runs through, a symlink is masked at its real target, inside
      or outside `$HOME` on the launching-account path; under the dedicated account a target
      inside or outside `$HOME` is masked when the account may reach it (an outside target is read
      through its own path even from a `0700` home, #358). The account mounts with its own uid and
      groups, so a directory on the way counts as enterable on `o+x`, on `g+x` when its group holds
      any user other than the directory's owner (a home given group `cairnwork` is masked; a
      user-private group, as on the usual `0750` home, is not), or on any POSIX ACL or error
      reading one (`bwrap_reach`). A target behind a directory this rule says the account cannot
      enter is not masked, because a mount over it would fail the run; it stays readable only
      where the rule is wrong for the host. A dangling link, or one whose target
      is a system root, `$HOME`, an ancestor of it or of a write root, makes the policy refuse
      (#341); under the dedicated account only for a link it selects, so a dangling link whose
      target's parent the account cannot reach, or that sits in a home the account cannot enter
      and points inside it, is ignored. Residual: the policy is built before the run, so a credential link repointed while
      a run is in progress leaves its new target readable (main already had this for relative
      links); tracked in #353.
      A link found *inside* a credential directory (`~/.aws/credentials -> /srv/x/cred`, up to
      three levels deep) has its real target masked too when that target leaves the directory,
      by the same rule and the same reach rule under the dedicated account (#377): the
      directory's tmpfs hides the link, not the target, which stays readable at its own path.
      Targets under the package-managed prefixes `/usr`, `/nix/store`, `/gnu/store` and `/snap`
      are public content and are left alone (`~/.config/tz -> /usr/share/zoneinfo`, systemd
      user units); the exemption covers all of `/usr`, so a private file reached through
      `/usr/local` stays readable (known residual); a link to a directory under `/etc` is
      refused (a file there is masked). A target whose existence cannot be checked (a
      permission error, not ENOENT or ENOTDIR) is refused; a dangling target under a read or
      write root of the run is refused; any other dangling target is skipped and counted in the
      policy notes. Residual (the #353 kind): the policy binds the whole host root read-only,
      so a skipped target outside the private trees created while the run is in progress (say
      a daemon's socket under `/var/tmp`) is readable, a socket connectable, at its own path;
      one under `/tmp` or `/run` does not appear in the run. Each credential directory is
      scanned without following links, three levels deep (`.config/a/b/c`, where credential
      links live), up to 50,000 entries; past that, or when a directory cannot be read, the
      policy is refused rather than leave a link unscanned.
      Residual: deeper levels are not scanned (counted in the policy notes), so a link below
      them leaves its target readable. Each real target is masked once.
    - **Socket trees**: `/tmp/.X11-unix` (an X server lets a process inject input and run
      commands outside the sandbox), `/run/user` (the systemd user bus and session agents) and
      `/run/libvirt` (every libvirt daemon socket) are hidden by the private `/tmp` and `/run`.
    - **Root-equivalent daemon sockets** replaced with `/dev/null` at their real path: Docker,
      Podman, containerd, the system D-Bus, LXD and Incus (a read-only bind does not stop
      `connect()` to a pathname `AF_UNIX` socket, and the network namespace covers only abstract
      sockets). The ones listed under `/run` and `/var/run` stay on the list although the private
      `/run` hides those paths: the name there may be a link to a socket outside `/run`
      (Rancher Desktop, Colima), and that real target is what gets masked.

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
  (a masked credential dir such as `~/.config` or `~/.local/state`) or inside `/run` or `/var/run`
  (refused). The tmpfs is applied after the write binds, so
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
which one. Without that pin the commitment is itself only the signer's value. The commitment does
not say which checks Core required, so a policy that pins it also pins `blackbox_checks`, the check
ids `blackbox-commitment` prints with it (#312): the request's `blackbox:*` ids must be exactly
those, each observed only as PASS (`BLACKBOX_CHECKS_MISSING`). Under either policy shape a
`blackbox:*` id named twice in the request, or observed without being required, refuses too (#373:
the current producer builds both from one declaration, so either is an inconsistent request;
several PASS observations of one required id are still accepted). With any policy or none, an
observation of any id Core did not require that is not PASS refuses (`UNREQUIRED_OBSERVATION_FAILED`,
#413: a FAIL the receiver was not asked about is still evidence that something failed; no producer
sends one); a v0.1 receipt refuses it the same way (#430). The receiver must copy both
fields unchanged: a hand-shortened `blackbox_checks` accepts receipts that skip the dropped checks,
and nothing here can tell without the declaration. A policy written before #312 pins
the commitment alone and is accepted with a weaker check: at least one `blackbox:*` id, each PASS.
Such a receiver still accepts a receipt whose Core request left out a declared check or named one
the declaration lacks; its `assurance.blackbox_checks` is `unpinned`, so regenerate the policy
(`not_checked` means no id check ran: no policy, or no non-empty commitment pin).
What Core required is read from the contract, so both receipt versions also refuse a request whose
contract and plan require different ids, whose plan or evidence names another contract, change set
or plan, or whose `predicate.verifier_observations` differs from its observations
(`RECEIPT_INCONSISTENT`, #440): Core refuses such a request.
This guards against a producer bug; a lying signer can write the right ids. Even with these pins,
that the checks really ran as blackbox runs remains the signer's claim, like every verifier run
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
the launching-account path applies) are applied to the paths that account can reach (#93, #358,
#377; the reach rule is under "Credential files" above): the sandbox runs as a uid that cannot
enter the owner's `0700` home, so a mount there would fail and nothing there can leak, while a
named credential path it can reach (in a loosened `0755` home, or the outside target of a link)
is masked. Beyond the named list this relies on the owner's secrets having ordinary private
permissions (`0600`/`0700`): a **world-readable** secret that is not on the list (e.g. a `0644`
token under a `0755` home) stays readable by the verifier — as it already is by every local
process — so the account is not a substitute for keeping the owner's credentials private.

### Trade-off without the dedicated account (the launching-account fallback)

Without the account, the bwrap sandbox runs as the account that launches it, for code that is
your own or already semi-trusted. **Everything that account can read is then readable inside the
sandbox**; only the named credential list above is masked, so this is not complete read
isolation (unlike Seatbelt's allow-list). A candidate can therefore read an unlisted plaintext
secret the account owns and copy it into the retained verifier log, so **never hand a raw
verifier log to a worker or a model** (`src/cairnwork/verifiers.py` says the same). Other users'
`0700` home directories are protected by ordinary POSIX permissions.

Two consequences of the whole-root read-only bind are mitigated, not fully closed, in either
mode (launching account or dedicated account):

- **Pathname sockets.** A read-only bind does not stop `connect()` to an `AF_UNIX` pathname
  socket, and the network namespace covers only abstract sockets. A reachable privileged local
  socket hands the candidate the host — the container daemons (Docker/Podman/containerd), the
  system D-Bus, and the **systemd user bus** under `/run/user/<uid>` (a `systemd-run --user`
  there starts a process *outside* the sandbox) — and an unprivileged one can still carry data
  out (journald keeps what it is sent and may forward it off the host). The dedicated account
  does not close this by itself: on Linux CI a run reached journald, systemd's notify and
  varlink sockets, snapd and uuidd under `/run` in both modes, and in owner mode the launching
  account's own sockets under `/tmp` (#391). So every run gets the private, read-only `/run` and
  `/tmp` described above: no host socket under `/run`, `/var/run` or `/tmp` is connectable from a
  run, in owner or account mode, including one the host creates while the run is in progress
  (the tmpfs lives in the run's own mount namespace), and no path under them leads back out (a
  recreated link stays inside the trees; the only roots bound there are the run's own under
  `/tmp`). `tests/test_run_isolation_witness.py` (every socket the host has there) and
  `tests/test_tmp_isolation_witness.py` (sockets planted under `/tmp` before the run and after the
  policy is built) check this on real bwrap in CI. Still open: a pathname socket elsewhere —
  under `/var/tmp`, `/var/lib` (sssd's pipes, databases), a home, `/srv` — is connectable
  whenever the running account may connect to it; these were not walked by the witness. This is
  not a claim that a Linux run has no host IPC channel. An agent socket an operator
  put at such a path is reachable by a candidate that guesses it (scrubbing `SSH_AUTH_SOCK` from
  the environment hides the usual pointer but not the path itself).
  What the private `/run` and `/tmp` change for a run (besides host `/tmp` files, above): a user
  known only through systemd-userdb (a `DynamicUser`) no longer resolves inside it (the nscd and
  userdb sockets are gone, so glibc falls back to `/etc/passwd`); NixOS `/run/wrappers` (setuid
  `sudo`, `ping`) is not put back (setuid is inert under bwrap's `no_new_privs` anyway); and a
  `/run` link leading out of the tree is not recreated: Debian's `/run/shm -> /dev/shm` (`/dev/shm`
  itself is there) and NixOS `/run/current-system`, so on NixOS commands found only through
  `/run/current-system/sw/bin` are missing inside a run (#469). Multiprocessing is unaffected: its
  semaphores and shared memory live in the run's own `/dev/shm`, its sockets in `TMPDIR` (Linux CI
  runs a multiprocessing child from a test that also writes through `tmp_path` and `tempfile`).
- So "writes and network are confined" holds for ordinary filesystem writes and IP networking;
  a filesystem socket outside the private trees is the residual. **Run genuinely adversarial
  third-party code only under the dedicated account**, not the launching account: the whole-root
  read bind means the deny-list cannot be proven complete, and without the account its own files
  and sockets are the trust boundary.

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

### Recovery's sweep by working directory (#301, #310, #420)

macOS withholds the environment of Apple platform binaries (`/bin/sh`, `/bin/sleep`), so recovery
cannot see the attempt marker on them. `proc_cwd.kill_withheld_in` SIGKILLs a process of the owner
whose environment is withheld and whose working directory is inside one of the dead attempt's run
directories (`attempt_dirs`). Three residuals remain (#436); each has its own bound:

- **A platform binary with one real environment entry is taken for withheld.** On a macOS that
  does show platform binaries' environments, such a process inside a run directory is killed.
  Bound: run directories are Cairnwork scratch. Pinned with fakes by
  `tests/test_proc_cwd_residuals.py`, which also shows it goes no further: not outside the run
  directories, not for an ordinary program, an unreadable signing status or two entries. The
  `proc_cwd.py` module docstring records the same kind of case for a legitimately empty
  environment: it is tied only by its directory too, Cairnwork's own parent shell included.
- **PID reuse.** `os.kill` takes a number: macOS has no pidfd to bind the kill to the process
  checked. A process that took the pid before the first start-time read is the one whose start
  time, environment and directory are checked, so it is killed only if it passes those checks
  itself (the first residual above). The kill can reach a process other than the one checked only
  if the pid was reused after the first start-time read: between the two reads, by a process whose
  start time is equal to the microsecond (`_started` compares seconds and microseconds), or after
  the second read, before `os.kill` (no wait in between). Either way the kill runs as the owner,
  so it reaches only a process of the owner's uid: Cairnwork never runs this sweep as root
  (recovery runs in the caller's own process, `cairnwork-route recover`, and no sudo step of
  Cairnwork runs it; an operator who starts recovery as root lifts this bound). macOS hands out
  pids in turn up to 99,999 before wrapping, so reuse within either window is unlikely in practice.
- **Directory checks are by path.** Recovery resolves the run directories to realpaths when it
  lists them, `kill_withheld_in` resolves them again at the start of every sweep (`confirm_gone`
  sweeps again after `CONFIRM_WAIT`), and a process's directory is resolved when it is checked; a
  run directory moved or replaced between those reads and the kill is not covered. Bound: only the
  owner can replace a top-level run directory. The owner's run roots belong to the owner, and a
  dedicated account's `runs/` is `chown $OWNER`, mode `2750` (`scripts/setup_verifier_user.sh`).

Testing rule: no live probe or mutant of `proc_cwd` on a real host (a mutant that dropped the
directory check killed every process of the user). Read the code, use a VM or sandbox, or fake the
process table and readers inside `tmp_path`.

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
