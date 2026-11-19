"""Adapters from the local relay contracts to the existing experimental components."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experimental.local_relay.config import DEFAULT_LOCAL_ENDPOINT
from experimental.local_relay.relay import Acceptance, Attempt
from experimental.local_worker.endpoint import EndpointUnreachable
from experimental.pause.client import CommandTransport, HaikuGate
from experimental.pause.flow import RunResult
from experimental.pause.interfaces import GateReply, GateRequest


@dataclass(frozen=True)
class MeteredGateReply(GateReply):
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


class LocalWorkerAdapter:
    """Run one prepared local-worker episode at the relay's requested context cap.

    ``run_episode`` owns the task/checkout/canary setup and is injectable so tests never start
    an agent or contact the endpoint. A failed/incomplete episode is not a candidate.
    """

    def __init__(
        self,
        run_episode: Callable[[int, str], RunResult],
        model_id: str = "local-9b",
        token_usage: Callable[[RunResult], tuple[int, int]] | None = None,
        endpoint_url: str = DEFAULT_LOCAL_ENDPOINT,
    ) -> None:
        self.run_episode = run_episode
        self.model_id = model_id
        self.token_usage = token_usage or _usage_from_record
        self.endpoint_url = endpoint_url

    def __call__(self, context_tokens: int, /) -> Attempt:
        result = self.run_episode(context_tokens, self.endpoint_url)
        if isinstance(result, RunResult) and result.code == EndpointUnreachable.exit_code:
            raise EndpointUnreachable("local worker endpoint is unreachable")
        if not isinstance(result, RunResult) or result.code != 0 or result.checkout is None:
            raise RuntimeError("local worker did not produce a completed episode")
        input_tokens, output_tokens = self.token_usage(result)
        return Attempt(result, self.model_id, input_tokens, output_tokens, 0.0)


class CoreAcceptance:
    """Turn the PAUSE CoreJudge verdict into the relay acceptance record."""

    def __init__(
        self, judge: Callable[[RunResult], Any], allowed_files: Sequence[str] | None = None
    ) -> None:
        self.judge = judge
        self.allowed_files = None if allowed_files is None else frozenset(allowed_files)

    def __call__(self, candidate: Any, /) -> Acceptance:
        if not isinstance(candidate, RunResult):
            raise TypeError("Core acceptance requires a local-worker RunResult")
        if self.allowed_files is not None:
            changed = _changed_paths(candidate)
            unexpected = sorted(changed - self.allowed_files)
            if unexpected:
                return Acceptance(
                    False,
                    json.dumps(
                        {"status": "REFUSED", "unexpected_paths": unexpected}, sort_keys=True
                    ),
                )
        verdict = self.judge(candidate)
        raw = json.dumps(
            {"status": verdict.status, "tree_sha256": verdict.tree_sha256}, sort_keys=True
        )
        return Acceptance(verdict.status == "VERIFIED", raw)


def _changed_paths(result: RunResult) -> set[str]:
    if result.checkout is None or not isinstance(result.base_sha, str):
        raise ValueError("Core acceptance requires checkout and base commit")
    commands = (
        [
            "git",
            "-C",
            str(result.checkout),
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            result.base_sha,
        ],
        ["git", "-C", str(result.checkout), "ls-files", "--others", "-z"],
    )
    changed: set[str] = set()
    for command in commands:
        output = subprocess.run(command, capture_output=True, check=False)
        if output.returncode:
            raise RuntimeError("cannot compare candidate paths with its base")
        changed.update(
            path.decode("utf-8", "surrogateescape") for path in output.stdout.split(b"\0") if path
        )
    return changed


class _UsageTransport:
    """Capture the API usage returned by the injected PAUSE transport."""

    def __init__(self, transport) -> None:
        self.transport = transport
        self.input_tokens = self.output_tokens = 0

    def send(self, model, effort, messages, timeout):
        result = self.transport.send(model, effort, messages, timeout)
        usage = result.body.get("usage", {}) if result.body else {}
        self.input_tokens = _int_field(usage, "input_tokens", "input_tokens")
        self.output_tokens = _int_field(usage, "output_tokens", "output_tokens")
        return result


class CostGate:
    """HaikuGate wrapper that attaches token counts and configured per-token cost."""

    def __init__(
        self,
        transport=None,
        *,
        command: Sequence[str] | None = None,
        input_usd_per_million: float = 0.8,
        output_usd_per_million: float = 4.0,
        scanner=None,
    ) -> None:
        if transport is not None and command is not None:
            raise ValueError("provide either transport or command")
        command = command or (
            str(Path.home() / ".local/bin/claude-api-run"),
            "--tag",
            "cw-local-relay",
        )
        self.transport = _UsageTransport(transport or CommandTransport(command))
        self.gate = HaikuGate(self.transport, scanner=scanner)
        self.input_rate = input_usd_per_million
        self.output_rate = output_usd_per_million

    def review(self, request: GateRequest) -> MeteredGateReply:
        reply = self.gate.review(request)
        input_tokens, output_tokens = self.transport.input_tokens, self.transport.output_tokens
        usd = (input_tokens * self.input_rate + output_tokens * self.output_rate) / 1_000_000
        return MeteredGateReply(
            reply.verdict,
            reply.model_id,
            reply.effort,
            reply.reason,
            input_tokens,
            output_tokens,
            usd,
        )


class AgyFallback:
    """Adapt a caller-owned formal AgyWorker invocation to the relay fallback contract."""

    def __init__(
        self,
        agy_worker,
        run_attempt: Callable[[Any, str], Any],
        quota_units: Callable[[Any], int] | None = None,
    ) -> None:
        self.agy_worker = agy_worker
        self.run_attempt = run_attempt
        self.quota_units = quota_units or (lambda _result: 0)
        self.last_quota_units = 0

    def __call__(self, task_id: str, /) -> Attempt:
        result = self.run_attempt(self.agy_worker, task_id)
        self.last_quota_units = self.quota_units(result)
        candidate = getattr(result, "candidate", result)
        return Attempt(candidate, "agy", usd=0.0)


class AgyQuotaLedger:
    """Add the fallback worker's separate quota measure to its ledger row."""

    def __init__(self, ledger, fallback: AgyFallback) -> None:
        self.ledger = ledger
        self.fallback = fallback

    def append(self, row: dict[str, Any]) -> None:
        if row.get("model_id") == "agy":
            row = {**row, "quota_units": self.fallback.last_quota_units}
        self.ledger.append(row)


def _int_field(value: dict, key: str, alternative: str) -> int:
    found = value.get(key, value.get(alternative, 0))
    return found if type(found) is int and found >= 0 else 0


def _usage_from_record(result: RunResult) -> tuple[int, int]:
    record = result.record if isinstance(result.record, dict) else {}
    totals = record.get("tokens_total", {})
    if isinstance(totals, dict):
        return (
            _int_field(totals, "prompt", "prompt"),
            _int_field(totals, "completion", "completion"),
        )
    return (
        _int_field(record, "input_tokens", "input_tokens"),
        _int_field(record, "output_tokens", "output_tokens"),
    )
