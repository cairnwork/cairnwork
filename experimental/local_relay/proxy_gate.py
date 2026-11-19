"""Haiku gate over the loopback proxy: text only, no tools, per-review cost from /ab/stats.

The reviewed text is a model-written candidate (untrusted input), so the gate is a plain chat
completion with no ``tools``/``tool_choice`` and no agentic CLI. Same Haiku route as the fallback
and the G1-G3 experiments: ``POST <proxy>/v1/chat/completions`` after labelling the proxy
``<task>-gate-<stage>``, then ``GET /ab/stats?label=...`` for the real cost.
"""

from __future__ import annotations

import contextlib
import functools
import json
import uuid
from collections.abc import Callable, Sequence
from urllib.parse import quote

from experimental.local_relay.adapters import MeteredGateReply, _flag_set, _is_flag, _proxy_request
from experimental.local_worker.endpoint import parse_loopback_url
from experimental.pause.client import HaikuGate, TransportResult
from experimental.pause.interfaces import GateRequest
from experimental.pause.schema import Reason

HAIKU_MODEL = "claude-haiku-5-5"
CHAT_TIMEOUT = 180.0
GATE_MAX_TOKENS = (
    2048  # thinking is on in the proxy (its effort/thinking are frozen); cap the total
)
# judge_v1.1 verdicts: only 可直接合併 passes; 需修改 and 不可用 are rejections.
_VERDICTS = {"可直接合併": "ACCEPT", "需修改": "REJECT", "不可用": "REJECT"}

Request = Callable[[str, str, "dict | None"], dict]


def _count_verdicts(value: object) -> int:
    """Verdict-keyed objects at any depth inside one decoded JSON value."""
    if isinstance(value, dict):
        own = isinstance(value.get("verdict"), str) and value["verdict"] in _VERDICTS
        return int(own) + sum(_count_verdicts(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_verdicts(item) for item in value)
    return 0


def parse_verdict(text: str) -> str | None:
    """Normalize a judge reply to ``ACCEPT\\n<reason>`` / ``REJECT\\n<reason>`` (or None).

    Fail-closed on any ambiguity: the candidate text is attacker-influenced and can make the model
    echo an earlier verdict. Every verdict in every form is counted (verdict-keyed JSON objects at
    any depth plus standalone ACCEPT/REJECT lines) and the reply passes only when the total is
    exactly one AND that verdict is the final element: the frozen JSON line
    (``{"verdict": ..., "reason": ...}``, top-level, a closing code fence and earlier prose are
    fine) or a standalone ACCEPT/REJECT line that is the last non-empty line. Nothing else is
    parsed, so PAUSE reports BAD_OUTPUT and the stage is not approved.
    """
    body = text.strip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    duplicate_key = False

    def reject_duplicates(pairs):
        nonlocal duplicate_key
        keys = [key for key, _value in pairs]
        duplicate_key = duplicate_key or len(keys) != len(set(keys))
        return dict(pairs)

    decoder = json.JSONDecoder(object_pairs_hook=reject_duplicates)
    objects: list[tuple[int, dict]] = []
    total = index = 0
    while (index := body.find("{", index)) != -1:
        try:
            value, end = decoder.raw_decode(body[index:])
        except ValueError:
            index += 1
            continue
        total += _count_verdicts(value)
        if isinstance(value, dict):
            objects.append((index + end, value))
        index += max(end, 1)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    marks = [i for i, line in enumerate(lines) if line in ("ACCEPT", "REJECT")]
    if duplicate_key or total + len(marks) != 1:  # a repeated key reads differently per parser
        return None
    if marks:
        return lines[-1] if marks[0] == len(lines) - 1 else None
    for end, value in objects:
        verdict = value.get("verdict")
        if isinstance(verdict, str) and verdict in _VERDICTS and not body[end:].strip():
            return f"{_VERDICTS[verdict]}\n{str(value.get('reason', ''))[:300]}"
    return None


class _ChatTransport:
    """PAUSE Transport that sends one plain chat completion (no tools) to the proxy."""

    def __init__(self, chat_url: str, request: Request) -> None:
        self.chat_url, self.request = chat_url, request
        self.last_text = ""

    def send(
        self, model: str, effort: str, messages: Sequence[dict], timeout: float
    ) -> TransportResult:
        body = {
            "model": HAIKU_MODEL,
            "messages": [dict(m) for m in messages],
            "max_tokens": GATE_MAX_TOKENS,
        }
        self.last_text = ""
        try:
            reply = self.request("POST", self.chat_url, body)
        except Exception:  # noqa: BLE001 - any proxy failure is a refusal, never a success
            return TransportResult(status=-1, body=None)
        choices = reply.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        if not isinstance(message, dict) or message.get("tool_calls"):
            return TransportResult(status=200, body=None)
        content = message.get("content")
        if not isinstance(content, str):
            return TransportResult(status=200, body=None)
        self.last_text = content
        # An unparseable or ambiguous reply is passed on as empty text (BAD_OUTPUT), never as the
        # raw reply: PAUSE would accept any text whose first line happens to be ACCEPT.
        return TransportResult(status=200, body={"text": parse_verdict(content) or ""})


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
        self._chat = _ChatTransport(f"{self.endpoint_url}/chat/completions", self.request)
        self._gate = HaikuGate(self._chat, scanner)

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def review(self, request: GateRequest) -> MeteredGateReply:
        # fresh nonce per review: the proxy's per-label totals accumulate across runs
        label = f"{request.task_id}-gate-{self.stage}-{uuid.uuid4().hex[:12]}"
        refused = MeteredGateReply(
            None, HAIKU_MODEL, request.settings.effort, Reason.PAUSE_GATE_REFUSED
        )
        try:
            self.request("POST", f"{self.label_url}/label", {"label": label})
        except Exception:  # noqa: BLE001 - an unlabelled review would be unattributed spend
            return refused
        self._chat.last_text = ""
        try:
            reply = self._gate.review(request)
        finally:
            with contextlib.suppress(Exception):  # a failed reset shows up in the stats check
                self.request("POST", f"{self.label_url}/label", {"label": "idle"})
        try:
            stats = self.request("GET", f"{self.label_url}/stats?label={quote(label)}", None)
        except Exception:  # noqa: BLE001 - unknown cost is never recorded as zero
            return self._unmetered(reply, refused)
        cost = _valid_cost(stats)
        if cost is None:
            return self._unmetered(reply, refused)
        usd, input_tokens, output_tokens = cost
        return MeteredGateReply(
            reply.verdict,
            HAIKU_MODEL,
            reply.effort,
            reply.reason,
            input_tokens,
            output_tokens,
            usd,
            self._chat.last_text,
        )

    @staticmethod
    def _unmetered(reply, refused: MeteredGateReply) -> MeteredGateReply:
        """No usable cost: keep the gate's own reason when it never produced a verdict (for
        example PAUSE_SECRET_BLOCKED sends nothing, so there is no spend to meter); a verdict
        with unknown cost is refused rather than trusted."""
        if reply.reason is not None:
            return MeteredGateReply(None, HAIKU_MODEL, reply.effort, reply.reason)
        return refused


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
