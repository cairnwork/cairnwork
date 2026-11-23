"""on-verified publishes the bytes it verified, not a second read of the file (#458)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_on_verified import Env, FakeHttp, _codes, _run

from cairnwork_attest import checkrun, on_verified
from cairnwork_attest.publish import publish_bundle

pytestmark = pytest.mark.usefixtures("production_git_env")

TOKEN = "ghs_SECRETTOKEN123"


@pytest.fixture
def env(tmp_path: Path, repo: Path) -> Env:
    return Env(tmp_path, repo)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(checkrun, "_default_http", fake)
    monkeypatch.setenv(checkrun.TOKEN_ENV, TOKEN)
    return fake


MARKER = "SWAPPED-BUNDLE-MARKER-7f3a"


def _note(bare: Path, candidate: str) -> str | None:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(bare), "notes", "--ref=refs/notes/cairnwork", "show", candidate],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _garbled_signature(raw: bytes) -> bytes:
    """The same bundle with one signature byte flipped, plus a marker in an unsigned field."""
    envelope: dict[str, Any] = json.loads(raw)
    sig = envelope["signatures"][0]
    sig["sig"] = ("A" if sig["sig"][0] != "A" else "B") + sig["sig"][1:]
    sig["marker"] = MARKER
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


def test_file_swapped_after_the_statement_step_is_not_published(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = on_verified._statement_step

    def statement_then_swap(*a: Any, **k: Any) -> Any:
        step = real(*a, **k)
        env.bundle.write_bytes(_garbled_signature(env.bundle.read_bytes()))
        return step

    monkeypatch.setattr(on_verified, "_statement_step", statement_then_swap)
    rc, out, obj = _run(capsys, env.argv())
    assert rc == 2
    assert _codes(obj)[:2] == [("statement", "OK"), ("publish", "SIGNATURE_INVALID")]
    assert _note(env.bare, env.candidate) is None
    assert MARKER not in out and http.calls == []


def test_file_swapped_while_publishing_does_not_change_the_note(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bytes] = []

    def swap_then_publish(**kw: Any) -> Any:
        seen.append(env.bundle.read_bytes())  # the bytes that were verified
        env.bundle.write_bytes(_garbled_signature(seen[0]))
        return publish_bundle(**kw)

    monkeypatch.setattr(on_verified, "publish_bundle", swap_then_publish)
    rc, out, obj = _run(capsys, env.argv())
    assert rc == 0 and _codes(obj)[1] == ("publish", "PUBLISHED")
    note = _note(env.bare, env.candidate)
    assert note is not None and MARKER not in note and MARKER not in out
    assert note == seen[0].decode()


def test_publish_without_verified_bytes_still_reads_the_file(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plain publish command is unchanged: it reads the file itself."""
    assert _run(capsys, env.argv("--no-check-run"))[0] == 0
    assert publish_bundle(env.bundle, env.repo, env.candidate).code == "NOOP"


def test_plain_publish_reads_the_file_once(
    env: Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cairnwork_attest import publish

    assert _run(capsys, env.argv("--no-check-run"))[0] == 0
    calls: list[Path] = []
    real = publish.read_bundle_bytes
    monkeypatch.setattr(publish, "read_bundle_bytes", lambda p: calls.append(p) or real(p))
    assert publish_bundle(env.bundle, env.repo, env.candidate).code == "NOOP"
    assert calls == [env.bundle]
    env.bundle.unlink()
    env.bundle.symlink_to(env.state)
    assert publish_bundle(env.bundle, env.repo, env.candidate).code == "BUNDLE_NOT_REGULAR"


def test_verified_bytes_over_the_size_cap_are_refused(env: Env, capsys: pytest.CaptureFixture[str]):
    """The ``verified=`` path takes the same size cap as reading the file (review of #1674)."""
    from cairnwork_attest.bundle import MAX_BUNDLE_BYTES

    assert _run(capsys, env.argv("--no-check-run"))[0] == 0
    big = env.bundle.read_bytes() + b" " * MAX_BUNDLE_BYTES
    result = publish_bundle(env.bundle, env.repo, env.candidate, verified=big)
    assert result.code == "BUNDLE_TOO_LARGE" and not result.ok
