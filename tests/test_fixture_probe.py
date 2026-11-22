"""#986 step 4a: the base fixture names come from a collect-only run whose recorder is loaded from
the private guard directory. Forged, shadowed or failed runs give None, never fewer names.

End to end (sandbox): the real verifier runner with ``record_fixtures=True``.
"""

import sys
import time

import pytest
from conftest import commit_change

import cairnwork_sandbox
from cairnwork import fixture_probe
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext

GENUINE = frozenset({"widget", "tmp_path"})  # pytest adds its own fixtures: a subset
CONFTEST = "import pytest\n\n@pytest.fixture\ndef widget():\n    return 1\n"
TEST = "def test_a(widget, tmp_path):\n    assert widget == 1\n"


def probe(repo, tmp_path, files, *, record=True, timeout=600, collect_only=True):
    head = commit_change(repo, files)
    spec = VerifierSpec(
        "probe", ("tests/test_a.py",), collect_only=collect_only, record_fixtures=record
    )
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    (run,) = run_verifiers(ctx, head, (spec,), "r1", timeout=timeout)
    return run


def base_files(**extra):
    files = {"conftest.py": CONFTEST, "tests/test_a.py": TEST}
    files.update(extra)
    return files


@pytest.mark.sandbox
def test_good_project_names_include_fixture_and_tmp_path(repo, tmp_path):
    run = probe(repo, tmp_path, base_files())
    assert run.returncode == 0
    assert run.fixture_names is not None
    assert GENUINE <= run.fixture_names


@pytest.mark.sandbox
def test_checkout_fixture_record_and_package_do_not_win(repo, tmp_path):
    forged = "def pytest_collection_finish(session):\n    raise SystemExit(0)\n"
    files = base_files(
        **{
            "fixture_record.py": forged,
            "cairnwork/__init__.py": "",
            "cairnwork/fixture_record.py": forged,
            "tests/_cairnwork_fixture_record.py": forged,
        }
    )
    run = probe(repo, tmp_path, files)
    assert run.fixture_names is not None
    assert run.fixture_names is not None
    assert GENUINE <= run.fixture_names


FORGED_FIXED_NAME = """
import json, os

def pytest_collection_finish(session):
    stages = {s: {"names": ["forged"], "count": 1} for s in
              ("itemcollected", "modifyitems_before", "modifyitems_after", "finish")}
    guard = os.environ["PYTHONPATH"].split(os.pathsep)[0]
    record = {
        "recorder_version": 2,
        "complete": True,
        "settings_digest": os.environ["CAIRNWORK_SETTINGS_DIGEST"],
        "base_sha": os.environ["CAIRNWORK_BASE_SHA"],
        "sys_path": [],
        "sys_path_finish": [],
        "stages": stages,
        "names": ["forged"],
        "plugin_modules": [
            {"name": "_cairnwork_fixture_record",
             "file": os.path.join(guard, "_cairnwork_fixture_record.py")},
        ],
    }
    with open(os.path.join(os.environ["CAIRNWORK_FIXTURE_RECORD_DIR"], "record.json"), "w") as f:
        json.dump(record, f)
"""


@pytest.mark.sandbox
def test_checkout_root_fixed_name_shadow_cannot_forge(repo, tmp_path):
    # the old fixed module name would make -p import this root file first (sys.path[0] is the
    # checkout); its record claims the guard's path. Names must still be the real ones.
    run = probe(repo, tmp_path, base_files(**{"_cairnwork_fixture_record.py": FORGED_FIXED_NAME}))
    assert run.fixture_names is not None
    assert run.fixture_names is not None
    assert GENUINE <= run.fixture_names
    assert run.fixture_names is not None
    assert "forged" not in run.fixture_names


def test_each_run_gets_its_own_module_name(tmp_path):
    from types import SimpleNamespace

    spec = SimpleNamespace(record_fixtures=True)
    ctx = SimpleNamespace(account=None, base_commit=None)
    modules, guards = [], []
    for index in range(2):
        private = tmp_path / f"p{index}"
        (private / "guard").mkdir(parents=True)
        (private / "run").mkdir()
        p = fixture_probe.start(spec, ctx, private, {}, b"ini", "c" * 40)
        assert p.argv[0] == "-p" and p.plugin_file is not None and p.plugin_file.exists()
        modules.append(p.argv[1])
        guards.append(p.plugin_file.name)
        p.close()
    assert modules[0] != modules[1] and guards[0] != guards[1]
    assert all(m.startswith("_cairnwork_fixture_record_") for m in modules)


@pytest.mark.sandbox
def test_conftest_redirecting_record_dir_gives_no_forgery(repo, tmp_path):
    conftest = (
        CONFTEST
        + "import os\n"
        + "os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'] = os.path.join(\n"
        + "    os.path.dirname(os.path.abspath(__file__)), 'fake')\n"
        + "os.makedirs(os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'], exist_ok=True)\n"
        + "open(os.path.join(os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'], 'record.json'),"
        + " 'w').write('{\"names\": [\"forged\"]}')\n"
    )
    run = probe(repo, tmp_path, base_files(**{"conftest.py": conftest}))
    assert run.fixture_names is None or GENUINE <= run.fixture_names
    assert run.fixture_names is None or "forged" not in run.fixture_names


