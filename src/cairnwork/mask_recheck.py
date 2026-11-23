"""A run whose credential masks did not hold for the whole run is never counted (#353).

A bwrap mask sits on one filesystem object. A host that re-points a credential link, replaces or
renames a masked file or directory, or creates a credential path or socket tree mid-run leaves the
real secret readable for the rest of the run. ``guard`` records the masked objects before the run
and, after a run that returned normally, checks that the same objects are still there and that the
policy built again from the same inputs is the same text. Any difference, or any error while
checking, raises ``MaskChanged`` (code CREDENTIAL_MASK_CHANGED): fail closed, nothing is counted.

Categories only: for a file this is detection after the fact, not prevention (a read already done
inside the run is not undone), and a change that is undone before the check is not seen.
docs/SECURITY.md has the limits. Seatbelt is not affected (an allow-list checked per access).
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cairnwork_sandbox.backend import Policy, SandboxError

_MAX_NAMES = 6
_Identity = tuple[int, int, int] | None  # (st_dev, st_ino, S_IFMT); None = absent


class MaskChanged(SandboxError):
    code = "CREDENTIAL_MASK_CHANGED"


def mask_paths(text: str) -> list[str]:
    """The paths the policy masks: the line after ``--tmpfs``, and the second line after
    ``--ro-bind`` when that is followed by ``/dev/null``."""
    lines = text.split("\n")
    found: list[str] = []
    for i, line in enumerate(lines[:-1]):
        if line == "--tmpfs":
            found.append(lines[i + 1])
        elif line == "--ro-bind" and lines[i + 1] == "/dev/null" and i + 2 < len(lines):
            found.append(lines[i + 2])
    return found


def _identity(path: str) -> _Identity:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


class _Held:
    """The masked objects as they were at entry; with ``O_PATH`` (Linux) each existing one is held
    open, so its inode number cannot be reused by a replacement while the run lasts."""

    def __init__(self) -> None:
        self.fds: list[int] = []
        self.identity: dict[str, _Identity] = {}

    def add(self, path: str) -> None:
        before = _identity(path)
        self.identity[path] = before
        flag = getattr(os, "O_PATH", None)
        if before is None or flag is None:
            return
        fd = os.open(path, flag | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.fds.append(fd)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)) != before:
            raise MaskChanged(f"{_label(path, {})} changed while the policy was being built")

    def close(self) -> None:
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()


def _home_names(backend: Any, policy: Policy) -> dict[str, str]:
    """real path of each credential name under the policy's home -> that name."""
    home = policy.inputs[2] if policy.inputs else None
    real_home = os.path.realpath(home or Path.home())
    return {
        os.path.realpath(f"{real_home}/{name}"): name
        for name in getattr(backend, "masked_home_paths", ())
    }


def _label(path: str, names: dict[str, str]) -> str:
    return names.get(path) or os.path.basename(path.rstrip("/")) or "a credential mask"


def _changed_message(paths: list[str], names: dict[str, str]) -> str:
    labels = sorted({_label(p, names) for p in paths})
    shown = ", ".join(labels[:_MAX_NAMES]) + (" ..." if len(labels) > _MAX_NAMES else "")
    return (
        f"the credential masks did not hold for the whole run (changed: {shown or 'unknown'}); "
        "the run is not counted and a retry is safe (docs/SECURITY.md)"
    )


def _identity_changes(held: _Held) -> list[str]:
    return [path for path, before in held.identity.items() if _identity(path) != before]


def _verify(backend: Any, policy: Policy, held: _Held) -> None:
    """Raise MaskChanged when the masks differ now; any error while checking counts as a change."""
    names: dict[str, str] = {}
    try:
        names = _home_names(backend, policy)
        changed = _identity_changes(held)
        if not changed:
            rebuilt = backend.policy(*policy.inputs)  # type: ignore[misc]
            if rebuilt.text != policy.text:
                old, new = set(mask_paths(policy.text)), set(mask_paths(rebuilt.text))
                changed = sorted(old ^ new)
                if not changed:  # a difference outside the mask list (tree links, reach)
                    changed = ["a credential mask"]
    except (OSError, SandboxError, ValueError) as exc:
        if isinstance(exc, MaskChanged):
            raise
        raise MaskChanged(
            f"the credential masks could not be re-checked ({type(exc).__name__}); "
            "the run is not counted and a retry is safe (docs/SECURITY.md)"
        ) from exc
    if changed:
        raise MaskChanged(_changed_message(changed, names))


@contextmanager
def guard(backend: Any, policy: Policy) -> Iterator[None]:
    """Around one sandboxed run: raise MaskChanged after it unless every mask held for the whole
    run. A body that raised propagates as it is. A bwrap policy without its build inputs cannot be
    checked, so it is refused before the run (fail closed). Other backends: no-op."""
    if getattr(backend, "name", None) != "bwrap":  # a test fake may carry no name
        yield
        return
    if policy.inputs is None:
        raise MaskChanged("the sandbox policy carries no build inputs to re-check its masks")
    held = _Held()
    try:
        try:
            for path in mask_paths(policy.text):
                held.add(path)
        except OSError as exc:
            raise MaskChanged(
                f"the credential masks could not be recorded ({type(exc).__name__})"
            ) from exc
        yield
        _verify(backend, policy, held)
    finally:
        held.close()
