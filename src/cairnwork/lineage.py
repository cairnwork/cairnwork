"""Source lineage (PRESERVED #10): where a task's base and the clone's origin come from.

A base commit must be a commit that is equal to or an ancestor of the tracked ``origin/main``,
and the working clone's ``origin`` must be the repository being served; otherwise the work is
refused before anything is dispatched or pushed.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    if grafts.exists():
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


# Namespaces Cairnwork writes itself, each with its own rule (#1266):
# - refs/cairnwork/attempts/<attempt_id> and ...-tested: a collected candidate
#   (workspace._fetch_head, deferred_tests.lay_tests_over); not in main's history.
# - refs/cairnwork-bases/<sha>: the intake's pin of a task base (intake._mirror_base); with
#   require_base_on_main false it may sit off main.
# Anything else under refs/cairnwork/ (e.g. refs/cairnwork/test) is not a mirror ref and is judged.
OWN_NAMESPACES = ("refs/cairnwork/attempts/", "refs/cairnwork-bases/")
CHECKED_NAMESPACES = ("refs/heads/", "refs/remotes/")  # judged by the branch rules above


def _refuse_foreign_history(clone: Path, code: str, advice: str) -> None:
    """Every other ref (tags, notes, stash, replace, ...) may name only a commit in origin/main's
    history, so the mirror carries nothing beyond main."""
    refuse_grafts(clone)
    for ref in run_git(clone, "for-each-ref", "--format=%(refname)").split():
        if ref.startswith(OWN_NAMESPACES + CHECKED_NAMESPACES):
            continue
        tip = ""
        if not ref.startswith("refs/replace/"):  # a replace ref rewrites history: always refused
            try:
                tip = run_git(
                    clone,
                    "--no-replace-objects",
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"{ref}^{{commit}}",
                ).strip()
            except GitCommandError:
                tip = ""
        if not tip or not _in_main_history(clone, tip):
            raise RuntimeError(f"{code}: mirror holds {ref}, not in main's history; {advice}")
