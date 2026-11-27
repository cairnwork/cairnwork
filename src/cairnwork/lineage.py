"""Source lineage (PRESERVED #10): where a task's base and the clone's origin come from.

A base commit must be a commit that is equal to or an ancestor of the tracked ``origin/main``,
and the working clone's ``origin`` must be the repository being served; otherwise the work is
refused before anything is dispatched or pushed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cairnwork import present
from cairnwork.gitcmd import GitCommandError, run_git

ORIGIN = re.compile(r"https://github\.com/([^/:@\s]+/[^/:@\s]+)")
COMMIT = re.compile(r"[0-9a-f]{40}")
MAIN_REFSPEC = "+refs/heads/main:refs/remotes/origin/main"
ORIGIN_MAIN = "refs/remotes/origin/main"


def verify_origin(clone: Path, repo: str) -> None:
    """Every fetch and push URL of origin must be https://github.com/<repo>.

    ``get-url --push --all origin`` is the set ``git push origin`` uses (insteadOf included),
    so the push must name the remote, never a URL built here. Fails closed: a URL with
    credentials (``user@``), a trailing slash or any other shape is refused, not normalised.
    Callers must run git through ``run_git`` (no GIT_* environment) for the push too, so the
    remote checked here is the remote pushed to."""
    for flags in (("--all",), ("--push", "--all")):
        try:
            urls = run_git(clone, "remote", "get-url", *flags, "origin").splitlines()
        except GitCommandError as exc:
            raise RuntimeError(f"LINEAGE_REMOTE_UNKNOWN: origin has no URL ({exc})") from exc
        if not any(urls):
            raise RuntimeError("LINEAGE_REMOTE_UNKNOWN: origin has no URL")
        for url in urls:
            found = ORIGIN.fullmatch(url)
            if not found:
                raise RuntimeError(f"LINEAGE_REMOTE_UNKNOWN: origin {url!r} is not a github URL")
            if found[1].lower().removesuffix(".git") != repo.lower():
                raise RuntimeError(f"LINEAGE_REPOSITORY_MISMATCH: origin {url!r} is not {repo}")


def require_on_main(clone: Path, base: str, *, fetch: bool = True) -> None:
    """base must be a commit in the history of origin/main (fetched first: a stale tracking
    ref is not trusted, though an ancestor of an older main is still an ancestor of the new)."""
    if not COMMIT.fullmatch(base):
        raise RuntimeError(f"LINEAGE_BASE_NOT_COMMIT: {base!r} is not a 40-hex commit id")
    if fetch:
        try:
            run_git(clone, "fetch", "--quiet", "origin", MAIN_REFSPEC)
        except GitCommandError as exc:
            raise RuntimeError(
                f"LINEAGE_FETCH_FAILED: could not fetch origin main ({exc})"
            ) from exc
    try:
        if run_git(clone, "cat-file", "-t", base).strip() != "commit":
            raise RuntimeError(f"LINEAGE_BASE_NOT_COMMIT: {base!r} is not a commit")
    except GitCommandError as exc:
        raise RuntimeError(f"LINEAGE_BASE_NOT_COMMIT: {base!r} is not a commit here") from exc
    if not _in_main_history(clone, base):
        raise RuntimeError(f"LINEAGE_BASE_NOT_ON_MAIN: {base} is not in origin/main")


def refuse_grafts(clone: Path) -> None:
    grafts = Path(clone, run_git(clone, "rev-parse", "--git-path", "info/grafts").strip())
    if present.exists(grafts):  # an unreadable path is raised, never read as absent (#1823)
        raise RuntimeError("LINEAGE_GRAFTS_REFUSED: .git/info/grafts rewrites history")


def _in_main_history(clone: Path, commit: str) -> bool:
    """The one ancestry test (#1266): is ``commit`` equal to or an ancestor of origin/main?

    Read from the real objects: replace refs are switched off, and a ``.git/info/grafts`` file,
    which rewrites parents, is refused outright (LINEAGE_GRAFTS_REFUSED) rather than trusted."""
    refuse_grafts(clone)
    try:
        run_git(clone, "--no-replace-objects", "merge-base", "--is-ancestor", commit, ORIGIN_MAIN)
    except GitCommandError:
        return False
    return True


def verify_single_branch(clone: Path) -> None:
    """The mirror must be single-branch (only main), so the single-commit branches that hold
    deferred hidden tests are never pre-fetched into it and reachable by the worker (Issue #76).
    A wildcard fetch refspec means a full clone that pulled every branch."""
    # check=False: git config exits 1 when the key is absent, which is the no-refspec case below,
    # not a command failure — reading it through _git would raise a generic "git config failed".
    try:
        specs = [
            s
            for s in run_git(clone, "config", "--get-all", "remote.origin.fetch").splitlines()
            if s
        ]
    except GitCommandError:
        specs = []
    advice = "re-clone with: git clone --single-branch --branch main <url>"
    code = "LINEAGE_MIRROR_NOT_SINGLE_BRANCH"
    if not specs:
        raise RuntimeError(f"{code}: origin has no fetch refspec; {advice}")
    for spec in specs:
        # The source of a fetch refspec is "[+]<src>:<dst>"; accept only main exactly, so a
        # wildcard ("refs/heads/*") or another branch ("refs/heads/mainline") is refused.
        if spec.lstrip("+").split(":", 1)[0] != "refs/heads/main":
            raise RuntimeError(f"{code}: origin fetch {spec!r} is not refs/heads/main; {advice}")
    # The config only governs future fetches; a mirror full-cloned once and later reconfigured still
    # holds the branches it already fetched. Refuse if any branch beyond main is actually present.
    allowed = {"refs/heads/main", "refs/remotes/origin/main", "refs/remotes/origin/HEAD"}
    refs = run_git(
        clone,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/",
        "refs/remotes/",
    )
    extra = [r for r in refs.split() if r not in allowed]
    if extra:
        raise RuntimeError(f"{code}: mirror holds a branch beyond main ({extra[0]}); {advice}")
    _refuse_foreign_history(clone, code, advice)
    _refuse_foreign_head(clone, code, advice)


# Refs Cairnwork writes itself, each matched by exact shape (#1266); a nested or lookalike name
# (refs/cairnwork/attempts/x/y, refs/cairnwork-bases/<short>) is not a mirror ref and is judged.
# - refs/cairnwork/attempts/<attempt_id> and ...-tested: a collected candidate
#   (workspace._fetch_head, deferred_tests.lay_tests_over); not in main's history. The id charset
#   is workspace.ATTEMPT_ID (no "/"), so the shape is one path segment.
# - refs/cairnwork-bases/<40 hex>: the intake's pin of a task base (intake._mirror_base, the full
#   lowercase commit sha); with require_base_on_main false it may sit off main.
OWN_REF = re.compile(
    r"refs/cairnwork/attempts/[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:-tested)?"
    r"|refs/cairnwork-bases/[0-9a-f]{40}"
)
# The three refs verify_single_branch's name check allows: their tips must still be on main, so a
# local main or origin/HEAD moved onto foreign history is refused (origin/main is main itself).
MAIN_REFS = frozenset({"refs/heads/main", "refs/remotes/origin/main", "refs/remotes/origin/HEAD"})


_FIELDS = "%(refname)%09%(objecttype)%09%(objectname)%09%(*objecttype)%09%(*objectname)"


def _refuse_foreign_history(clone: Path, code: str, advice: str) -> None:
    """Every other ref (tags, notes, stash, replace, ...) may name only a commit in origin/main's
    history, so the mirror carries nothing beyond main.

    One ``for-each-ref`` lists every ref with its peeled object and one ``rev-list`` gives
    origin/main's history, so the refs are judged in memory (#1391: a git process per ref cost
    about 0.23 s each). A ref that is not a commit or a one-level tag of a commit (a tag of a tag,
    a tree, a blob) is judged by the one-ref path below, which keeps its exact meaning."""
    refuse_grafts(clone)
    history: set[str] | None = None
    too_long = False
    for line in run_git(clone, "for-each-ref", f"--format={_FIELDS}").splitlines():
        ref, kind, name, peeled_kind, peeled = (line.split("\t") + ["", "", "", "", ""])[:5]
        if OWN_REF.fullmatch(ref):
            continue
        if ref.startswith(("refs/heads/", "refs/remotes/")) and ref not in MAIN_REFS:
            continue  # a stray branch is already refused by the name check above
        if ref.startswith("refs/replace/"):  # a replace ref rewrites history: always refused
            raise RuntimeError(f"{code}: mirror holds {ref}, not in main's history; {advice}")
        if kind == "commit":
            tip = name
        elif kind == "tag" and peeled_kind == "commit":
            tip = peeled
        else:
            if not _one_ref_in_main(clone, ref):
                raise RuntimeError(f"{code}: mirror holds {ref}, not in main's history; {advice}")
            continue
        if history is None and not too_long:
            history = _main_history(clone)
            too_long = history is None
        if too_long:  # a very long history: no set in memory, one git call for this ref
            judged = _in_main_history(clone, tip)
        else:
            judged = tip in (history or ())
        if not judged:
            raise RuntimeError(f"{code}: mirror holds {ref}, not in main's history; {advice}")


HISTORY_CAP = 200_000  # commits held in memory (about 20 MB); a longer history is judged ref by ref


def _main_history(clone: Path) -> set[str] | None:
    """Every commit equal to or an ancestor of origin/main, from the real objects (no replace
    refs); empty when origin/main does not resolve, as ``_in_main_history`` says no then. None
    when the history is longer than ``HISTORY_CAP``: the caller then judges each ref alone."""
    try:
        listed = run_git(
            clone, "--no-replace-objects", "rev-list", f"--max-count={HISTORY_CAP + 1}", ORIGIN_MAIN
        ).split()
    except GitCommandError:
        return set()
    return None if len(listed) > HISTORY_CAP else set(listed)


def _one_ref_in_main(clone: Path, ref: str) -> bool:
    try:  # origin/HEAD may be a symref; ^{commit} resolves it
        tip = run_git(
            clone, "--no-replace-objects", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
        ).strip()
    except GitCommandError:
        return False
    return bool(tip) and _in_main_history(clone, tip)


def _refuse_foreign_head(clone: Path, code: str, advice: str) -> None:
    """HEAD names main and main resolves to a commit, or (detached) HEAD is a commit in main's
    history: a HEAD on another branch, or on an unborn main, is a mirror state the refs checks
    above do not see (#1391). Only ``symbolic-ref -q`` exit 1 means detached; any other git
    failure propagates. A shallow mirror is judged like any other: a HEAD whose ancestry
    ``merge-base`` cannot read is refused (fail closed)."""
    detached = False
    try:
        target = run_git(clone, "symbolic-ref", "-q", "HEAD").strip()
    except GitCommandError as exc:
        cause = exc.__cause__
        if not (isinstance(cause, subprocess.CalledProcessError) and cause.returncode == 1):
            raise
        detached, target = True, "HEAD"
    if not detached and target != "refs/heads/main":
        raise RuntimeError(f"{code}: mirror HEAD points at {target}, not refs/heads/main; {advice}")
    try:
        tip = run_git(
            clone,
            "--no-replace-objects",
            "rev-parse",
            "--verify",
            "--quiet",
            f"{target}^{{commit}}",
        ).strip()
    except GitCommandError:
        tip = ""
    if not tip:
        raise RuntimeError(f"{code}: mirror HEAD ({target}) names no commit; {advice}")
    if detached and not _in_main_history(clone, tip):
        raise RuntimeError(f"{code}: mirror HEAD is not in main's history; {advice}")
