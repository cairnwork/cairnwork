"""Where bwrap mounts a credential mask when the credential path is, or runs through, a symlink.

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
    too_broad: tuple[str, ...],
    check: Callable[[str], str],
) -> str:
    """The path to mount the mask for ``literal`` on.

    A path with no link on it is returned unchanged. Otherwise the link's real target, after
    ``check`` (the policy's path check, which refuses a target that could rewrite a policy line).
    Refused with ``SandboxError``: a dangling link (bwrap cannot mount over it, and a target that
    appears later would be unmasked), and a target that is a system root, ``$HOME``, an ancestor
    of it, or that covers a write root (a tmpfs there would hide the run itself, which fails later
    and more obscurely than a clear refusal now)."""
    real = os.path.realpath(literal)
    if not os.path.exists(literal):  # the caller saw it lexist: a dangling link or a link loop
        raise SandboxError(
            f"credential path {literal!r} is a symlink to {real!r}, which does not exist;"
            " remove the dangling link or create its target"
        )
    if real == literal:
        return literal
    real = check(real)
    prefix = real.rstrip("/") + "/"
    covered = [p for p in (real_home, *writes) if p == real or p.startswith(prefix)]
    if real in too_broad or covered:
        raise SandboxError(
            f"credential path {literal!r} links to {real!r}, which a mask cannot cover without"
            f" hiding {covered or [real]}; point the link elsewhere or move the run"
        )
    return real
