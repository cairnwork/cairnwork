"""check-run says what its --repo verify pins and marks bundle values when unverified (#747)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cairnwork_attest.checkrun import post_check_run
from tests.attest import test_check_run_assurance as _assurance

FakeHttp = _assurance.FakeHttp
v02_world = _assurance.v02_world  # the fixture, registered here by name

PINS = (
    "- Checked against: the candidate named in the bundle only, "
    "base, policy and Core identity not pinned"
)
NOTE = "The values above are read from the bundle and were not verified by this step."


def _summary(bundle: Path, world: dict, *, key: bool = True, repo: bool = True) -> list[str]:
    http = FakeHttp()
    res = post_check_run(
        bundle,
        "octo/repo",
        public_key=world["pub"] if key else None,
        repo=world["full"] if repo else None,
        token="ghs_TOKEN",
        http=http,
    )
    assert res.ok, res
    return json.loads(http.calls[0][3])["output"]["summary"].splitlines()


def test_a_full_verify_says_only_the_bundles_candidate_was_pinned(v02_world: dict) -> None:
    lines = _summary(v02_world["bundle"], v02_world)
    assert any(x.startswith("- Offline verification: VERIFIED") for x in lines)
    assert PINS in lines
    assert NOTE not in lines


def test_signature_only_has_no_pins_line_and_no_note(v02_world: dict) -> None:
    lines = _summary(v02_world["bundle"], v02_world, repo=False)
    assert "- Offline verification: VERIFIED, signature only" in lines
    assert PINS not in lines and NOTE not in lines


def test_a_failed_verify_marks_the_values_as_unverified(v02_world: dict, tmp_path: Path) -> None:
    env = json.loads(v02_world["bundle"].read_text())
    stmt = json.loads(base64.b64decode(env["payload"]))
    stmt["predicate"]["binding"]["test_paths"] = ["tests/test_changed.py"]
    env["payload"] = base64.b64encode(json.dumps(stmt).encode()).decode()
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(env))
    lines = _summary(changed, v02_world)
    assert "- Offline verification: SIGNATURE_INVALID" in lines
    assert NOTE in lines and PINS not in lines


def test_an_unchecked_signature_marks_the_values_as_unverified(v02_world: dict) -> None:
    lines = _summary(v02_world["bundle"], v02_world, key=False, repo=False)
    assert NOTE in lines and PINS not in lines
