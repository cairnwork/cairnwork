#!/usr/bin/env python3
"""Environment smoke test for onboarding an external repo to Cairnwork.

The slow part of trying Cairnwork on a new repo is not the AI task; it is finding
the environment couplings (writes next to the repo, git safe.directory, the venv's
python not on PATH, network) one at a time, each after a dispatched run fails. This
lists them all up front: run a repo's own already-green tests twice — once bare, and
once through Cairnwork's real verification path — and compare.

Accuracy comes from running the sandbox side through ``verifiers.run_verifiers``
itself (not a hand-rolled profile), so this measures exactly what verification does
and stays correct as that path changes (#33/#36/#40). ``account=None`` means no
VerifierAccount exclusive lock, so probes for different repos run in parallel on one
host. Only pytest repos are covered, because ``parse_verifier`` accepts only
``python3 -m pytest … tests/*.py`` (node/conda come later).

A repo is ENV_OK when its green tests stay green under the sandbox, ENV_COUPLING when
they pass bare but fail under the sandbox (the reason is classified from the verifier
log), and REPO_TESTS_NOT_GREEN when they do not pass bare — in which case the repo
cannot serve as an environment probe at all. A run that passes except for skipped tests is
SKIPPED_TESTS: the verifier rejects skips, so the target must name tests that run on this host.

Scope and honest limits:
- This probes the ``account=None`` path (same uid as the caller). Couplings that only
  appear when verification runs under a separate ``VerifierAccount`` (a different uid) —
  notably git ``safe.directory`` ownership and file-permission handoffs — are NOT covered
  here and can still surface on a real dispatch. A cross-uid probe is route (b), deferred.
- The bare run happens first and in no sandbox, so a test that writes a global resource
  (a fixed host port, ``/tmp/app.sock``, a shared cache) can leave state the sandbox run
  then reuses; and parallel probes can contend on such fixed resources. Keep tests
  hermetic, or lower ``workers`` / run serially when they are not.
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from cairnwork.workspace import create_attempt_clone, remove_attempt_clone

# Ordered signatures for classifying a sandbox-only failure from the verifier log.
_SIGNS = (
    ("WRITE_OUTSIDE_REPO", re.compile(r"deny file-write|Operation not permitted|Read-only file")),
    (
        "GIT",
        re.compile(r"dubious ownership|safe\.directory|could not read Username|not a git repo"),
    ),
    ("PATH", re.compile(r"command not found|No such file or directory: '(?:python|git|node)")),
    ("NETWORK", re.compile(r"Could not resolve host|Network is unreachable|deny network")),
)


@dataclass(frozen=True)
class RepoProbe:
    repo: Path  # a git repo
    targets: tuple[str, ...]  # that repo's own green tests, e.g. ("tests/test_a.py",)
    python: str  # absolute path to the venv python that runs those tests


@dataclass(frozen=True)
class ProbeResult:
    repo: str
    # ENV_OK | ENV_COUPLING | SKIPPED_TESTS | REPO_TESTS_NOT_GREEN | SKIP_BARE_TIMEOUT
    # | MIRROR_MULTI_BRANCH
    verdict: str
    reason: str


def _head(repo: Path) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _bare_is_green(probe: RepoProbe, commit: str, run_root: Path, timeout: int) -> bool | None:
    """Run the repo's tests with no sandbox in a clean checkout. None if it times out.

    Runs in its own session and, on timeout, kills the whole process group, so a test that
    spawns a background process does not leave an orphan holding the checkout open."""
    clone_id = f"bare-{secrets.token_hex(6)}"
    checkout = create_attempt_clone(probe.repo, commit, run_root, clone_id)
    home = run_root / f"home-{secrets.token_hex(6)}"
    home.mkdir()
    env = {k: os.environ[k] for k in ("LANG", "LC_ALL") if k in os.environ}
    # Match the sandbox side (verifiers #33): the venv's bin leads PATH, so a test that spawns
    # plain ``python`` gets the same interpreter here as under verification. Without this a repo
    # that shells out to ``python`` fails bare and is misreported REPO_TESTS_NOT_GREEN.
    env["PATH"] = os.pathsep.join(
        filter(None, (str(Path(probe.python).parent), os.environ.get("PATH")))
    )
    env.update(HOME=str(home), TMPDIR=str(home), PYTHONDONTWRITEBYTECODE="1")
    argv = [probe.python, "-m", "pytest", "-q", *probe.targets]
    process = subprocess.Popen(  # noqa: S603 - argv from parse_verifier shape, no shell
        argv,
        cwd=checkout.path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        process.wait(timeout=timeout)
        return process.returncode == 0
    except subprocess.TimeoutExpired:
        return None
    finally:
        # Kill the group by the leader's pid (== pgid under start_new_session), not os.getpgid:
        # after a normal wait() the leader is already reaped, so getpgid would raise and a child
        # the test backgrounded would survive. killpg(pid) still reaches the group while a member
        # lives; suppress only the case where the whole group is already gone.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        remove_attempt_clone(run_root, clone_id)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _include_applies(repo: Path, key: str, scratch: Path) -> bool:
    """Whether an includeIf condition holds, asked of git itself. Unsure counts as holding."""
    if not key.startswith("includeif.") or key.startswith("includeif.hasconfig:"):
        return True  # hasconfig: depends on the real config; fail closed
    cond = key[len("includeif.") : -len(".path")].replace("\\", "\\\\").replace('"', '\\"')
    (scratch / "marker.cfg").write_text("[cairn]\n\tm = 1\n")
    (scratch / "cond.cfg").write_text(f'[includeIf "{cond}"]\n\tpath = {scratch / "marker.cfg"}\n')
    got = _run_git(
        repo, "config", "--file", str(scratch / "cond.cfg"), "--includes", "--get", "cairn.m"
    )
    if got.returncode == 1 and not got.stderr.strip():
        return False
    return True


def _include_problem(repo: Path) -> str | None:
    """A reason if a config include target that git would read is missing or unreadable."""
    listing = _run_git(
        repo, "config", "--no-includes", "--show-origin", "--null", "--get-regexp",
        r"^include(if\..*)?\.path$",
    )  # fmt: skip
    if listing.returncode not in (0, 1) or listing.stderr.strip():
        return f"could not list config includes (git exit {listing.returncode})"
    parts = listing.stdout.split("\0")  # --null: "<origin>\0<key>\n<value>\0" per entry
    with tempfile.TemporaryDirectory() as scratch:
        for origin, entry in zip(parts[0::2], parts[1::2], strict=False):
            key, _, value = entry.partition("\n")
            if not origin.startswith("file:"):
                continue
            target = Path(value).expanduser()
            if not target.is_absolute():
                target = (repo / origin.removeprefix("file:")).parent / target
            if (not os.access(target, os.R_OK) or not target.is_file()) and _include_applies(
                repo, key, Path(scratch)
            ):
                return f"config include {value!r} is missing or unreadable"
    return None


def _config_files() -> list[Path]:
    """The global config files git reads, resolved the way git does (an unreadable system config
    makes git itself fail, which the exit-code check refuses)."""
    env = os.environ
    if "GIT_CONFIG_GLOBAL" in env:  # then git reads no ~/.gitconfig and no XDG config
        files = [Path(env["GIT_CONFIG_GLOBAL"])]
    else:
        home = os.path.expanduser("~")
        xdg = env.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if os.path.isabs(xdg) else Path(home) / ".config"
        files = [Path(home) / ".gitconfig", base / "git" / "config"]
    return files


def _unreadable(path: Path) -> bool:
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False  # absent, or a dangling link: git ignores it
    except OSError:
        return True  # e.g. a parent that cannot be searched
    return not os.access(path, os.R_OK)


def _silent_read_failure(repo: Path) -> str | None:
    """git exits 0/1 and prints nothing when it cannot read a global config, an include.path
    target or a ref directory (Issue #1120). Check those inputs ourselves; None if all fine."""
    for cfg in _config_files():
        if _unreadable(cfg):
            return f"git config {str(cfg)!r} is unreadable"
    try:
        problem = _include_problem(repo)
        common = _run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except OSError as err:
        return f"could not run git ({err})"
    if problem is not None:
        return problem
    if common.returncode != 0 or common.stderr.strip() or not common.stdout.strip():
        return f"could not locate the git dir (git exit {common.returncode})"
    for sub in ("heads", "remotes"):
        top = Path(common.stdout.strip()) / "refs" / sub
        if not top.is_dir():  # absent, or a reftable stub: nothing to walk
            continue
        unreadable: list[OSError] = []
        for _dir, _dirs, _files in os.walk(top, onerror=unreadable.append):
            pass
        if unreadable:
            return f"{unreadable[0].filename!r} is unreadable"
    return None


def _mirror_multi_branch_reason(repo: Path) -> str | None:
    """A reason if repo is a mirror whose origin is not single-branch (Issue #76), else None.
    A repo with no origin fetch refspec (a local probe checkout) is not applicable.
    A git failure is a reason too (Issue #1106): only exit 1 (key unset) means not applicable."""
    unsure = "refusing to treat the repo as single-branch"
    try:
        got = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            ["git", "-C", str(repo), "config", "--get-all", "remote.origin.fetch"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as err:
        return f"could not read remote.origin.fetch ({err}); {unsure}"
    if got.returncode not in (0, 1):
        return f"could not read remote.origin.fetch (git exit {got.returncode}); {unsure}"
    # git can exit 0/1 while having failed to read part of its config (Issue #1120): any stderr
    # output means the answer is not trustworthy, so refuse before trusting the exit code.
    if got.stderr.strip():
        return f"git reported a problem reading remote.origin.fetch (stderr not empty); {unsure}"
    silent = _silent_read_failure(repo)
    if silent is not None:
        return f"{silent}; {unsure}"
    if got.returncode == 1:
        return None
    specs = [s for s in got.stdout.splitlines() if s.strip()]
    advice = "re-clone with: git clone --single-branch --branch main <url>"
    if not specs:
        return None
    # The source of "[+]<src>:<dst>" must be exactly main; a wildcard or another branch is refused.
    if any(spec.lstrip("+").split(":", 1)[0] != "refs/heads/main" for spec in specs):
        return f"mirror not single-branch; {advice}"
    # The config only governs future fetches; refuse a mirror that already holds a non-main branch.
    try:
        refs = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            [
                "git",
                "-C",
                str(repo),
                "for-each-ref",
                "--format=%(refname)",
                "refs/heads/",
                "refs/remotes/origin/",
            ],  # fmt: skip
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as err:
        return f"could not list refs ({err}); {unsure}"
    if refs.returncode != 0:
        return f"could not list refs (git exit {refs.returncode}); {unsure}"
    if refs.stderr.strip():  # e.g. a corrupt ref is skipped with a warning and exit 0 (#1120)
        return f"git reported a problem listing refs (stderr not empty); {unsure}"
    allowed = {"refs/heads/main", "refs/remotes/origin/main", "refs/remotes/origin/HEAD"}
    if any(r not in allowed for r in refs.stdout.split()):
        return f"mirror holds a branch beyond main; {advice}"
    return None


def probe_repo(probe: RepoProbe, *, timeout: int = 300) -> ProbeResult:
    spec = parse_verifier("env-smoke", ["python3", "-m", "pytest", "-q", *probe.targets])
    name = probe.repo.name
    multi = _mirror_multi_branch_reason(probe.repo)
    if multi is not None:  # a full mirror pre-fetched the hidden-test branches (Issue #76)
        return ProbeResult(name, "MIRROR_MULTI_BRANCH", multi)
    commit = _head(probe.repo)
    # resolve(): on macOS mkdtemp returns /var/folders/... (a symlink to /private/var/...); the
    # sandbox profile uses the realpath, so the checkout must too or pytest collection walks the
    # symlink and is denied at /private.
    run_root = Path(tempfile.mkdtemp(prefix="envsmoke.")).resolve()
    try:
        bare = _bare_is_green(probe, commit, run_root, timeout)
        if bare is None:
            return ProbeResult(name, "SKIP_BARE_TIMEOUT", f"bare tests exceeded {timeout}s")
        if not bare:
            return ProbeResult(name, "REPO_TESTS_NOT_GREEN", "tests do not pass bare; not a probe")
        ctx = VerifyContext(probe.repo, run_root, python=probe.python, base_commit=commit)
        run = run_verifiers(ctx, commit, (spec,), "env-smoke", timeout=timeout)[0]
        if run.observation.passed:
            return ProbeResult(name, "ENV_OK", "")
        skipped = _skip_like(run)
        if skipped is not None:
            return ProbeResult(
                name, "SKIPPED_TESTS", f"{skipped} skipped; pick targets without skips"
            )
        return ProbeResult(name, "ENV_COUPLING", _classify(run))
    finally:
        _scrub(run_root)


def _skip_like(run) -> int | None:
    """Count skip-like outcomes when that is the only reason the run was not passed.

    The verifier counts a run as passed only when every collected test is ``passed`` — a
    deliberate rule so a candidate cannot hide a failure behind a skip. So an all-pass-but-skipped
    run (exit 0, nothing mutated, not timed out) is not an environment coupling: the verification
    target just names tests that do not run on this host. Report it as SKIPPED_TESTS so the operator
    picks targets without skips, rather than ``ENV_COUPLING sandbox returncode=0`` which reads like
    an environment problem."""
    if run.timed_out or run.mutated or run.returncode != 0:
        return None
    skip = {"skipped", "xfailed", "xpassed"}
    outcomes = list(run.outcomes.values())
    skipped = sum(1 for outcome in outcomes if outcome in skip)
    if skipped and all(outcome == "passed" or outcome in skip for outcome in outcomes):
        return skipped
    return None


def _classify(run) -> str:
    # Heuristic from the verifier log: WRITE_OUTSIDE_REPO also matches a bare "Operation not
    # permitted" from git/xcrun denials; the order is a best guess, not a proof of cause.
    if run.timed_out:
        return "TIMEOUT"
    if run.mutated:
        return "WRITE_INTO_REPO:" + ",".join(run.mutated[:3])
    try:
        text = run.log_path.read_text(errors="replace")[-20000:]
    except OSError:
        text = ""
    hits = [name for name, pattern in _SIGNS if pattern.search(text)]
    return ",".join(hits) if hits else f"sandbox returncode={run.returncode}"


def probe_all(probes: list[RepoProbe], *, workers: int = 4) -> list[ProbeResult]:
    """account=None => no exclusive verifier lock => probes run in parallel on one host."""
    if not probes:
        return []
    with cf.ThreadPoolExecutor(max_workers=min(workers, len(probes))) as pool:
        return list(pool.map(probe_repo, probes))


def _scrub(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str]) -> int:
    # Each argument: <repo>::<python>::<tests/a.py,tests/b.py>
    if not argv:
        print("usage: env_smoke.py <repo>::<python>::<tests/a.py,tests/b.py> ...", file=sys.stderr)
        return 2  # no probes is a usage error, not a clean "everything OK"
    probes = []
    for item in argv:
        repo, python, targets = item.split("::", 2)
        probes.append(RepoProbe(Path(repo), tuple(targets.split(",")), python))
    results = probe_all(probes)
    print(f"{'verdict':<22}{'reason':<34}repo")
    for r in results:
        print(f"{r.verdict:<22}{r.reason:<34}{r.repo}")
    if any(r.verdict == "ENV_COUPLING" for r in results):
        return 1
    # Distinct code: a repo we could not probe at all (not green bare, or bare timed out) is not
    # the same signal as a coupling, but still not a clean onboarding.
    if any(
        r.verdict
        in ("SKIPPED_TESTS", "REPO_TESTS_NOT_GREEN", "SKIP_BARE_TIMEOUT", "MIRROR_MULTI_BRANCH")
        for r in results
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
