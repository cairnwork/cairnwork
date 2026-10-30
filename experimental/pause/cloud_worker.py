"""The K arm's cloud worker (#476 step 2b): ``claude -p`` edits the attempt clone.

Shaped like ``cairnwork.workers.agy.AgyWorker`` (``run(workdir, task)`` returns provenance; the
clone is judged by the same verifier and receipt) and imports only public names from the core,
one way. Model and effort are frozen: ``claude-sonnet-5-5`` and ``--effort medium``, both sent
on every call. The preliminary run goes through ``claude-api-run --tag pause-cw-k --`` (prepaid
API credit); the formal run is the same line with ``wrap_api_run=False`` (subscription).
The formal run's subscription volume goes to the lab for scheduling before it starts.

Three things are decided here before the freeze:

(a) Request count: API requests actually sent, counted as distinct assistant message ids in the
    stream-json output plus its ``api_retry`` events; with plain json, the result's
    ``num_turns``; else None and source ``UNCOUNTABLE``, never a guess. If a run cannot be
    counted, the preliminary and the formal run both move to calling the API directly.
(b) Refusal: ``stop_reason == "refusal"`` on the result or any response. When the output carries
    no stop_reason at all, a run of at most one request that exited 0 and changed nothing in
    the clone is judged a refusal, with basis ``inferred``.
(c) The summary of each run is written next to the log as ``<attempt>.claude-run.json``."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cairnwork import proc_start, provenance
from cairnwork.confined import ATTEMPT_ENV, MAX_LOG_BYTES, confirm_gone, sweep_marked
from cairnwork.unswept import WorkerMayLive
from cairnwork.workers.agy import ENV_KEEP, HIDDEN_TESTS, PROMPT

FROZEN_MODEL = "claude-sonnet-5-5"
FROZEN_EFFORT = "medium"
TOOLS = "Read,Edit,Write,Glob,Grep"
UNCOUNTABLE = "UNCOUNTABLE"


class CloudWorkerError(Exception):
    pass


class CloudWorkerUnswept(CloudWorkerError, WorkerMayLive):
    """WORKER_SWEEP_UNCLEAN: processes of the run may survive; the attempt is fenced."""


class CloudWorkerRefusal(CloudWorkerError):
    """The model declined; ``basis`` is ``stop_reason`` or ``inferred``. Never resent."""

    def __init__(self, message: str, basis: str) -> None:
        super().__init__(message)
        self.basis = basis


@dataclass(frozen=True)
class CloudWorkerConfig:
    model: str = FROZEN_MODEL
    effort: str = FROZEN_EFFORT
    timeout_seconds: int = 900
    claude: str = "claude"
    wrap_api_run: bool = True  # preliminary: True; formal: False (same line, no wrapper)
    api_run: str = "claude-api-run"
    tag: str = "pause-cw-k"

    def __post_init__(self) -> None:
        if (self.model, self.effort) != (FROZEN_MODEL, FROZEN_EFFORT):
            raise CloudWorkerError(f"model and effort are frozen: {FROZEN_MODEL} {FROZEN_EFFORT}")

    def argv(self, prompt: str) -> list[str]:
        line = [
            self.claude, "-p", prompt,
            "--model", self.model,
            "--effort", self.effort,
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--allowedTools", TOOLS,
        ]  # fmt: skip
        return [self.api_run, "--tag", self.tag, "--", *line] if self.wrap_api_run else line


@dataclass(frozen=True)
class RunSummary:
    request_count: int | None
    request_count_source: str
    stop_reason: str | None  # None: the output carries none
    is_error: bool
    models: tuple[str, ...]


def parse_output(text: str) -> RunSummary:
    """Read ``claude -p`` json (one object) or stream-json (one object per line)."""
    events: list[dict] = []
    try:
        whole = json.loads(text)
        events = [whole] if isinstance(whole, dict) else []
    except ValueError:
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    messages = [
        e["message"] for e in events
        if e.get("type") == "assistant" and isinstance(e.get("message"), dict)
    ]  # fmt: skip
    ids = {m["id"] for m in messages if isinstance(m.get("id"), str)}
    retries = sum(1 for e in events if e.get("subtype") == "api_retry")
    turns = final.get("num_turns")
    if ids:
        count, source = len(ids) + retries, "stream_responses"
    elif type(turns) is int and turns >= 0:
        count, source = turns, "num_turns"
    else:
        count, source = None, UNCOUNTABLE
    reasons = [m.get("stop_reason") for m in messages] + [final.get("stop_reason")]
    reasons = [r for r in reasons if isinstance(r, str)]
    stop = "refusal" if "refusal" in reasons else (reasons[-1] if reasons else None)
    usage = final.get("modelUsage")
    models = tuple(sorted(usage)) if isinstance(usage, dict) else ()
    return RunSummary(count, source, stop, bool(final.get("is_error")) or not final, models)


def _changed(workdir: Path) -> bool:
    out = subprocess.run(  # noqa: S603, S607 - fixed argv
        ["git", "-C", str(workdir), "status", "--porcelain", "--ignored=no"],
        capture_output=True, text=True, check=False, timeout=60,
    )  # fmt: skip
    return out.returncode != 0 or bool(out.stdout.strip())  # unreadable: not "nothing changed"


@dataclass(frozen=True)
class CloudWorker:
    config: CloudWorkerConfig = CloudWorkerConfig()

    def run(self, workdir: Path, task) -> dict[str, str]:
        config = self.config
        prompt = PROMPT.format(
            title=task.title,
            instructions=task.instructions,
            allowed=", ".join(sorted(task.allowed_files)),
            hidden_tests=HIDDEN_TESTS if getattr(task, "test_commit", "") else "",
        )
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env[ATTEMPT_ENV] = workdir.name
        mark = proc_start.boot_mark()
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            config.argv(prompt), cwd=workdir, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )  # fmt: skip
        timed_out = False
        try:
            out, _ = process.communicate(timeout=config.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            out = b""
        finally:
            swept = self._stop(process, workdir.name, mark)
        text = out.decode(errors="replace")
        log = workdir.parent / f"{workdir.name}.claude.log"
        log.write_bytes(text.encode()[: MAX_LOG_BYTES + 1])
        if not swept.gone:
            raise CloudWorkerUnswept(f"WORKER_SWEEP_UNCLEAN: {swept.problem()}; log {log}", "")
        if timed_out:
            raise CloudWorkerError(f"claude timed out; log {log}")
        got = parse_output(text)
        refusal = self._refusal(got, process.returncode, workdir)
        record = {
            "model": config.model, "effort": config.effort, "wrapped": config.wrap_api_run,
            "request_count": got.request_count, "request_count_source": got.request_count_source,
            "stop_reason": got.stop_reason, "refusal": refusal,
        }  # fmt: skip
        summary = workdir.parent / f"{workdir.name}.claude-run.json"
        summary.write_text(json.dumps(record, sort_keys=True))
        if refusal:
            raise CloudWorkerRefusal(f"claude refused ({refusal}); log {log}", refusal)
        if process.returncode != 0:
            raise CloudWorkerError(f"claude exited {process.returncode}; log {log}")
        if got.is_error:
            raise CloudWorkerError(f"claude ended with an error result; log {log}")
        observed = got.models[0] if len(got.models) == 1 else None
        return provenance.make("claude-cloud", config.model, observed)

    @staticmethod
    def _refusal(got: RunSummary, code: int, workdir: Path) -> str:
        if got.stop_reason == "refusal":
            return "stop_reason"
        few = got.request_count is not None and got.request_count <= 1
        if got.stop_reason is None and code == 0 and few and not _changed(workdir):
            return "inferred"
        return ""

    @staticmethod
    def _stop(process, attempt: str, mark: str):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()

        def look():
            return sweep_marked(ATTEMPT_ENV, attempt, skip_group=process.pid, dispatched=mark)

        return confirm_gone(look(), look)
