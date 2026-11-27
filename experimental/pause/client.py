"""Configured Haiku clients and minimal command transport (#476 step 2)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from experimental.pause.interfaces import GateReply, GateRequest, PlanReply, PlanRequest
from experimental.pause.schema import GATE_VERDICTS, Reason
from experimental.pause.secrets_scan import Scanner, names_credential


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
        try:
            payload = json.dumps({"model": model, "effort": effort, "messages": messages})
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
        except (OSError, TypeError, ValueError):
            return TransportResult(status=-1, body=None)  # unusable command or request
        try:
            envelope = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            envelope = None
        if not isinstance(envelope, dict):
            # exit 0 with unparseable output is a bad output, not an upstream refusal
            return TransportResult(status=200 if result.returncode == 0 else -1, body=None)
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
        categories = _secret_categories(self.scanner, messages)
        prefix = "PAUSE_PLANNER" if planner else "PAUSE_GATE"
        if categories:
            return None, model, effort, Reason.PAUSE_SECRET_BLOCKED, categories
        budget, bad_retry = max(0, attempts), False
        while budget > 0:
            budget -= 1
            result = self.transport.send(model, effort, messages, timeout)
            if result.quota_blocked:
                return None, model, effort, Reason[f"{prefix}_QUOTA_EXHAUSTED"], ()
            if result.timed_out:
                if budget > 0:
                    continue
                return None, model, effort, Reason[f"{prefix}_TIMEOUT"], ()
            status = result.status if isinstance(result.status, int) else -1
            if 500 <= status <= 599:
                if budget > 0:
                    continue
                return None, model, effort, Reason[f"{prefix}_REFUSED"], ()
            if status != 200 and (planner or not 200 <= status < 300):
                return None, model, effort, Reason[f"{prefix}_REFUSED"], ()
            body = result.body
            if body is not None and (body.get("stop_reason") == "refusal" or body.get("refusal")):
                return None, model, effort, Reason[f"{prefix}_REFUSAL"], ()
            if body is None or not isinstance(body.get("text"), str):
                return None, model, effort, Reason[f"{prefix}_BAD_OUTPUT"], ()
            text = body["text"]
            if planner:
                if not text.strip():
                    return None, model, effort, Reason.PAUSE_PLANNER_BAD_OUTPUT, ()
                return text, model, effort, None, ()
            first = text.strip().splitlines()[0] if text.strip() else ""
            if first not in GATE_VERDICTS:
                if not bad_retry:  # one extra try for an empty or non-verdict reply
                    bad_retry, budget = True, budget + 1
                    continue
                return None, model, effort, Reason.PAUSE_GATE_BAD_OUTPUT, (f"HTTP {status}",)
            if status != 200:
                return None, model, effort, Reason.PAUSE_GATE_REFUSED, ()
            return first, model, effort, None, ()
        return None, model, effort, Reason[f"{prefix}_REFUSED"], ()


class HaikuPlanner(_Client):
    def __init__(self, transport: Transport, scanner: Scanner | None = None) -> None:
        super().__init__(transport, scanner or Scanner())

    def plan(self, request: PlanRequest) -> PlanReply:
        text, model, effort, reason, diagnostics = self._send(
            request, request.messages, planner=True
        )
        return PlanReply(
            text=text,
            model_id=model,
            effort=effort,
            reason=reason,
            diagnostics=diagnostics,
        )


class HaikuGate(_Client):
    def __init__(self, transport: Transport, scanner: Scanner | None = None) -> None:
        super().__init__(transport, scanner or Scanner())

    def review(self, request: GateRequest) -> GateReply:
        messages = ({"role": "user", "content": request.plan},)
        verdict, model, effort, reason, diagnostics = self._send(request, messages, planner=False)
        return GateReply(
            verdict=verdict,
            model_id=model,
            effort=effort,
            reason=reason,
            diagnostics=diagnostics,
        )


def _secret_categories(scanner: Scanner, messages: object) -> tuple[str, ...]:
    """Scan every piece alone and every mapping item joined as 'key: value' (#1204)."""
    try:
        texts = [*_strings(messages), *_joined_items(messages)]
        return tuple(dict.fromkeys(c for text in texts for c in scanner.scan(text)))
    except Exception:  # noqa: BLE001  fail closed; never echo what broke the scanner
        return ("scanner_error",)


def _joined_items(value: object) -> Iterator[str]:
    """Each item whose key names a credential, as a JSON '{"key": value}' piece (#1204).

    Only credential keys are joined (a message's own "content" or "text" is not), so ordinary code
    in a transcript is scanned as written. The piece is quoted JSON: the scanner's rules for quoted
    values apply, nested values are carried, and a header pair ["Authorization", "Basic x"] too.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and names_credential(key) and _holds_value(item):
                yield json.dumps({key: item}, default=str)
            yield from _joined_items(item)
    elif isinstance(value, (list, tuple)):
        first = next((item for item in value if isinstance(item, str)), None)
        if (
            first is not None
            and _header_name(first)
            and names_credential(first)
            and _holds_value(value)
        ):
            yield json.dumps(list(value), default=str)
        for item in value:
            yield from _joined_items(item)


_HEADER_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _header_name(text: str) -> bool:
    """A list's first string must look like a header name, not a line of code (#1204)."""
    return _HEADER_NAME.fullmatch(text) is not None


_MIN_JOINED = 6  # the scanner's own floor for a quoted value; "next" or "4096" is not a secret


def _holds_value(value: object) -> bool:
    """A string of at least _MIN_JOINED characters or a number, at any depth."""
    if isinstance(value, str):
        return len(value) >= _MIN_JOINED
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if isinstance(value, dict):
        return any(_holds_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_value(item) for item in value)
    return False


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
