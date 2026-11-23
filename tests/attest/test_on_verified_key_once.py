"""on-verified takes the public key once per run and every step uses that key (#1683)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_on_verified import Env, FakeHttp, _codes, _run
from test_on_verified_single_read import _note

from cairnwork_attest import checkrun, on_verified
from cairnwork_attest.bundle import parse_bundle
from cairnwork_attest.dsse import _sign_bytes
from cairnwork_attest.keys import load_private_key, public_key_pem

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


def _forge(bundle: Path, key_file: Path) -> None:
    """Swap in a bundle signed by an attacker key, and make the key file that attacker's key."""
    attacker = Ed25519PrivateKey.generate()
    payload = base64.b64decode(parse_bundle(bundle.read_bytes())["payload"])
    forged = _sign_bytes(payload, attacker)
    bundle.write_text(json.dumps(forged, sort_keys=True, separators=(",", ":")))
    key_file.write_text(public_key_pem(attacker.public_key()))


def test_a_bundle_and_key_file_swapped_together_are_not_trusted(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = on_verified._publish_step

    def swap_then_publish(args: Any, bundle: Path, key: Any) -> Any:
        _forge(bundle, key.path)
        return real(args, bundle, key)

    monkeypatch.setattr(on_verified, "_publish_step", swap_then_publish)
    rc, _, obj = _run(capsys, env.argv())
    assert rc == 2
    assert _codes(obj)[:2] == [("statement", "OK"), ("publish", "SIGNATURE_INVALID")]
    assert _note(env.bare, env.candidate) is None and http.calls == []


def test_a_key_file_swapped_before_publish_is_ignored(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = on_verified._publish_step

    def swap_then_publish(args: Any, bundle: Path, key: Any) -> Any:
        key.path.write_text(public_key_pem(Ed25519PrivateKey.generate().public_key()))
        return real(args, bundle, key)

    monkeypatch.setattr(on_verified, "_publish_step", swap_then_publish)
    rc, _, obj = _run(capsys, env.argv())
    assert rc == 0 and _codes(obj)[:2] == [("statement", "OK"), ("publish", "PUBLISHED")]


def test_a_key_file_swapped_before_check_run_is_ignored(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = on_verified._check_run_step

    def swap_then_check(args: Any, bundle: Path, key: Any) -> Any:
        key.path.write_text(public_key_pem(Ed25519PrivateKey.generate().public_key()))
        return real(args, bundle, key)

    monkeypatch.setattr(on_verified, "_check_run_step", swap_then_check)
    rc, _, obj = _run(capsys, env.argv())
    assert rc == 0 and _codes(obj)[-1] == ("check_run", "CREATED")
    summary = str(http.calls)
    assert "VERIFIED" in summary and "SIGNATURE_INVALID" not in summary


def test_reusing_an_existing_bundle_checks_it_with_the_loaded_key(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(capsys, env.argv("--no-check-run"))[0] == 0
    seen: list[Any] = []
    real = on_verified._verify_loaded

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(k.get("public_key"))
        return real(*a, **k)

    monkeypatch.setattr(on_verified, "_verify_loaded", spy)
    rc, _, obj = _run(capsys, env.argv("--no-check-run"))
    assert rc == 0 and obj["steps"][0]["detail"] == "reused existing bundle"
    mine = load_private_key(env.state).public_key().public_bytes_raw()
    assert len(seen) >= 2 and all(k is not None and k.public_bytes_raw() == mine for k in seen)


def test_check_run_given_a_loaded_key_uses_exactly_that_key(
    env: Env, http: FakeHttp, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key object is the only key: a wrong one refuses, the right one verifies (no file read)."""
    assert _run(capsys, env.argv("--no-check-run"))[0] == 0
    right = load_private_key(env.state).public_key()
    seen: list[Any] = []
    real = checkrun._verify_loaded
    monkeypatch.setattr(
        checkrun, "_verify_loaded", lambda *a, **k: seen.append(k["public_key"]) or real(*a, **k)
    )
    for key in (Ed25519PrivateKey.generate().public_key(), right):
        http.calls.clear()
        checkrun.post_check_run(
            env.bundle,
            checkrun.SLUG_FOR_TESTS if False else "o/r",
            public_key=key,
            repo=env.repo,
            token=TOKEN,
            http=http,
        )
        summaries.append(str(http.calls)) if (
            summaries := locals().get("summaries")
        ) is not None else None
    assert seen[0] is not right and seen[1] is right
