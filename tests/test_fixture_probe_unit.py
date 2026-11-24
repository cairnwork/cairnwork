"""#986 step 4a: Probe.read and start/finish on their own (no sandbox needed)."""

import hashlib
import json
from types import SimpleNamespace

from cairnwork import fixture_probe

STAGES = ("itemcollected", "modifyitems_before", "modifyitems_after", "finish")


def _record(module, plugin_file, names=("widget", "tmp_path")):
    stages = {s: {"names": sorted(names), "count": 1} for s in STAGES}
    return {
        "recorder_version": 2,
        "nonce": "n0nce",
        "complete": True,
        "settings_digest": "dig",
        "base_sha": "base",
        "sys_path": [],
        "sys_path_finish": [],
        "stages": stages,
        "names": sorted(names),
        "plugin_modules": [{"name": module, "file": str(plugin_file)}],
    }


def _unit_probe(tmp_path, record, module, plugin_file):
    record_dir = tmp_path / "rec"
    record_dir.mkdir(exist_ok=True)
    plugin_file.parent.mkdir(exist_ok=True)
    plugin_file.touch()
    (record_dir / "record.json").write_text(json.dumps(record))
    return fixture_probe.Probe(
        ("-p", module), module, record_dir, plugin_file, "dig", "base", "n0nce"
    )


UNIT_MODULE = "_cairnwork_fixture_record_abc"


def test_unit_good_record_returns_names(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    probe = _unit_probe(tmp_path, _record(UNIT_MODULE, pf), UNIT_MODULE, pf)
    assert probe.read(0, False) == frozenset({"widget", "tmp_path"})
    assert probe.read(5, False) == frozenset({"widget", "tmp_path"})


def test_unit_missing_plugin_entry_is_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    record = _record(UNIT_MODULE, pf)
    record["plugin_modules"] = []
    assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None


def test_unit_other_module_name_is_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    record = _record("_some_other_plugin", pf)
    assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None


def test_unit_other_file_is_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    record = _record(UNIT_MODULE, tmp_path / "elsewhere" / f"{UNIT_MODULE}.py")
    assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None


def test_unit_symlink_to_other_file_is_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    other = tmp_path / "other.py"
    other.touch()
    link = tmp_path / "guard" / "link.py"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(other)
    record = _record(UNIT_MODULE, link)
    assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None


def test_unit_bad_exit_codes_are_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    probe = _unit_probe(tmp_path, _record(UNIT_MODULE, pf), UNIT_MODULE, pf)
    for code in (1, 2, None, True):
        assert probe.read(code, False) is None


def test_unit_timed_out_is_none(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    probe = _unit_probe(tmp_path, _record(UNIT_MODULE, pf), UNIT_MODULE, pf)
    assert probe.read(0, True) is None


def test_an_account_run_records_nothing(tmp_path):
    """Owner runs only: with an account, start adds nothing and finish reads nothing, even when a
    complete, matching record is sitting in the private dir."""
    spec = SimpleNamespace(record_fixtures=True)
    owner = SimpleNamespace(account=None, base_commit=None)
    account = SimpleNamespace(account=object(), base_commit=None)
    ini, commit = b"ini", "c" * 40
    private = tmp_path / "private"
    (private / "guard").mkdir(parents=True)
    (private / "run").mkdir()
    env: dict = {}
    started = fixture_probe.start(spec, owner, private, env, ini, commit)
    record = _record(started.module, started.plugin_file)
    record["settings_digest"] = hashlib.sha256(ini).hexdigest()
    record["base_sha"] = commit
    record["nonce"] = started.nonce
    assert started.record_dir is not None
    (started.record_dir / "record.json").write_text(json.dumps(record))
    run = SimpleNamespace(returncode=0, timed_out=False)
    assert fixture_probe.finish(spec, owner, commit, private, ini, run) == frozenset(
        {"widget", "tmp_path"}
    )  # control: the same private dir reads fine for an owner run
    assert fixture_probe.finish(spec, account, commit, private, ini, run) is None
    other, other_env = tmp_path / "other", {}
    (other / "guard").mkdir(parents=True)
    (other / "run").mkdir()
    assert (
        fixture_probe.start(spec, account, other, other_env, ini, commit) == fixture_probe.Probe()
    )
    assert other_env == {} and not list((other / "guard").iterdir())


def test_unit_record_without_this_runs_nonce_is_refused(tmp_path):
    """A forged record that copies every visible field (module, file, digest) but not the nonce."""
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    for nonce in ("other", "", None):
        record = _record(UNIT_MODULE, pf)
        record["nonce"] = nonce
        assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None
    record = _record(UNIT_MODULE, pf)
    del record["nonce"]
    assert _unit_probe(tmp_path, record, UNIT_MODULE, pf).read(0, False) is None
    probe = _unit_probe(tmp_path, _record(UNIT_MODULE, pf), UNIT_MODULE, pf)
    probe.nonce = ""  # no nonce on the parent side: nothing can match
    assert probe.read(0, False) is None


def test_empty_nonce_on_both_sides_is_refused(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    record = _record(UNIT_MODULE, pf)
    record["nonce"] = ""  # what the recorder writes when it got no pipe
    unit = _unit_probe(tmp_path, record, UNIT_MODULE, pf)
    unit.nonce = ""
    assert unit.read(0, False) is None
