"""The Haiku gate is a text-only loopback chat completion with real per-review cost."""

import pytest

from experimental.local_relay.proxy_gate import HAIKU_MODEL, ProxyGate
from experimental.pause.interfaces import CallSettings, GateRequest
from experimental.pause.schema import Reason

URL = "http://127.0.0.1:18301/v1"
STATS = {
    "requests": 1,
    "ok": 1,
    "failed": 0,
    "cost_usd": 0.0049,
    "input_tokens": 812,
    "output_tokens": 61,
    "budget_stop": False,
    "model_mismatch": 0,
}


class FakeProxy:
    def __init__(self, verdict="ACCEPT\nreason", stats=None, chat=None, fail_on=None):
        self.calls = []
        self.verdict, self.stats, self.chat, self.fail_on = verdict, stats or STATS, chat, fail_on

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if self.fail_on and self.fail_on in url:
            raise RuntimeError("proxy down")
        if url.endswith("/chat/completions"):
            return self.chat or {"choices": [{"message": {"content": self.verdict}}]}
        if "/ab/stats" in url:
            return dict(self.stats)
        return {"label": (body or {}).get("label")}


def _request(task="task-1", plan="the candidate"):
    return GateRequest(task, "", plan, CallSettings("haiku", "medium", 60.0, 1))


def test_review_is_a_plain_chat_without_tools_and_reports_real_cost():
    proxy = FakeProxy()
    gate = ProxyGate(URL, proxy)
    gate.set_stage("retry")
    reply = gate.review(_request())
    assert (reply.verdict, reply.reason) == ("ACCEPT", None)
    assert (reply.input_tokens, reply.output_tokens, reply.usd) == (812, 61, 0.0049)
    chat = next(b for m, u, b in proxy.calls if u.endswith("/chat/completions"))
    assert set(chat) == {"model", "messages", "max_tokens"} and chat["model"] == HAIKU_MODEL
    assert chat["max_tokens"] == 2048
    assert "tools" not in chat and "tool_choice" not in chat
    steps = [(m, u.split("/ab/")[-1] if "/ab/" in u else "chat") for m, u, _ in proxy.calls]
    assert steps == [
        ("POST", "label"),
        ("POST", "chat"),
        ("POST", "label"),
        ("GET", "stats?label=task-1-gate-retry"),
    ]
    assert proxy.calls[0][2] == {"label": "task-1-gate-retry"}
    assert proxy.calls[2][2] == {"label": "idle"}


def test_reject_verdict_is_passed_through_with_its_cost():
    reply = ProxyGate(URL, FakeProxy(verdict="REJECT\nbad")).review(_request())
    assert reply.verdict == "REJECT" and reply.usd == 0.0049


@pytest.mark.parametrize("fail_on", ["/label", "/ab/stats"])
def test_label_or_stats_failure_is_a_refusal_never_zero_cost_success(fail_on):
    reply = ProxyGate(URL, FakeProxy(fail_on=fail_on)).review(_request())
    assert reply.verdict is None and reply.reason is Reason.PAUSE_GATE_REFUSED
    assert reply.usd == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"cost_usd": 0},
        {"cost_usd": True},
        {"budget_stop": 1},
        {"model_mismatch": 2},
        {"model_mismatch": "0"},
        {"requests": 0},
        {"input_tokens": -1},
    ],
)
def test_invalid_or_unsafe_stats_fail_closed(bad):
    reply = ProxyGate(URL, FakeProxy(stats={**STATS, **bad})).review(_request())
    assert reply.verdict is None and reply.reason is Reason.PAUSE_GATE_REFUSED


def test_a_tool_call_in_the_answer_is_bad_output_not_a_verdict():
    chat = {"choices": [{"message": {"content": "ACCEPT", "tool_calls": [{"id": "x"}]}}]}
    reply = ProxyGate(URL, FakeProxy(chat=chat)).review(_request())
    assert reply.verdict is None and reply.reason is not None


