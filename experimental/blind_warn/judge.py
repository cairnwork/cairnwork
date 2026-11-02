"""Blind-test warning line: two independent blind tests, one warning, never a verdict.

A model that sees only the ticket and the repository's conventions writes two test files (A, B).
Both run on the base and on the candidate through Cairnwork's own verifier path. When both files
have a function that fails on the candidate (and was collected on the base), the result carries a
one-line warning. Nothing here changes a verdict: every error gives warn=false plus an error text,
and no exception reaches the caller.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.intake import Intake
from cairnwork.verifiers import VerifierRun, VerifierSpec, run_verifiers
from cairnwork.verify_context import VerifyContext

DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
PROMPTS = Path(__file__).parent / "prompts"
CONTEXT_BYTES = 150_000  # repository text sent to the model, per call
MAX_FILE_BYTES = 30_000
MAX_TOKENS = 4096
RUN_TIMEOUT = 300
USD_PER_MTOK = (3.0, 15.0)  # input, output: an estimate, not a bill
_FENCE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
_FILE_MODES = ("100644", "100755")  # regular files; not symlinks (120000) or gitlinks
_TEXT_SUFFIXES = (".py", ".md", ".rst", ".txt", ".cfg", ".toml", ".ini")

ModelCall = Callable[[str, str], tuple[str, float]]  # (prompt, model) -> (text, usd)
Runner = Callable[..., tuple[VerifierRun, ...]]  # run_verifiers(ctx, commit, specs, id, timeout=)


class BlindWarnError(Exception):
    """Anything that stops a run; reported in the result's ``error``, never raised to the caller."""


