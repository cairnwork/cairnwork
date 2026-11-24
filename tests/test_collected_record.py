"""#1769 step 1: the recorder's plugin and sys.path fields reach the caller, validated.

Same trust rules as the names (collected_names): any doubt gives None, never a partial record.
"""

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_collected_names import DIGEST, SHA, good_record, read, write_record

from cairnwork import fixture_probe
from cairnwork.collected_names import CollectedRecord, collected_record


def got(run_dir: Path, *, code: object = 0, digest: str = DIGEST, sha: str = SHA) -> Any:
    return collected_record(run_dir, exit_code=code, settings_digest=digest, base_sha=sha)


def test_good_record_carries_plugins_and_sys_paths(tmp_path: Path) -> None:
    record = got(write_record(tmp_path, good_record()))
    assert record == CollectedRecord(
        names=frozenset({"a", "tmp_path"}),
        plugins=(("p", None), ("q", "/q.py")),
        sys_path=("/x",),
        sys_path_finish=("/x", "/y"),
    )


def test_names_reader_and_record_reader_agree(tmp_path: Path) -> None:
    run_dir = write_record(tmp_path, good_record())
    record = got(run_dir)
    assert record is not None and read(run_dir) == record.names


@pytest.mark.parametrize("field", ["plugin_modules", "sys_path", "sys_path_finish"])
def test_a_malformed_extra_field_voids_the_whole_record(tmp_path: Path, field: str) -> None:
    rec = good_record()
    rec[field] = "not-a-list"
    run_dir = write_record(tmp_path, rec)
    assert got(run_dir) is None and read(run_dir) is None


@pytest.mark.parametrize("code", [1, 2, True, None, "0"])
def test_a_bad_exit_status_gives_no_record(tmp_path: Path, code: object) -> None:
    assert got(write_record(tmp_path, good_record()), code=code) is None


def test_incomplete_or_foreign_record_gives_none(tmp_path: Path) -> None:
    rec = good_record()
    rec["complete"] = False
    assert got(write_record(tmp_path, rec)) is None
    rec = copy.deepcopy(good_record())
    assert got(write_record(tmp_path, rec), digest="other") is None
    assert got(write_record(tmp_path, rec), sha="other") is None


def test_probe_read_record_applies_the_nonce_check(tmp_path: Path) -> None:
    module = "_cairnwork_fixture_record_abc"
    plugin_file = tmp_path / "guard" / f"{module}.py"
    plugin_file.parent.mkdir()
    plugin_file.touch()
    rec = good_record()
    rec["nonce"] = "n0nce"
    rec["plugin_modules"] = [{"name": module, "file": str(plugin_file)}]
    run_dir = write_record(tmp_path, rec)
    probe = fixture_probe.Probe(("-p", module), module, run_dir, plugin_file, DIGEST, SHA, "n0nce")
    record = probe.read_record(0, False)
    assert record is not None and record.plugins == ((module, str(plugin_file)),)
    assert probe.read(0, False) == record.names
    bad = fixture_probe.Probe(("-p", module), module, run_dir, plugin_file, DIGEST, SHA, "forged")
    assert bad.read_record(0, False) is None and bad.read(0, False) is None
    assert probe.read_record(0, True) is None  # timed out


def test_finish_record_is_none_without_a_recorder(tmp_path: Path) -> None:
    spec = SimpleNamespace(record_fixtures=False)
    ctx = SimpleNamespace(account=None, base_commit="b")
    assert fixture_probe.finish_record(spec, ctx, "c", tmp_path, b"", None) is None


@pytest.mark.sandbox
def test_a_real_run_fills_collect_record_in_step_with_the_names(repo, tmp_path):
    from test_fixture_probe import base_files, probe

    run = probe(repo, tmp_path, base_files())
    assert run.collect_record is not None and run.fixture_names == run.collect_record.names
    assert any(
        name == "conftest" or name.endswith(".conftest") for name, _ in run.collect_record.plugins
    )
    assert run.collect_record.sys_path_finish
    plain = probe(repo, tmp_path, base_files(**{"tests/test_b.py": ""}), record=False)
    assert plain.collect_record is None and plain.fixture_names is None
