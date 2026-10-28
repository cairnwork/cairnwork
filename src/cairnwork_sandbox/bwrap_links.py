"""Where and how bwrap mounts a credential mask, including when the credential path is, or runs
through, a symlink.

bwrap sets its mounts up inside a temporary root, so it resolves an absolute symlink (for
example home-manager's ``~/.config -> /nix/store/...``) against that root, where the target does
not exist, and the whole run fails at mount (#341). A relative one happens to work. So a path
reached through any link is masked at its real target instead, wherever that is: the secret the
link points at stays hidden, and the literal link is left alone (it only names the target).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable

from cairnwork_sandbox.backend import SandboxError


def mask_target(
    literal: str,
    real_home: str,
    writes: Iterable[str],
    reads: Iterable[str],
    too_broad: tuple[str, ...],
    check: Callable[[str], str],
) -> list[str]:
    """The bwrap flags that mask ``literal``: a ``--tmpfs`` over a directory, ``/dev/null`` bound
    read-only over anything else (a file, a socket). The type is the real target's, not the one
    the credential name suggests: a ``.netrc`` that is a directory, or an ``.ssh`` that is a file,
    would otherwise fail the whole run at mount ("Destination is not a file", #378 V5).

    A path with no link on it is masked where it is. Otherwise at the link's real target, after
    ``check`` (the policy's path check, which refuses a target that could rewrite a policy line).
    Refused with ``SandboxError``: a dangling link (bwrap cannot mount over it, and a target that
    appears later would be unmasked); a link target that is a system root or covers ``$HOME`` or
    a write root; and any mask, linked or not, that covers a read root (#378 V4). A tmpfs there
    would hide the run itself or the interpreter it reads, which fails later and more obscurely
    than a clear refusal now. (A write root under a plain credential path under ``$HOME`` is the
    documented deployment error that ``cairnwork-verify`` refuses, docs/SECURITY.md.)"""
    real = os.path.realpath(literal)
    if not os.path.exists(literal):  # the caller saw it lexist: a dangling link or a link loop
        raise SandboxError(
            f"credential path {literal!r} is a symlink to {real!r}, which does not exist;"
            " remove the dangling link or create its target"
        )
    named = [("read root", p) for p in reads]
    if real != literal:
        real = check(real)
        named += [("$HOME", real_home)] + [("write root", p) for p in writes]
    prefix = real.rstrip("/") + "/"
    covered = [f"{kind} {p!r}" for kind, p in named if p == real or p.startswith(prefix)]
    if real in too_broad or covered:
        how = f"links to {real!r}, which" if real != literal else "is a path that"
        raise SandboxError(
            f"credential path {literal!r} {how} a mask cannot cover without hiding"
            f" {covered or [real]}; point the link elsewhere or move the run"
        )
    return ["--tmpfs", real] if os.path.isdir(real) else ["--ro-bind", "/dev/null", real]
