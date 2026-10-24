"""Robustness guards of the local worker loop (#88 follow-up, 1ca5's proposal; every number is a
Limits field, frozen in the manifest).

Repeat guard: consecutive turns whose tool call has the same name and byte-identical arguments
(agent_protocol.call_key) form a streak; any other turn ends it. With
``repeat_requires_unchanged_tree`` a call extends the streak only if the previous identical call
left the observed tree as it found it; "the tree" is what the loop observes after every tool call:
HEAD and the git status porcelain v2 entries (paths and their states, untracked and ignored files
included). An edit to a file that was already modified changes no entry, so it does not count as a
change (a content fingerprint would need a new wrapper query); a tree not observed counts as
changed, so an unknown never ends an episode. From ``repeat_hint_at`` (None: off) the call is not
run; the model gets REPEAT_HINT instead (error_category REPEATED). At ``repeat_stop_at`` the call
is not run either and the episode ends with REPEAT_LIMIT; the model is sent nothing more.
ADDENDUM_05 defaults: hint off, stop at 5, unchanged tree required.

Invalid calls: a BadCall (unknown tool, bad JSON, missing or extra arguments: INVALID_CALL) is a
recoverable invalid_tool_call turn; the error goes back to the model and the turn counts as a
step. ``invalid_call_limit`` of them in a row end the episode with INVALID_TOOL_CALL_LIMIT. A
structurally broken reply (ProtocolError) still aborts (ERROR_ABORT, protocol_error): Nori counts
it as a model outcome (ADDENDUM_05).

Context window: before each request the prompt must leave room for ``max_completion_tokens`` in
``context_window``. The loop first takes a cheap upper bound (prompt_bound): the last request's
reported prompt tokens plus, for every message added since, its JSON bytes and MESSAGE_OVERHEAD
(a byte-level BPE token covers at least one byte; chat-template markers per message are covered
by the overhead); with no reported count, the whole payload's bytes plus FIRST_OVERHEAD. Only when
that bound does not fit does it ask the endpoint for the exact count (endpoint.count_tokens:
Splash's /apply-template then /tokenize, the prompt the server would build); an endpoint that
cannot count ends the episode on the bound (limit_hit context_window_bound), so a request that
would not fit is never sent. A 400 that says the context is too long still maps to CONTEXT_LIMIT
(context_window_http_400).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from experimental.local_worker.agent_protocol import BadCall
from experimental.local_worker.endpoint import Endpoint, EndpointError, EndpointTimeout
from experimental.local_worker.episode import TurnLog
from experimental.local_worker.gitstate import TreeState
from experimental.local_worker.loop_config import Limits

MESSAGE_OVERHEAD = 32  # tokens per message for the template's role markers and separators
FIRST_OVERHEAD = 1024  # the template's fixed text (tool instructions) when no count is known
REPEAT_HINT = (
    "Not run: this call is identical to your previous {n} call(s), so its result would not"
    " change. Change the arguments or use another tool: {tools}."
)

REPEAT_STOP = "Not run: repeat limit reached; the episode ends."


def json_bytes(value: object) -> int:
    return len(json.dumps(value).encode())


def prompt_bound(payload: dict, known: tuple[int, int] | None) -> int:
    """An upper bound on the prompt tokens of ``payload``. ``known``: (messages in an earlier
    request, its exact prompt tokens), or None."""
    messages = payload["messages"]
    if known is None:
        fixed = json_bytes(payload.get("tools", [])) + FIRST_OVERHEAD
        return fixed + sum(json_bytes(m) + MESSAGE_OVERHEAD for m in messages)
    sent, tokens = known
    return tokens + sum(json_bytes(m) + MESSAGE_OVERHEAD for m in messages[sent:])


def tree_changed(before: TreeState | None, after: TreeState | None) -> bool | None:
    """Whether a call changed the observed tree; None when either side was not observed."""
    if before is None or after is None or not before.observed or not after.observed:
        return None
    return (before.head, before.entries) != (after.head, after.entries)


@dataclass
class RepeatGuard:
    hint_at: int | None
    stop_at: int
    requires_unchanged_tree: bool
    key: tuple[str, str] | None = None
    count: int = 0
    last_changed: bool | None = False  # whether the streak's last call changed the tree

    def next(self, key: tuple[str, str] | None) -> str:
        """Count the call ``key`` (None: no valid call this turn, which ends any streak) and say
        what to do with it: "run", "hint" or "stop"."""
        same = key is not None and key == self.key
        if same and self.requires_unchanged_tree and self.last_changed is not False:
            same = False
        self.key, self.count = key, (self.count + 1 if same else 1)
        self.last_changed = False  # until the call runs and its tree is observed
        if key is not None and self.count >= self.stop_at:
            return "stop"
        if key is not None and self.hint_at is not None and self.count >= self.hint_at:
            return "hint"
        return "run"

    def hint(self, tools: tuple[str, ...]) -> str:
        return REPEAT_HINT.format(n=self.count - 1, tools=", ".join(tools))


class GuardMixin:
    """The context-window check and the invalid-call turn of AgentLoop (which inherits them)."""

    limits: Limits
    endpoint: Endpoint
    _known: tuple[int, int] | None
    _repeat: RepeatGuard
    _invalid: int
    _payload: Callable[[list[dict]], dict]

    def _context_stop(self, messages: list[dict], deadline: float) -> tuple[str, str] | None:
        """A request whose prompt and output budget exceed the window is never sent (above).
        The bound first; the endpoint's exact count only when the bound does not fit."""
        room = self.limits.context_window - self.limits.max_completion_tokens
        payload = self._payload(messages)
        if prompt_bound(payload, self._known) <= room:
            return None
        count = getattr(self.endpoint, "count_tokens", None)
        try:
            exact = count(payload, deadline) if count is not None else None
        except EndpointTimeout:
            raise
        except EndpointError:
            exact = None  # it cannot count: end on the bound rather than send and fail
        if exact is None:
            return "CONTEXT_LIMIT", "context_window_bound"
        self._known = (len(messages), exact)
        return ("CONTEXT_LIMIT", "context_window") if exact > room else None

    def _invalid_call(
        self, log: TurnLog, messages: list[dict], assistant: dict, calls: list, exc: BadCall
    ) -> str | None:
        """An invalid_tool_call turn (guards.py): the model is told why, nothing runs and none of
        the call is published."""
        self._repeat.next(None)
        self._invalid += 1
        log.action_type, log.error_category = "invalid_tool_call", "INVALID_CALL"
        call = dict(calls[0], id=calls[0].get("id") or f"call_{log.index}")
        assistant["tool_calls"] = [call]
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(exc)})
        limit = self.limits.invalid_call_limit
        return "INVALID_TOOL_CALL_LIMIT" if self._invalid >= limit else None
