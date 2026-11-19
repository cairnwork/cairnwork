"""Haiku gate over the loopback proxy: text only, no tools, per-review cost from /ab/stats.

The reviewed text is a model-written candidate (untrusted input), so the gate is a plain chat
completion with no ``tools``/``tool_choice`` and no agentic CLI. Same Haiku route as the fallback
and the G1-G3 experiments: ``POST <proxy>/v1/chat/completions`` after labelling the proxy
``<task>-gate-<stage>``, then ``GET /ab/stats?label=...`` for the real cost.
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable, Sequence
from urllib.parse import quote

from experimental.local_relay.adapters import MeteredGateReply, _flag_set, _is_flag, _proxy_request
from experimental.local_worker.endpoint import parse_loopback_url
from experimental.pause.client import HaikuGate, TransportResult
from experimental.pause.interfaces import GateRequest
from experimental.pause.schema import Reason

HAIKU_MODEL = "claude-haiku-5-5"
CHAT_TIMEOUT = 180.0

Request = Callable[[str, str, "dict | None"], dict]


class _ChatTransport:
    """PAUSE Transport that sends one plain chat completion (no tools) to the proxy."""

    def __init__(self, chat_url: str, request: Request) -> None:
        self.chat_url, self.request = chat_url, request

    def send(
        self, model: str, effort: str, messages: Sequence[dict], timeout: float
    ) -> TransportResult:
        body = {"model": HAIKU_MODEL, "messages": [dict(m) for m in messages]}
        try:
            reply = self.request("POST", self.chat_url, body)
        except Exception:  # noqa: BLE001 - any proxy failure is a refusal, never a success
            return TransportResult(status=-1, body=None)
        choices = reply.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        if not isinstance(message, dict) or message.get("tool_calls"):
            return TransportResult(status=200, body=None)
        content = message.get("content")
        return TransportResult(
            status=200, body={"text": content} if isinstance(content, str) else None
        )


class ProxyGate:
    def __init__(self, endpoint_url: str, request: Request | None = None, scanner=None) -> None:
        target = parse_loopback_url(endpoint_url)
        if not target.base_path.endswith("/v1"):
            raise ValueError("Haiku proxy URL must end in /v1")
        self.endpoint_url = str(target)
        self.label_url = (
            f"http://{target.host}:{target.port}{target.base_path.removesuffix('/v1')}/ab"
        )
        self.request: Request = request or functools.partial(_proxy_request, timeout=CHAT_TIMEOUT)
        self.stage = "gate"
        self._gate = HaikuGate(
            _ChatTransport(f"{self.endpoint_url}/chat/completions", self.request), scanner
        )

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def review(self, request: GateRequest) -> MeteredGateReply:
        label = f"{request.task_id}-gate-{self.stage}"
        refused = MeteredGateReply(
            None, HAIKU_MODEL, request.settings.effort, Reason.PAUSE_GATE_REFUSED
        )
        try:
            self.request("POST", f"{self.label_url}/label", {"label": label})
        except Exception:  # noqa: BLE001 - an unlabelled review would be unattributed spend
            return refused
        try:
            reply = self._gate.review(request)
        finally:
            with contextlib.suppress(Exception):  # a failed reset shows up in the stats check
                self.request("POST", f"{self.label_url}/label", {"label": "idle"})
        try:
            stats = self.request("GET", f"{self.label_url}/stats?label={quote(label)}", None)
        except Exception:  # noqa: BLE001 - unknown cost is never recorded as zero
            return refused
        cost = _valid_cost(stats)
        if cost is None:
            return refused
        usd, input_tokens, output_tokens = cost
        return MeteredGateReply(
            reply.verdict,
            HAIKU_MODEL,
            reply.effort,
            reply.reason,
            input_tokens,
            output_tokens,
            usd,
        )


def _valid_cost(stats: dict) -> tuple[float, int, int] | None:
    usd, tokens_in, tokens_out = (
        stats.get("cost_usd"),
        stats.get("input_tokens"),
        stats.get("output_tokens"),
    )
    if (
        not isinstance(usd, (int, float))
        or isinstance(usd, bool)
        or usd <= 0
        or not all(type(t) is int and t >= 0 for t in (tokens_in, tokens_out))
        or not _is_flag(stats.get("budget_stop"))
        or not _is_flag(stats.get("model_mismatch"))
        or _flag_set(stats["budget_stop"])
        or _flag_set(stats["model_mismatch"])
        or type(stats.get("requests")) is not int
        or stats["requests"] <= 0
    ):
        return None
    return float(usd), int(tokens_in), int(tokens_out)  # type: ignore[arg-type]
