"""#1488: on macOS CI (CAIRNWORK_REQUIRE_SANDBOX=1) a failed Seatbelt probe fails the #1353
witnesses instead of taking the weaker UNCOMPARABLE branch, and the flag reaches the child."""

import pra1353_consume as consume
import pytest
import test_1039_1353_witnesses as witnesses


def _probe(monkeypatch, platform, available, flag):
    monkeypatch.setattr(witnesses.sys, "platform", platform)
    monkeypatch.setattr(consume, "sandbox_available", lambda: available)
    if flag is None:
        monkeypatch.delenv("CAIRNWORK_REQUIRE_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("CAIRNWORK_REQUIRE_SANDBOX", flag)


def test_a_failed_probe_fails_the_gate_when_the_sandbox_is_required(monkeypatch):
    _probe(monkeypatch, "darwin", False, "1")
    with pytest.raises(pytest.fail.Exception, match="Seatbelt probe failed"):
        witnesses._sandbox_expected()


@pytest.mark.parametrize("flag", [None, "0", ""])
def test_a_failed_probe_still_takes_the_weaker_branch_without_the_flag(monkeypatch, flag):
    _probe(monkeypatch, "darwin", False, flag)
    assert witnesses._sandbox_expected() is False


@pytest.mark.parametrize("platform, available", [("linux", False), ("darwin", True)])
def test_the_real_sandbox_is_expected_on_linux_or_with_a_working_seatbelt(
    monkeypatch, platform, available
):
    _probe(monkeypatch, platform, available, "1")
    assert witnesses._sandbox_expected() is True


@pytest.mark.parametrize("flag", [None, "1"])
def test_the_flag_reaches_the_sealed_child(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("CAIRNWORK_REQUIRE_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("CAIRNWORK_REQUIRE_SANDBOX", flag)
    seen = {}

    class Stop(Exception):
        pass

    def run(*_args, **kwargs):
        seen.update(kwargs["env"])
        raise Stop

    with pytest.raises(Stop):
        consume.launch(witnesses.PINS, run=run)
    assert seen["CAIRNWORK_REQUIRE_SANDBOX"] == (flag or "0")
