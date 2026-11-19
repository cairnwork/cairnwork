"""#458 item 3: ``verify`` reports a missing or malformed ``--public-key`` as a key error with a
usage-class exit (2), as ``check-run`` does, so an operator mistake never reads as a forged
bundle (``SIGNATURE_INVALID``, exit 1). A wrong but well-formed key is still ``SIGNATURE_INVALID``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnwork_attest.cli import main
from cairnwork_attest.keys import public_key_pem
from cairnwork_attest.verify import verify_bundle
from tests.attest.test_verify import setup_verified_bundle_fixture


def _bad_keys(tmp_path: Path) -> dict[str, tuple[Path, str]]:
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a pem key\n")
    return {
        "missing": (tmp_path / "absent.pem", "KEY_UNREADABLE"),
        "malformed": (garbage, "KEY_MALFORMED"),
    }


@pytest.mark.parametrize("case", ["missing", "malformed"])
def test_verify_bundle_names_the_key_problem(tmp_path, repo, case):
    bundle, _pub, _base, candidate = setup_verified_bundle_fixture(tmp_path / "state", repo)
    key, code = _bad_keys(tmp_path)[case]
    res = verify_bundle(bundle, public_key_pem=key, repo=repo, expected_candidate=candidate)
    assert (res.ok, res.code) == (False, code)


@pytest.mark.parametrize("case", ["missing", "malformed"])
@pytest.mark.parametrize("as_json", [False, True])
def test_verify_cli_exits_2_for_a_key_problem(tmp_path, repo, capsys, case, as_json):
    bundle, _pub, _base, candidate = setup_verified_bundle_fixture(tmp_path / "state", repo)
    key, code = _bad_keys(tmp_path)[case]
    argv = ["verify", str(bundle), "--public-key", str(key), "--repo", str(repo)]
    rc = main([*argv, "--candidate", candidate, *(["--json"] if as_json else [])])
    out = capsys.readouterr().out
    assert rc == 2, out
    if as_json:
        line: Any = json.loads(out)
        assert (line["ok"], line["code"]) == (False, code)
    else:
        assert out.startswith(f"{code}: ")


def test_a_wrong_wellformed_key_is_still_a_signature_failure(tmp_path, repo, capsys):
    bundle, _pub, _base, candidate = setup_verified_bundle_fixture(tmp_path / "state", repo)
    other = tmp_path / "other.pem"
    other.write_text(public_key_pem(Ed25519PrivateKey.generate().public_key()))
    argv = ["verify", str(bundle), "--public-key", str(other), "--repo", str(repo)]
    rc = main([*argv, "--candidate", candidate])
    out = capsys.readouterr().out
    assert rc == 1
    assert out.startswith("SIGNATURE_INVALID: ")
