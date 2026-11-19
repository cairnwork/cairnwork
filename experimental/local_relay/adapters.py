"""Adapters from the local relay contracts to the existing experimental components."""

from __future__ import annotations

import http.client
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from experimental.local_relay.config import DEFAULT_LOCAL_ENDPOINT
from experimental.local_relay.diagnostics import episode_failure
from experimental.local_relay.relay import Acceptance, Attempt, LocalAttemptFailed
from experimental.local_worker.endpoint import (
    EndpointContextLimit,
    EndpointTimeout,
    EndpointUnreachable,
    parse_loopback_url,
)
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
        result = self.run_with_endpoint(context_tokens, self.endpoint_url)
        input_tokens, output_tokens = self.token_usage(result)
        return Attempt(result, self.model_id, input_tokens, output_tokens, 0.0)

    def run_with_endpoint(self, context_tokens: int, endpoint: str) -> RunResult:
        """Run this adapter's loop against another configured endpoint."""
        result = self.run_episode(context_tokens, endpoint)
        if isinstance(result, RunResult) and result.code == EndpointUnreachable.exit_code:
            raise EndpointUnreachable(
                f"local worker endpoint is unreachable; {episode_failure(result)}"
            )
        if (
            not isinstance(result, RunResult)
            or result.code != 0
            or result.checkout is None
            or not _completed_record(result.record)
        ):
            if isinstance(result, RunResult):
                raise LocalAttemptFailed(episode_failure(result))
            raise LocalAttemptFailed("local worker returned no RunResult")
        return result


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


class HaikuProxyFallback:
    """Run the local-worker loop against a loopback Haiku proxy and record proxy cost."""

    def __init__(
        self,
        endpoint_url: str,
        run_attempt: Callable[[int, str], RunResult],
        request: Callable[[str, str, dict | None], dict] | None = None,
    ) -> None:
        target = parse_loopback_url(endpoint_url)
        if not target.base_path.endswith("/v1"):
            raise ValueError("Haiku proxy URL must end in /v1")
        self.endpoint_url = str(target)
        self.label_url = (
            f"http://{target.host}:{target.port}{target.base_path.removesuffix('/v1')}/ab"
        )
        self.run_attempt = run_attempt
        self.request = request or _proxy_request

    def __call__(self, task_id: str, /) -> Attempt:
        _proxy_call(self.request, "POST", f"{self.label_url}/label", {"label": task_id})
        try:
            try:
                result = self.run_attempt(65536, self.endpoint_url)
            except (EndpointContextLimit, EndpointTimeout, EndpointUnreachable) as exc:
                raise LocalAttemptFailed(f"Haiku local-worker failed: {exc}") from exc
        finally:
            _proxy_call(self.request, "POST", f"{self.label_url}/label", {"label": "idle"})
        if (
            not isinstance(result, RunResult)
            or result.code != 0
            or result.checkout is None
            or not _completed_record(result.record)
        ):
            if isinstance(result, RunResult):
                raise LocalAttemptFailed(episode_failure(result))
            raise LocalAttemptFailed("Haiku local-worker returned no RunResult")
        stats = _proxy_call(self.request, "GET", f"{self.label_url}/stats?label={task_id}", None)
        if stats.get("budget_stop") is True:
            raise LocalAttemptFailed("Haiku proxy stopped at its budget")
        if stats.get("model_mismatch") is True:
            raise LocalAttemptFailed("Haiku proxy reported a model mismatch")
        usd = stats.get("cost_usd")
        input_tokens, output_tokens = stats.get("input_tokens"), stats.get("output_tokens")
        if (
            not isinstance(usd, (int, float))
            or isinstance(usd, bool)
            or not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or usd <= 0
            or input_tokens < 0
            or output_tokens < 0
            or type(stats.get("budget_stop")) is not bool
            or type(stats.get("model_mismatch")) is not bool
            or stats["budget_stop"]
            or stats["model_mismatch"]
            or type(stats.get("requests")) is not int
            or stats["requests"] <= 0
            or type(stats.get("ok")) is not int
            or stats["ok"] <= 0
            or type(stats.get("failed")) is not int
            or stats["failed"] != 0
        ):
            raise LocalAttemptFailed("Haiku proxy returned invalid or unknown cost stats")
        return Attempt(result, "claude-haiku-5-5", input_tokens, output_tokens, float(usd))


def _proxy_request(method: str, url: str, body: dict | None) -> dict:
    parts = urlsplit(url)
    target = parse_loopback_url(f"{parts.scheme}://{parts.netloc}{parts.path}")
    connection = http.client.HTTPConnection(target.host, target.port, timeout=30)
    try:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        path = target.base_path + ("?" + parts.query if parts.query else "")
        connection.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            raise RuntimeError(f"proxy returned HTTP {response.status}")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("proxy response must be a JSON object")
        return data
    finally:
        connection.close()


def _proxy_call(request, method: str, url: str, body: dict | None) -> dict:
    try:
        return request(method, url, body)
    except LocalAttemptFailed:
        raise
    except (OSError, http.client.HTTPException, RuntimeError, ValueError) as exc:
        raise LocalAttemptFailed(f"Haiku proxy {method} failed: {exc}") from exc


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


def _completed_record(record: Any) -> bool:
    verification = record.get("verification", {}) if isinstance(record, dict) else {}
    feedback = record.get("spec_feedback", {}) if isinstance(record, dict) else {}
    return (
        isinstance(verification, dict)
        and verification.get("exit_reason") == "COMPLETED"
        and isinstance(feedback, dict)
        and feedback.get("needs_spec") is not True
    )