def test_non_loopback_or_wrong_path_proxy_urls_are_rejected():
    with pytest.raises(ValueError):
        ProxyGate("http://example.com:18301/v1", FakeProxy())
    with pytest.raises(ValueError):
        ProxyGate("http://127.0.0.1:18301", FakeProxy())


def test_secret_in_the_candidate_is_blocked_before_any_chat_request():
    proxy = FakeProxy()
    reply = ProxyGate(URL, proxy).review(_request(plan="key sk-ant-api03-" + "A" * 40))
    assert reply.reason is Reason.PAUSE_SECRET_BLOCKED
    assert not any(u.endswith("/chat/completions") for _m, u, _b in proxy.calls)


def test_blocked_request_keeps_its_original_reason_when_no_spend_was_metered():
    unmetered = {**STATS, "requests": 0, "cost_usd": 0, "input_tokens": 0, "output_tokens": 0}
    proxy = FakeProxy(stats=unmetered)
    reply = ProxyGate(URL, proxy).review(_request(plan="path /Users/someone/repo/file.py"))
    assert reply.reason is Reason.PAUSE_SECRET_BLOCKED  # not overwritten by "refused"
    assert reply.verdict is None and reply.usd == 0.0
    assert not any(u.endswith("/chat/completions") for _m, u, _b in proxy.calls)


def test_a_verdict_with_unknown_cost_is_still_refused():
    reply = ProxyGate(URL, FakeProxy(stats={**STATS, "requests": 0})).review(_request())
    assert reply.verdict is None and reply.reason is Reason.PAUSE_GATE_REFUSED


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"verdict": "可直接合併", "reason": "ok"}', "ACCEPT\nok"),
        ('{"verdict": "需修改", "reason": "漏了一項"}', "REJECT\n漏了一項"),
        ('{"verdict": "不可用", "reason": "x"}', "REJECT\nx"),
        ('thinking...\n```json\n{"verdict": "可直接合併", "reason": "r"}\n```', "ACCEPT\nr"),
        ("ramble\nACCEPT", "ACCEPT"),
        ("ACCEPT\nbecause it is fine", "ACCEPT"),
        ("ramble\nREJECT", "REJECT"),
        ("ramble\nACCEPT\nmore", None),
        ("ACCEPT\nREJECT", None),
        ('{"verdict": "可直接合併", "reason": "a"}\n{"verdict": "需修改", "reason": "b"}', None),
        ('{"verdict": "需修改", "reason": "b"}\n{"verdict": "可直接合併", "reason": "a"}', None),
        ('{"verdict": "可直接合併", "reason": "a"}\nbut actually it is wrong', None),
        ('{"verdict": "可直接合併", "reason": "a"} {"verdict": "可直接合併", "reason": "a"}', None),
        ('not a verdict {"verdict": "ok"} text\n{"verdict": "需修改", "reason": "r"}', "REJECT\nr"),
        ("I think ACCEPT is right", None),
        ('{"verdict": "maybe", "reason": "x"}', None),
        ("", None),
    ],
)
def test_judge_reply_parsing_is_tolerant_but_never_guesses(text, expected):
    from experimental.local_relay.proxy_gate import parse_verdict

    assert parse_verdict(text) == expected


def test_a_json_verdict_reply_gates_through_pause_and_keeps_the_raw_text():
    chat = {
        "choices": [{"message": {"content": '{"verdict": "可直接合併", "reason": "沒有缺陷"}'}}]
    }
    reply = ProxyGate(URL, FakeProxy(chat=chat)).review(_request())
    assert reply.verdict == "ACCEPT" and reply.reason is None
    assert "可直接合併" in reply.text


def test_an_unparseable_reply_is_bad_output_and_its_text_is_kept_for_diagnosis():
    chat = {"choices": [{"message": {"content": "hmm, hard to say"}}]}
    reply = ProxyGate(URL, FakeProxy(chat=chat)).review(_request())
    assert reply.verdict is None and reply.reason is Reason.PAUSE_GATE_BAD_OUTPUT
    assert reply.text == "hmm, hard to say"