@pytest.mark.sandbox
def test_conftest_writing_record_into_checkout_gives_no_forgery(repo, tmp_path):
    conftest = (
        CONFTEST
        + "import os\n"
        + "d = os.environ['CAIRNWORK_FIXTURE_RECORD_DIR']\n"
        + "target = os.path.join(d, 'record.json')\n"
        + "os.path.lexists(target) and os.remove(target)\n"
        + "os.symlink(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fake.json'),"
        + " target)\n"
    )
    files = base_files(**{"conftest.py": conftest, "fake.json": '{"names": ["forged"]}'})
    run = probe(repo, tmp_path, files)
    assert run.fixture_names is None or GENUINE <= run.fixture_names
    assert run.fixture_names is None or "forged" not in run.fixture_names


@pytest.mark.sandbox
def test_collection_error_gives_none(repo, tmp_path):
    files = base_files(**{"tests/test_a.py": "import no_such_module_986\n"})
    run = probe(repo, tmp_path, files)
    assert run.returncode == 2
    assert run.fixture_names is None


@pytest.mark.sandbox
def test_conftest_system_exit_gives_none(repo, tmp_path):
    files = base_files(**{"conftest.py": "raise SystemExit(0)\n"})
    run = probe(repo, tmp_path, files)
    assert run.fixture_names is None


@pytest.mark.sandbox
def test_timeout_gives_none(repo, tmp_path):
    files = base_files(**{"conftest.py": CONFTEST + "import time\ntime.sleep(60)\n"})
    start = time.monotonic()
    run = probe(repo, tmp_path, files, timeout=3)
    assert run.timed_out
    assert run.fixture_names is None
    assert time.monotonic() - start < 120


@pytest.mark.sandbox
def test_record_fixtures_false_is_unchanged(repo, tmp_path):
    run = probe(repo, tmp_path, base_files(), record=False)
    assert run.fixture_names is None
    assert run.returncode == 0


def test_no_sandbox_backend_gives_none(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cairnwork_sandbox, "select", lambda: None)
    run = probe(repo, tmp_path, base_files())
    assert run.returncode is None
    assert run.fixture_names is None


@pytest.mark.sandbox
def test_missing_recorder_source_gives_none(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(fixture_probe, "RECORDER", tmp_path / "no_such_recorder.py")
    run = probe(repo, tmp_path, base_files())
    assert run.fixture_names is None


@pytest.mark.sandbox
def test_unimportable_recorder_gives_none(repo, tmp_path, monkeypatch):
    bad = tmp_path / "bad_recorder.py"
    bad.write_text("this is not python (\n")
    monkeypatch.setattr(fixture_probe, "RECORDER", bad)
    run = probe(repo, tmp_path, base_files())
    assert run.fixture_names is None


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
    import json

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
    import hashlib
    import json
    from types import SimpleNamespace

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


FORGE_AT_EXIT = """
import json, os, sys

def pytest_unconfigure(config):
    mod = sys.modules[next(m for m in sys.modules if m.startswith("_cairnwork_fixture_record_"))]
    stages = {s: {"names": ["forged"], "count": 1}
              for s in ("itemcollected", "modifyitems_before", "modifyitems_after", "finish")}
    record = {
        "recorder_version": 2, "complete": True,
        "settings_digest": os.environ["CAIRNWORK_SETTINGS_DIGEST"],
        "base_sha": os.environ["CAIRNWORK_BASE_SHA"],
        "sys_path": [], "sys_path_finish": [], "stages": stages, "names": ["forged"],
        "plugin_modules": [{"name": mod.__name__, "file": mod.__file__}],
    }
    path = os.path.join(os.environ["CAIRNWORK_FIXTURE_RECORD_DIR"], "record.json")
    with open(path, "w") as handle:
        json.dump(record, handle)
"""


@pytest.mark.sandbox
def test_run_code_forging_every_visible_field_still_gives_no_names(repo, tmp_path):
    # the run sees the module name, the guard file and the digests, but not the nonce
    run = probe(repo, tmp_path, base_files(**{"conftest.py": FORGE_AT_EXIT}))
    assert run.fixture_names is None


def test_the_parents_nonce_pipe_is_closed_after_a_run(repo, tmp_path, monkeypatch):
    seen = []
    real = fixture_probe.start

    def spy(*args):
        result = real(*args)
        seen.append(result)
        return result

    monkeypatch.setattr(fixture_probe, "start", spy)
    probe(repo, tmp_path, base_files())
    assert seen and seen[0].read_fd == -1
    seen[0].close()  # idempotent


def test_empty_nonce_on_both_sides_is_refused(tmp_path):
    pf = tmp_path / "guard" / f"{UNIT_MODULE}.py"
    record = _record(UNIT_MODULE, pf)
    record["nonce"] = ""  # what the recorder writes when it got no pipe
    unit = _unit_probe(tmp_path, record, UNIT_MODULE, pf)
    unit.nonce = ""
    assert unit.read(0, False) is None


FORGE_WITH_NONCE = FORGE_AT_EXIT.replace(
    '"plugin_modules"', '"nonce": mod._META["nonce"], "plugin_modules"'
)


@pytest.mark.sandbox
def test_known_limit_same_process_code_can_forge_after_the_last_write(repo, tmp_path):
    # Out of scope by design (see fixture_probe's docstring): code inside the pytest process can
    # read the recorder module's state. If this ever starts returning None the limit was sealed;
    # then flip the assertion and drop the advisory note.
    run = probe(repo, tmp_path, base_files(**{"conftest.py": FORGE_WITH_NONCE}))
    assert run.fixture_names == frozenset({"forged"})
