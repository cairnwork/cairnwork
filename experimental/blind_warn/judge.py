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
import unicodedata
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
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


def _is_hidden(ch: str) -> bool:
    cat = unicodedata.category(ch)  # C*: control, format, surrogate, private, unassigned
    return cat.startswith("C") or cat in ("Zl", "Zp")


_BATCH = 200  # blobs per `git cat-file --batch` call


def _listing(base: Path) -> tuple[list[tuple[str, str, int]], int]:
    """(name, blob id, size) of the regular files tracked at HEAD; how many names were skipped."""
    listing = run_git_bytes(base, "ls-tree", "-r", "-z", "-l", "HEAD")  # same source as the blobs
    files, skipped = [], 0
    for item in listing.split(b"\0"):
        info, tab, raw = item.partition(b"\t")
        fields = info.split()
        if not tab or len(fields) != 4 or fields[0].decode() not in _FILE_MODES:
            continue
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # a name that is not text is skipped, never a failure of the whole listing
        if any(_is_hidden(c) for c in name):
            skipped += 1  # a control character in a name could fake a header line in the prompt
            continue
        files.append((name, fields[2].decode(), int(fields[3])))
    return files, skipped


def _blobs(base: Path, oids: list[str]) -> dict[str, bytes]:
    """The contents of the given blobs, read with one `git cat-file --batch` call."""
    request = "".join(f"{oid}\n" for oid in oids).encode()
    out = run_git_bytes(base, "cat-file", "--batch", stdin=request)
    found, pos = {}, 0
    for oid in oids:
        header_end = out.index(b"\n", pos)
        header = out[pos:header_end].split()
        if len(header) != 3 or header[0].decode() != oid or header[1] != b"blob":
            raise BlindWarnError(f"unexpected cat-file output for {oid}")
        size = int(header[2])
        found[oid] = out[header_end + 1 : header_end + 1 + size]
        pos = header_end + 1 + size + 1  # the content is followed by a newline
    return found


def _text(blob: bytes) -> str:
    """The blob as run_git returned it: strict UTF-8, line endings read as text mode does."""
    return blob.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _cut(text: str, limit: int) -> str:
    """The longest prefix of text that is at most ``limit`` UTF-8 bytes (never half a character)."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def repo_context(base: Path, ticket: str) -> str:
    """Tracked text files of the base, those that share the most words with the ticket first.

    The same files and text as reading each blob in rank order (``git show``), decoding it as
    UTF-8 (a file that is not is skipped), keeping its first MAX_FILE_BYTES bytes (whole
    characters) and adding it while the total stays within CONTEXT_BYTES bytes; but a blob is read
    only when it could still fit (its size bounds its length in bytes), and blobs are read
    ``_BATCH`` at a time with one git call each, so a large repository costs a few calls, not one
    per file.
    """
    files, skipped = _listing(base)
    words = _tokens(ticket)
    ranked = sorted(
        (f for f in files if f[0].endswith(_TEXT_SUFFIXES)),
        key=lambda f: (-len(words & _tokens(f[0].replace("/", " ").replace(".", " "))), f[0]),
    )
    parts, used, i = [], 0, 0
    if skipped:
        parts.append(f"--- SKIPPED: {skipped} file names with control characters")
    while i < len(ranked):
        # the next files that could still fit; each is decided below on its real text, in order
        chunk = [f for f in ranked[i : i + _BATCH] if used + min(f[2], MAX_FILE_BYTES)
                 <= CONTEXT_BYTES]  # fmt: skip
        i += _BATCH
        try:
            blobs = _blobs(base, [oid for _, oid, _ in chunk]) if chunk else {}
        except GitCommandError:
            continue  # as one unreadable blob was skipped before
        for name, oid, _ in chunk:
            try:
                text = _cut(_text(blobs[oid]), MAX_FILE_BYTES)
            except UnicodeDecodeError:
                continue
            size = len(text.encode("utf-8"))
            if used + size > CONTEXT_BYTES:
                continue
            parts.append(f"--- FILE: {name}\n{text}")
            used += size
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
    """``failed`` split by the base run, for display and counts only (``warn`` is unchanged):
    ``regression``, the function passed on the base; ``not_fixed``, it did not pass there
    either (a blind test written from the ticket is meant to fail on the base, so this is the
    candidate not fixing the issue, or a wrong test)."""
    failed, undecided, written = classify(cand, base)
    passed_on_base = {short_name(n) for n, state in base.outcomes.items() if state == "passed"}
    failed_on_base = {short_name(n) for n, state in base.outcomes.items() if state != "passed"}
    regression = [n for n in failed if n in passed_on_base and n not in failed_on_base]
    return {
        "written": written, "failed": failed, "undecided": undecided,
        "regression": regression, "not_fixed": [n for n in failed if n not in regression],
    }  # fmt: skip


def _result(warn=False, a=None, b=None, line="", cost=0.0, error="") -> dict:
    empty = {"written": 0, "failed": [], "undecided": [], "regression": [], "not_fixed": []}
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
    kinds = [
        f"{label}: {len(set(a[key]) | set(b[key]))}"
        for key, label in (("not_fixed", "not fixed"), ("regression", "regression"))
        if a[key] or b[key]
    ]
    return f"Blind tests: A and B both fail ({', '.join(names)}); {', '.join(kinds)}; not a verdict"


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
