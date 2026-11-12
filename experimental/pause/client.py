"""Configured Haiku clients and minimal command transport (#476 step 2)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from experimental.pause.interfaces import GateReply, GateRequest, PlanReply, PlanRequest
from experimental.pause.schema import GATE_VERDICTS, Reason
from experimental.pause.secrets_scan import Scanner


@dataclass(frozen=True)
class TransportResult:
    status: int
    body: dict | None
    timed_out: bool = False
    quota_blocked: bool = False


class Transport(Protocol):
    def send(
        self, model: str, effort: str, messages: Sequence[dict], timeout: float
    ) -> TransportResult: ...


class CommandTransport:
    """Run a caller configured JSON transport command; credentials belong to that command."""

    def __init__(self, command: Sequence[str]) -> None:
        self.command = tuple(command)

    def send(
        self, model: str, effort: str, messages: Sequence[dict], timeout: float
    ) -> TransportResult:
        payload = json.dumps({"model": model, "effort": effort, "messages": messages})
        try:
            result = subprocess.run(
                self.command,
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TransportResult(status=0, body=None, timed_out=True)
        try:
            envelope = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            envelope = None
        if not isinstance(envelope, dict):
            return TransportResult(status=result.returncode, body=None)
        body = envelope.get("body")
        status = envelope.get("status", result.returncode)
        return TransportResult(
            status=status if isinstance(status, int) and not isinstance(status, bool) else -1,
            body=body if isinstance(body, dict) else None,
            timed_out=bool(envelope.get("timed_out", False)),
            quota_blocked=bool(envelope.get("quota_blocked", False)),
        )


class _Client:
    def __init__(self, transport: Transport, scanner: Scanner) -> None:
        self.transport, self.scanner = transport, scanner

    @staticmethod
    def _settings(request: PlanRequest | GateRequest) -> tuple[str, str, float, int]:
        settings = request.settings
        return settings.model_id, settings.effort, settings.timeout_seconds, settings.max_attempts

    def _send(self, request: PlanRequest | GateRequest, messages: Sequence[dict], planner: bool):
        model, effort, timeout, attempts = self._settings(request)
        pieces = list(_strings(messages))
        # each string, and the pieces newline-joined (nothing the real request does not contain)
        blocked = any(self.scanner.scan(v) for v in (*pieces, "\n".join(pieces)))
        prefix = "PAUSE_PLANNER" if planner else "PAUSE_GATE"
        if blocked:
            return None, model, effort, Reason.PAUSE_SECRET_BLOCKED
        for attempt in range(max(0, attempts)):
            result = self.transport.send(model, effort, messages, timeout)
            if result.quota_blocked:
                return None, model, effort, Reason[f"{prefix}_QUOTA_EXHAUSTED"]
            if result.timed_out:
                if attempt + 1 < attempts:
                    continue
                return None, model, effort, Reason[f"{prefix}_TIMEOUT"]
            status = result.status if isinstance(result.status, int) else -1
            if 500 <= status <= 599:
                if attempt + 1 < attempts:
                    continue
                return None, model, effort, Reason[f"{prefix}_REFUSED"]
            if status != 200:
                return None, model, effort, Reason[f"{prefix}_REFUSED"]
            body = result.body
            if body is not None and (body.get("stop_reason") == "refusal" or body.get("refusal")):
                return None, model, effort, Reason[f"{prefix}_REFUSAL"]
            if body is None or not isinstance(body.get("text"), str):
                return None, model, effort, Reason[f"{prefix}_BAD_OUTPUT"]
            text = body["text"]
            if planner:
                if not text.strip():
                    return None, model, effort, Reason.PAUSE_PLANNER_BAD_OUTPUT
                return text, model, effort, None
            first = text.strip().splitlines()[0] if text.strip() else ""
            if first not in GATE_VERDICTS:
                return None, model, effort, Reason.PAUSE_GATE_BAD_OUTPUT
            return first, model, effort, None
        return None, model, effort, Reason[f"{prefix}_REFUSED"]


class HaikuPlanner(_Client):
    def __init__(self, transport: Transport, scanner: Scanner | None = None) -> None:
        super().__init__(transport, scanner or Scanner())

    def plan(self, request: PlanRequest) -> PlanReply:
        text, model, effort, reason = self._send(request, request.messages, planner=True)
        return PlanReply(text=text, model_id=model, effort=effort, reason=reason)


class HaikuGate(_Client):
    def __init__(self, transport: Transport, scanner: Scanner | None = None) -> None:
        super().__init__(transport, scanner or Scanner())

    def review(self, request: GateRequest) -> GateReply:
        messages = ({"role": "user", "content": request.plan},)
        verdict, model, effort, reason = self._send(request, messages, planner=False)
        return GateReply(verdict=verdict, model_id=model, effort=effort, reason=reason)


def _strings(value: object) -> Iterator[str]:
    """Every string in a nested request: dict keys and values, bytes decoded, scalars as text."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (bytes, bytearray)):
        yield bytes(value).decode("utf-8", "replace")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif value is not None:
        yield str(value)