def anthropic_call(prompt: str, model: str) -> tuple[str, float]:
    """One Messages API call. The key comes only from ANTHROPIC_API_KEY."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise BlindWarnError("ANTHROPIC_API_KEY is not set")
    body = json.dumps(
        {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310 (fixed https URL)
        data = json.loads(response.read())
    text = "".join(part.get("text", "") for part in data.get("content", []))
    usage = data.get("usage", {})
    usd = (
        usage.get("input_tokens", 0) * USD_PER_MTOK[0]
        + usage.get("output_tokens", 0) * USD_PER_MTOK[1]
    ) / 1_000_000
    return text, usd


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z_]{4,}", text)}


def repo_context(base: Path, ticket: str) -> str:
    """Tracked text files of the base, those that share the most words with the ticket first."""
    listing = run_git(
        base, "ls-tree", "-r", "-z", "HEAD"
    )  # one source as the blobs; -z: no quoting
    entries = (item.split("\t", 1) for item in listing.split("\0") if "\t" in item)
    names = [name for info, name in entries if info.split(" ", 1)[0] in _FILE_MODES]
    words = _tokens(ticket)
    ranked = sorted(
        (n for n in names if n.endswith(_TEXT_SUFFIXES)),
        key=lambda n: (-len(words & _tokens(n.replace("/", " ").replace(".", " "))), n),
    )
    parts, used = [], 0
    for name in ranked:
        try:
            text = run_git(base, "show", f"HEAD:{name}")[:MAX_FILE_BYTES]  # the tracked blob
        except (GitCommandError, UnicodeDecodeError):
            continue
        if used + len(text) > CONTEXT_BYTES:
            continue
        parts.append(f"--- FILE: {name}\n{text}")
        used += len(text)
    return "\n".join(parts)


def _brief(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").replace("{D}", "the files given below")


def _extract_tests(reply: str) -> str | None:
    """The test file from a reply: the longest fenced block; None for a skip or no code."""
    blocks = _FENCE.findall(reply)
    if not blocks or reply.strip().lower().startswith("skipped"):
        return None
    return max(blocks, key=len)


def _write_tests(
    call: ModelCall, model: str, ticket: str, context: str, conventions: str
) -> tuple[str | None, float]:
    prompt = (
        f"{_brief('BRIEF-test.md')}\n\nInstead of writing files, put the full text of "
        "work/test_deferred.py in one ```python block in your reply (or reply `skipped <reason>`)."
        f"\n\n=== ISSUE.md\n{ticket}\n\n=== CONVENTIONS.md\n{conventions}\n\n"
        "=== TASK.md\nThe library is the repository under src/; tests go in the tests/ directory."
        f"\n\n=== src/\n{context}"
    )
    text, usd = call(prompt, model)
    return _extract_tests(text), usd


def short_name(nodeid: str) -> str:
    return nodeid.split("::")[-1].split("[")[0]


def classify(cand: VerifierRun, base: VerifierRun) -> tuple[list[str], list[str], int]:
    """(failed, undecided, written) for one test file, from its candidate and base runs.

    A function fails only if it fails on the candidate and was collected on the base (so a base
    collection error never counts). A candidate run that did not run cleanly (exit code other than
    0 or 1, a collection error) makes the whole file undecided."""
    written = len(cand.outcomes) or len(base.outcomes)
    failing = sorted(n for n, state in cand.outcomes.items() if state == "failed")
    if cand.returncode not in (0, 1) or cand.collect_errors:
        return [], sorted({short_name(n) for n in failing} or {"(file)"}), written
    failed = sorted({short_name(n) for n in failing if n in base.outcomes})
    undecided = sorted({short_name(n) for n in failing if n not in base.outcomes})
    return failed, undecided, written


def _commit(work: Path, message: str) -> str:
    run_git(work, "add", "-A")
    run_git(
        work, "-c", "user.name=blind-warn", "-c", "user.email=blind-warn@example.invalid",
        "commit", "-q", "--allow-empty", "-m", message,
    )  # fmt: skip
    return run_git(work, "rev-parse", "HEAD").strip()


def _put(work: Path, rel: str, text: str) -> None:
    """Write a file inside the clone; refuse (BlindWarnError) when any component is a link."""
    current = work
    parts = Path(rel).parts
    for part in parts[:-1]:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            current.mkdir()
            continue
        if not stat.S_ISDIR(mode):
            raise BlindWarnError("unsafe test path")
    target = current / parts[-1]
    try:
        if not stat.S_ISREG(os.lstat(target).st_mode):
            raise BlindWarnError("unsafe test path")
        target.unlink()  # a regular file the candidate added at our path
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(target, flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)


def _verify_both(
    work: Path,
    base_sha: str,
    patch: Path,
    files: dict[str, str],
    ctx: VerifyContext,
    run: Runner,
    root: str,
) -> tuple[tuple[VerifierRun, ...], tuple[VerifierRun, ...], tuple[VerifierSpec, ...]]:
    names = {key: f"{root}/test_blind_{key}.py" for key in files}
    for key, text in files.items():
        _put(work, names[key], text)
    base_commit = _commit(work, "blind tests on base")
    run_git(work, "checkout", "-q", "--detach", base_sha)
    run_git(work, "apply", "--whitespace=nowarn", "--", str(patch))
    for key, text in files.items():
        _put(work, names[key], text)
    cand_commit = _commit(work, "candidate with blind tests")
    specs = tuple(VerifierSpec(key, (names[key],)) for key in files)
    base_runs = run(ctx, base_commit, specs, f"bw-{uuid.uuid4().hex[:8]}-b", timeout=RUN_TIMEOUT)
    cand_runs = run(ctx, cand_commit, specs, f"bw-{uuid.uuid4().hex[:8]}-c", timeout=RUN_TIMEOUT)
    return base_runs, cand_runs, specs


def _summary(cand: VerifierRun, base: VerifierRun) -> dict:
    failed, undecided, written = classify(cand, base)
    return {"written": written, "failed": failed, "undecided": undecided}


def _result(warn=False, a=None, b=None, line="", cost=0.0, error="") -> dict:
    empty = {"written": 0, "failed": [], "undecided": []}
    result = {
        "warn": warn,
        "a": a or empty,
        "b": b or empty,
        "line": line,
        "cost_usd": round(cost, 4),
    }
    if error:
        result["error"] = error
    return result


def _line(a: dict, b: dict) -> str:
    names = sorted(set(a["failed"]) | set(b["failed"]))[:6]
    return f"Blind tests: A and B both fail ({', '.join(names)}); not a verdict"


def _run(ticket, base, patch, state, model, call, runner, cost) -> dict:
    context = repo_context(base, ticket)
    conv_prompt = (
        f"{_brief('BRIEF-conv.md')}\n\nInstead of writing files, put the full text of "
        "work/conventions.md in your reply (not just OK)."
        f"\n\n=== ISSUE.md\n{ticket}\n\n=== src/\n{context}"
    )
    conventions, usd = call(conv_prompt, model)
    cost[0] += usd
    if conventions.strip().strip(".!").lower() in ("", "ok"):
        raise BlindWarnError("the conventions step returned no summary")
    files = {}
    for key in ("a", "b"):  # same brief, two separate calls
        text, usd = _write_tests(call, model, ticket, context, conventions)
        cost[0] += usd
        if text:
            files[key] = text
    if len(files) < 2:
        return _result(
            cost=cost[0],
            error="the model wrote no test for "
            + "/".join(k for k in ("a", "b") if k not in files),
        )
    intake = Intake(state)
    config = intake.config()
    account = intake.verifier_account()
    work_dir = state / "blind_warn" / uuid.uuid4().hex[:12]
    work_dir.mkdir(parents=True)
    work = work_dir / "repo"
    try:
        return _run_in(work_dir, work, base, patch, files, config, account, runner, cost)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_in(work_dir, work, base, patch, files, config, account, runner, cost) -> dict:
    state = work_dir.parent.parent
    base_sha = run_git(base, "rev-parse", "HEAD").strip()
    run_git(work_dir, "clone", "-q", "--no-hardlinks", "--", str(base), str(work))
    ctx = VerifyContext(
        work, state / "runs", account=account,
        python=config.get("verifier_python"), base_commit=base_sha,
        import_paths=tuple(config.get("import_paths") or ()),
    )  # fmt: skip
    base_runs, cand_runs, specs = _verify_both(
        work, base_sha, patch, files, ctx, runner, (config.get("test_roots") or ["tests"])[0]
    )
    by = {s.verifier_id: i for i, s in enumerate(specs)}
    a = _summary(cand_runs[by["a"]], base_runs[by["a"]])
    b = _summary(cand_runs[by["b"]], base_runs[by["b"]])
    warn = bool(a["failed"] and b["failed"])
    return _result(warn, a, b, _line(a, b) if warn else "", cost[0])


def run_blind_warn(
    ticket_file: Path,
    base: Path,
    candidate_diff: Path,
    state: Path,
    *,
    model: str = DEFAULT_MODEL,
    call_model: ModelCall | None = None,
    runner: Runner = run_verifiers,
) -> dict:
    """Return the result dict; never raises (bar KeyboardInterrupt). An error means warn=false."""
    cost = [0.0]
    try:
        if call_model is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return _result(error="ANTHROPIC_API_KEY is not set")
            call_model = anthropic_call
        ticket = Path(ticket_file).read_text(encoding="utf-8")
        return _run(ticket, Path(base), Path(candidate_diff), Path(state), model, call_model,
                    runner, cost)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - fail safe: a warning line must never block or crash the caller
        return _result(cost=cost[0], error=f"{type(exc).__name__}: {exc}"[:500])
