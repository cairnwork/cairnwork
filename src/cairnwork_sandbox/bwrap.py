"""Linux bubblewrap (bwrap) confinement for running untrusted candidate code.

The policy is a read-only bind of the whole root plus read-write binds for the write roots,
with a fresh /dev and /proc, every namespace unshared (so no network), all capabilities
dropped and nested user namespaces disabled. A read-only root bind does not stop connect() to a
pathname socket, which no network namespace covers, so /run (and a separate /var/run) is a
private, read-only tmpfs per run with only what a run needs put back (``bwrap_private``, #391).
Named credential directories under $HOME (and the /tmp/.X11-unix socket tree: an X server lets a
reachable client inject input and run commands outside the sandbox) are emptied with a tmpfs,
credential files are replaced by /dev/null, and the root-equivalent LXD and Incus sockets under
/var are replaced by /dev/null. See docs/SECURITY.md for the full set.

Trade-off (documented in docs/SECURITY.md): the whole root stays readable, so everything the
running account (the launching one, or the dedicated verifier account) can read is readable
inside; only the named credential list is masked. Other users' 0700 home directories are
protected by POSIX permissions. This is not complete read isolation, and a pathname socket
outside the private trees (under /tmp, /var/lib, a home) stays connectable if that account may.

On Ubuntu 24.04+ an AppArmor rule restricts unprivileged user namespaces by default:
``unshare(CLONE_NEWUSER)`` still succeeds but the new namespace holds no capabilities, so
mounting a fresh ``/proc`` or a bind fails. Lifting ``kernel.apparmor_restrict_unprivileged_userns``
(or, on an end user's machine, loading an AppArmor profile that grants ``userns``) makes
confinement work.

``available()`` probes with a real run: unshare a user and pid namespace with every
capability dropped and nested userns disabled, bind the root read-only and mount a fresh
``/proc``, then run ``true`` — the same hardening the real policy uses, so a build that
rejects one of these flags fails the probe instead of passing it and erroring on the real run.
The probe always mounts ``/proc`` on purpose: a probe that only binds the root passes on a
host where a nested ``/proc`` still fails, which would turn a dead backend into a false green.
When bwrap is missing or broken, callers run nothing (there is no unconfined fallback).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from cairnwork_sandbox.backend import Policy, SandboxError
from cairnwork_sandbox.bwrap_inner import inner_link_masks
from cairnwork_sandbox.bwrap_links import mask_target
from cairnwork_sandbox.bwrap_private import private_flags
from cairnwork_sandbox.bwrap_reach import account_may_traverse
from cairnwork_sandbox.shm import shm_flags, shm_unavailable_reason

NAME = "bwrap"

_SAFE = re.compile(r"^/[A-Za-z0-9 _./+@~-]*$")
_TOO_BROAD = (
    "/",
    "/home",
    "/tmp",
    "/var",
    "/var/tmp",
    "/usr",
    "/etc",
    "/opt",
    "/srv",
    "/root",
    "/run",
)  # noqa: S108
_CRED_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config",
    ".docker",
    ".kube",
    ".claude",
    ".codex",
    ".gemini",
    ".nexus",
    ".local/state",
    ".local/share/keyrings",  # gnome-keyring login keyring
    ".terraform.d",
)
_CRED_FILES = (
    ".netrc",
    ".git-credentials",  # plaintext git tokens
    ".npmrc",  # npm publish token
    ".pypirc",  # PyPI token
    ".pgpass",  # PostgreSQL password file
    ".my.cnf",  # MySQL client password
    ".vault-token",  # HashiCorp Vault token
    ".Xauthority",  # X11 cookie: reaching the X server lets a process inject input / run commands
)
# Root-equivalent daemon sockets outside the private /run (#391), which already hides Docker,
# Podman, containerd, the system D-Bus, libvirt and the per-user runtime tree: a read-only bind
# does not stop ``connect()`` to a pathname AF_UNIX socket. Replaced with /dev/null so connect
# fails.
_DANGEROUS_SOCKETS = (
    "/var/snap/lxd/common/lxd/unix.socket",  # LXD: root-equivalent (launch a privileged container)
    "/var/lib/lxd/unix.socket",
    "/var/lib/incus/unix.socket",  # Incus (the LXD fork): same root-equivalence
)
# The X11 server sockets, emptied with a tmpfs (the whole tree at once, uid-independent): a
# reachable X server lets a process inject input and run commands outside the sandbox.
_X11_SOCKET_DIR = "/tmp/.X11-unix"  # noqa: S108 - masked, not created

# Unshare a user + pid namespace, drop all capabilities, disable nested userns, bind the root
# read-only, mount a fresh /proc, run true. Exit 0 means bwrap can build a confining namespace
# here with the same hardening flags the real policy uses (so a build that rejects one fails
# the probe rather than passing it and then erroring on the real run).
_PROBE = (
    "--unshare-pid",
    "--unshare-user",
    "--cap-drop",
    "ALL",
    "--disable-userns",  # match the real policy: a setuid bwrap that rejects this fails the probe
    "--ro-bind",
    "/",
    "/",
    "--proc",
    "/proc",
    "true",
)


SHM_NOTE_PREFIX = "shm withheld: "


@lru_cache(maxsize=1)
def bwrap_path() -> str | None:
    """Absolute path to bwrap, or None when it is not installed (backend then unavailable).

    A relative result (a relative ``PATH`` entry) is refused: ``wrap`` runs the binary with
    ``cwd`` set to the candidate's checkout, so a relative ``bin/bwrap`` would resolve to a
    program the candidate planted and run everything unconfined. Fail closed instead.
    """
    found = shutil.which("bwrap")
    if found is None or not os.path.isabs(found):
        return None
    return os.path.realpath(found)


@lru_cache(maxsize=1)
def available() -> bool:
    """True only if bwrap can create a confining namespace here, probed with a real run.

    Fails closed on anything unexpected (missing binary, error, timeout): the caller then
    runs nothing rather than falling back to an unconfined run.
    """
    binary = bwrap_path()
    if binary is None:
        return False
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv built from constants
            [binary, *_PROBE], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _safe(path: Path | str) -> str:
    """The real path, checked so it cannot add or rewrite a line of the policy."""
    real = os.path.realpath(path)
    if not _SAFE.fullmatch(real):  # match() + '$' would accept a trailing newline
        raise SandboxError(f"path {real!r} has characters not allowed in a policy")
    return real


def _require_narrow(root: str, real_home: str) -> None:
    """A write root may not be '/', a system or temp root, $HOME, or any ancestor of $HOME."""
    if root in _TOO_BROAD or root == real_home or real_home.startswith(root.rstrip("/") + "/"):
        raise SandboxError(f"root {root!r} is too broad for a sandbox policy")


def build_policy(
    read_roots: tuple[Path, ...],
    write_roots: tuple[Path, ...],
    home: Path | None = None,
    mask_home_credentials: bool = True,
) -> Policy:
    """bwrap flags, one argv element per line; later flags override earlier ones.

    ``mask_home_credentials`` true is the launching-account path: the sandbox runs as the owner
    of ``home``, who can mount over any path in their own home, so every existing credential
    dir/file is masked. False is the dedicated verifier account (Issue #84): the sandbox runs as
    a different uid, so a tmpfs over a path it cannot even resolve (a ``0700`` home) would fail
    with EPERM. It is not all-or-nothing, though — a world-readable secret under a loosened
    (``0755``) home still leaks to that uid, so the credential paths that uid can *resolve to mount
    over* are masked (every directory on the real path enterable, ``bwrap_reach``). Those are the
    ones that can leak and can be mounted without EPERM; a correctly ``0700`` home yields none,
    unless a link in it points out to a reachable target (#358). The narrow-check still uses the
    real home, and the private /run and the socket masks (which do not touch the owner's home)
    are always applied.
    """
    real_home = _safe(home or Path.home())
    # read_roots are validated but never emitted: the whole root is already bound read-only, so
    # (unlike Seatbelt's allow-list) a read root grants no extra scope. Validating still rejects
    # a malformed path early, and a credential mask must not hide one (#378).
    reads = [_safe(root) for root in read_roots]
    writes = [_safe(root) for root in write_roots]
    for root in writes:
        _require_narrow(root, real_home)
    flags = [
        "--unshare-all",
        # --unshare-all uses --unshare-user-try (soft); --disable-userns needs the hard
        # --unshare-user, so pass it explicitly or bwrap refuses with
        # "--disable-userns requires --unshare-user".
        "--unshare-user",
        "--new-session",
        "--die-with-parent",
        "--cap-drop",
        "ALL",
        "--disable-userns",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        *shm_flags(bwrap_path() or ""),
    ]
    # The private /run goes first, so the write binds and masks below land on top of it (#391).
    private, remount = private_flags(reads, mask_home_credentials)
    flags += private
    for root in writes:
        flags += ["--bind", root, root]
    # Mask only credential paths that exist (a bind over an absent path fails on the read-only
    # root, and an absent path leaks nothing) and, off the owner path, only those the account uid
    # can both reach and read. The policy is rebuilt per run, so one created later is masked next.
    masks, mask_notes = _credential_masks(real_home, mask_home_credentials, writes, reads)
    flags += masks
    # Mask root-equivalent daemon sockets (LXD, Incus): a read-only root bind does not stop
    # connect() to a pathname socket, and no network namespace covers it. Bind over the canonical
    # path (realpath collapses duplicate entries to one existing socket; binding /dev/null onto a
    # symlink path itself makes bwrap try to create a mountpoint through it and fail). Only an
    # existing socket is masked: exists() follows the link, so an absent or dangling one adds no
    # flag.
    masked_socks: set[str] = set()
    for sock in _DANGEROUS_SOCKETS:
        real = _safe(sock) if os.path.exists(sock) else None
        if real and real not in masked_socks:
            masked_socks.add(real)
            flags += ["--ro-bind", "/dev/null", real]
    # Empty the socket trees that exist with a tmpfs (whole tree, uid-independent).
    for socket_dir in _socket_dirs_to_empty():
        flags += ["--tmpfs", _safe(socket_dir)]
    flags += remount  # last: the private trees turn read-only once everything is mounted on them
    # Surfaced by the verifier run (Policy.notes), not written into the hashed policy text.
    reason = shm_unavailable_reason(bwrap_path() or "")
    notes = ((f"{SHM_NOTE_PREFIX}{reason}",) if reason else ()) + mask_notes
    return Policy("\n".join(flags) + "\n", notes)


def _credential_masks(
    home: str, owner: bool, writes: list[str], reads: list[str]
) -> tuple[list[str], tuple[str, ...]]:
    """The credential masks under ``home`` (#341, #378), then the outside targets of the links in
    the credential directories (#377), all through the one ``mask_target`` rule."""

    def mask(path: str) -> list[str]:
        return mask_target(path, home, writes, reads, _TOO_BROAD, _safe)

    flags = [flag for rel in _home_cred_masks(home, owner) for flag in mask(f"{home}/{rel}")]
    cred_dirs = [f"{home}/{rel}" for rel in _CRED_DIRS]
    inner, notes = inner_link_masks(cred_dirs, owner, flags, mask, writes + reads)
    return flags + inner, notes


def _home_cred_masks(real_home: str, owner: bool) -> list[str]:
    """The relative path of each existing credential path under ``real_home`` to mask.

    ``owner`` true (the owner runs the sandbox): every existing credential path, since the owner
    can mount over any path in their own home. False (the dedicated account, a different uid):
    only the paths that uid can resolve to mount over without EPERM — which are exactly the ones
    that can leak to it. Read bits are not consulted: masking any reachable path is harmless, and
    reasoning about them per path is error-prone (a ``0711`` directory is unreadable yet still lets
    a known world-readable file under it leak)."""
    masks: list[str] = []
    for rel in _CRED_DIRS + _CRED_FILES:
        if not os.path.lexists(f"{real_home}/{rel}"):
            continue
        if owner or _account_can_reach(real_home, rel):
            masks.append(rel)
    return masks


def _account_can_reach(real_home: str, rel: str) -> bool:
    """True if the verifier process may resolve ``real_home/rel`` to mount over it: bwrap mounts as
    the verifier uid with its own groups, so every directory from ``/`` down to the real target's
    parent must be one it may enter (``bwrap_reach``: ``o+x``, ``g+x`` on a group holding another
    user than the owner — a home given group ``cairnwork`` included — or a POSIX ACL). The target
    is resolved with ``realpath``, inside or outside the home: an outside one (``.ssh ->
    /srv/creds``) is read through its own path, so the home's mode does not protect it (#358). One
    behind a directory that rule says the account cannot enter is not masked, since binding over
    it would EPERM and fail the run; it stays readable only if the rule is wrong for the host. The
    target's own read bits are not consulted: a reachable path is masked whatever its mode (a
    ``0711`` directory is unreadable yet still lets a known world-readable child leak). A selected
    target is masked at its real path (#341), or refused by ``mask_target`` as on the owner path
    (a system root, the home, a write root or an ancestor of either, or a dangling link: in the
    home when the account can enter it, outside it when the account can reach its parent)."""
    literal = f"{real_home}/{rel}"
    try:
        real = os.path.realpath(literal)
        if real.startswith(f"{real_home}/") and not os.path.exists(real):  # dangling, in home
            return account_may_traverse(real_home)
    except OSError:
        return False
    return account_may_traverse(os.path.dirname(real))


def _socket_dirs_to_empty() -> list[str]:
    """The socket directories to empty with a tmpfs, those that exist. Masking the whole tree
    (not a per-path or per-uid entry) is uid-independent and covers every socket under it, so it
    holds whether the candidate runs as the launching account or a dedicated verifier uid. The
    global is read each call (tests rebind it): ``/tmp/.X11-unix`` holds the X server sockets (a
    reachable X server lets a process inject input and run commands). The trees under /run
    (``/run/user``, ``/run/libvirt``) are hidden by the private /run (#391). Deduplicated by real
    path so two names for the same directory mask it once."""
    seen: set[str] = set()
    dirs = []
    for d in (_X11_SOCKET_DIR,):
        if os.path.isdir(d) and os.path.realpath(d) not in seen:
            seen.add(os.path.realpath(d))
            dirs.append(d)
    return dirs


def _binary() -> str:
    binary = bwrap_path()
    if binary is None:
        raise SandboxError("bwrap is not installed")
    return binary


def wrap(argv: list[str], policy_path: Path) -> list[str]:
    binary = _binary()
    flags = [line for line in Path(policy_path).read_text().splitlines() if line]
    return [binary, *flags, "--", *argv]


def unconfined(argv: list[str]) -> list[str]:
    """Through the bwrap binary but not isolated (the verifier account's sudoers rule only
    permits the bwrap binary, not argv itself)."""
    return [_binary(), "--bind", "/", "/", "--dev", "/dev", "--", *argv]


class BwrapBackend:
    """The Backend interface over this module. Methods look module functions up at call time,
    so a test that monkeypatches ``available`` here changes what callers see."""

    name = NAME
    masked_home_paths: tuple[str, ...] = _CRED_DIRS + _CRED_FILES
    # A credential path reached through a symlink is masked at its real target (#339, #341).
    masks_link_targets = True

    def available(self) -> bool:
        return available()

    def policy(
        self,
        read_roots: tuple[Path, ...],
        write_roots: tuple[Path, ...],
        home: Path | None = None,
        mask_home_credentials: bool = True,
        allow_ipc: bool = True,
    ) -> Policy:
        # allow_ipc is ignored (#311): --unshare-all gives every run its own IPC namespace (SysV,
        # POSIX message queues) and --dev a fresh /dev, so /dev/shm (POSIX semaphores and shared
        # memory) is a new tmpfs per run. Nothing named outlives the run or is shared with another.
        return build_policy(read_roots, write_roots, home, mask_home_credentials)

    def wrap(self, argv: list[str], policy_path: Path) -> list[str]:
        return wrap(argv, policy_path)

    def unconfined(self, argv: list[str]) -> list[str]:
        return unconfined(argv)


BACKEND = BwrapBackend()
