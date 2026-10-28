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
    resolve: Callable[[str], str],
) -> str | None:
    """The path to mount the mask for ``literal`` on, or None when there is nothing to mask.

    ``resolve`` is the policy's checked realpath (it refuses a target that could rewrite a policy
    line). A path with no link on it is returned unchanged. A dangling link holds nothing and
    bwrap could not mount over it, so it gets no mask. A target that is a system root, ``$HOME``,
    an ancestor of it, or that covers a write root is refused: a tmpfs there would hide the run
    itself, which fails later and more obscurely than a clear refusal now."""
    real = resolve(literal)
    if real == literal:
        return literal
    if not os.path.exists(real):
        return None
    prefix = real.rstrip("/") + "/"
    covered = [p for p in (real_home, *writes) if p == real or p.startswith(prefix)]
    if real in too_broad or covered:
        raise SandboxError(
            f"credential path {literal!r} links to {real!r}, which a mask cannot cover without"
            f" hiding {covered or [real]}; point the link elsewhere or move the run"
        )
    return real
