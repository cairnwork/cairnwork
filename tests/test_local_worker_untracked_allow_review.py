"""The start allow-list (#261), review of PR #263: numstat from a fresh tree, nothing dropped
between status and digest, bad digest records, globs, the old wrapper, and the corpus field."""

import hashlib
import json
from dataclasses import replace

import local_worker_fakes
import pytest
from local_worker_allow_fakes import AllowWorld, canary_report
from local_worker_fakes import META, TASK, cli_harness, make_loop, reply

from experimental.local_worker import corpus_io, episode
from experimental.local_worker.agent import RefusedStart, initial_messages
from experimental.local_worker.episode_schema import SchemaError, validate_record
from experimental.local_worker.gitstate import parse_digest
from experimental.local_worker.untracked_allow import build

ALLOW = build(["data", "gen/fixture.json"], None)
READ = reply("read_file", path="src/app.py")
LIST = reply("list_dir", path="src")
EDIT = reply("edit_file", path="src/app.py", target="x = 1", replacement="x = 2")


def _record(tmp_path, replies, world):
    loop, world, _, _ = make_loop(tmp_path, replies, world=world)
    loop.settings = replace(loop.settings, untracked_allow=ALLOW)
    out = loop.run(initial_messages(TASK))
    rec = episode.build_record(META, out)
    validate_record(rec)
    return rec, out, loop


class Survivor(AllowWorld):
    """A process the sweeps missed rewrites ``change`` just before the cleanup's numstat (the
    second numstat: the first is the base's), after the last turn's observation."""

    def __init__(self, change: dict[str, str], **kw) -> None:
        super().__init__(**kw)
        self.change, self.numstats = change, 0

    def handle(self, action, args, stdin):
        if action == "numstat":
            self.numstats += 1
            if self.numstats == 2:
                self.files.update(self.change)
        return super().handle(action, args, stdin)


# -- M1: numstat counts only from the tree observed right after it ------------------------------
def test_a_change_after_the_last_observation_nulls_numstat(tmp_path):
    rec, out, _ = _record(tmp_path, [READ, LIST], Survivor({"data/x.dat": "one\n"}))
    assert out.patch is None and out.cleanup == "failed"  # never (-1, 1, 0) with cleanup ok
    assert rec["verification"]["patch_lines_added"] is None
    assert rec["allowed_base_changed_turns"] == 1  # the last tool turn got the fresh tree
    assert rec["first_edit_cut"]["cut_status"] == "CUT"
    assert rec["first_edit_cut"]["cut_turn_index"] == 1


def test_a_same_size_change_after_the_last_observation_nulls_numstat(tmp_path):
    """The count alone looks fine here (as many lines as at base): only the fresh digest
    shows that an allowed base file changed."""
    rec, out, _ = _record(tmp_path, [READ, LIST], Survivor({"data/x.dat": "ONE\nTWO\n"}))
    assert out.patch is None and out.cleanup == "failed"
    assert rec["allowed_base_changed_turns"] == 1


def test_an_out_of_list_file_after_the_last_observation_sets_the_cut(tmp_path):
    rec, out, _ = _record(tmp_path, [READ, LIST], Survivor({"stray.py": "s\n"}))
    assert rec["first_edit_cut"]["cut_turn_index"] == 1
    assert rec["allowed_base_changed_turns"] == 0  # no allowed base file changed
    assert out.patch == (1, 0, 0) and out.cleanup == "ok"


def test_the_final_digest_runs_even_after_an_observed_last_turn(tmp_path):
    world = AllowWorld()
    _record(tmp_path, [READ, LIST], world)
    calls = [c[0] for c in world.calls]
    assert calls[-3:] == ["numstat", "status", "digest"]


def test_a_negative_count_is_no_count(tmp_path):
    loop, _, _, _ = make_loop(tmp_path, [], world=AllowWorld())
    loop.settings = replace(loop.settings, untracked_allow=ALLOW)
    base = loop._check_base(loop.observe(), None)
    assert loop._own_patch(base, (100, 0, 0)) is not None
    assert loop._own_patch(base, (0, 0, 0)) is None  # less than the base's own lines


# -- O1: only numstat and cleanup change when an allowed base file changed ----------------------
def test_an_allowed_base_file_change_touches_only_numstat_and_cleanup(tmp_path):
    write = reply("write_file", path="data/x.dat", content="changed\n")
    changed, _, _ = _record(tmp_path / "a", [READ, write, EDIT], AllowWorld())
    other = reply("write_file", path="src/new.py", content="n\n")
    plain, _, _ = _record(tmp_path / "b", [READ, other, EDIT], AllowWorld())
    for rec in (changed, plain):
        assert rec["verification"]["exit_reason"] == "COMPLETED"
        assert rec["termination"]["abort_cause"] is None
        assert rec["termination"]["limit_hit"] is None and not rec["termination"]["interrupted"]
        assert rec["spec_feedback"]["needs_spec"] is False
        assert [t["action_type"] for t in rec["turns"]][-1] == "finish"
    assert changed["termination"]["cleanup"] == "failed"
    assert changed["verification"]["patch_lines_added"] is None
    assert changed["allowed_base_changed_turns"] == 2  # after the write and after the edit
    assert plain["termination"]["cleanup"] == "ok"
    assert plain["verification"]["patch_lines_added"] == 2
    assert plain["allowed_base_changed_turns"] == 0


# -- M2: a digest record status did not show is never dropped -----------------------------------
class Between(AllowWorld):
    """A file appears between a turn's status and digest queries (the third digest call: base,
    turn 0, then turn 1)."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.digests = 0

    def handle(self, action, args, stdin):
        if action == "digest":
            self.digests += 1
            if self.digests == 3:
                self.files["stray.py"] = "s\n"
        return super().handle(action, args, stdin)


def test_a_file_created_between_status_and_digest_changes_the_tree(tmp_path):
    rec, out, _ = _record(tmp_path, [READ, LIST, READ], Between())
    cut = rec["first_edit_cut"]
    assert (cut["cut_status"], cut["cut_turn_index"]) == ("CUT", 1)
    tree = out.turns[1].tree
    assert tree is not None
    assert any(e.startswith("h ") and e.endswith(" stray.py") for e in tree.entries)


# -- S1: a digest record that does not parse is no observation ----------------------------------
def test_one_bad_digest_record_makes_the_digest_unparsed():
    good = b":000000 100644 " + b"0" * 40 + b" " + b"5" * 40 + b" A\0data/x.dat\0"
    assert parse_digest(good) is not None
    assert parse_digest(good + b":000000 100644 zz A\0data/y.dat\0") is None
    assert parse_digest(b"junk\0data/y.dat\0" + good) is None
    assert parse_digest(good[:-1]) is None  # not NUL-terminated


def test_a_bad_digest_record_refuses_the_start(tmp_path):
    class Bad(AllowWorld):
        def digest(self) -> bytes:
            return super().digest() + b":000000 100644 nothex A\0data/y.dat\0"

    loop, _, _, _ = make_loop(tmp_path, [], world=Bad())
    loop.settings = replace(loop.settings, untracked_allow=ALLOW)
    assert not loop.observe().observed
    with pytest.raises(RefusedStart, match="not a clean git tree"):
        loop.run(initial_messages(TASK))


VERIFY = corpus_io.verify_canary  # the real one, before any test patches it


# -- m1: globs -----------------------------------------------------------------------------------
@pytest.mark.parametrize("glob", ["[!x]*", "[a-z]*.egg-info", "*a*", "*.py", "*.md", "src/*"])
def test_a_broad_glob_or_a_character_class_is_refused(glob):
    with pytest.raises(ValueError):
        build(None, [glob])


@pytest.mark.parametrize("glob", ["*.egg-info", "src/*.egg-info", "gen/*.json", "pkg/_v*.py"])
def test_a_harness_glob_is_accepted(glob):
    assert build(None, [glob]).globs == (glob,)


# -- m2: the old wrapper with a list refuses, through the CLI -----------------------------------
def test_a_list_with_the_old_wrapper_refuses_before_the_manifest(tmp_path, monkeypatch):
    old = b"#!/bin/sh\n# the wrapper before the digest query\n"
    path, wrapper, _ = canary_report(tmp_path, old)
    monkeypatch.setattr(corpus_io, "PREVIOUS_WRAPPER_SHA256", hashlib.sha256(old).hexdigest())
    monkeypatch.setattr(local_worker_fakes, "World", AllowWorld)
    run_cli, out = cli_harness(tmp_path, monkeypatch)

    def canary(report, out_dir, **kw):  # the real check, on the old wrapper's canary report
        return VERIFY(path, path.parent, wrapper=wrapper, host="mini", **kw)

    monkeypatch.setattr(corpus_io, "verify_canary", canary)
    assert run_cli([READ], "run", "--allow-untracked", "data") == 2
    assert not (out / "manifest.json").exists()  # refused at the canary binding
    assert run_cli([READ], "run") == 2  # no list: the binding passes (the start refuses later)
    assert (out / "manifest.json").exists()


# -- O2: the corpus field -------------------------------------------------------------------------
def test_the_field_is_absent_without_a_list(tmp_path):
    loop, _, _, _ = make_loop(tmp_path, [READ])
    rec = episode.build_record(META, loop.run(initial_messages(TASK)))
    assert "allowed_base_changed_turns" not in rec
    validate_record(rec)


@pytest.mark.parametrize("bad", [True, -1, 99, 1.0, None])
def test_the_schema_checks_the_field(tmp_path, bad):
    rec, _, _ = _record(tmp_path, [READ, EDIT], AllowWorld())
    rec["allowed_base_changed_turns"] = bad
    with pytest.raises(SchemaError):
        validate_record(rec)


def test_a_changed_turn_needs_a_cut(tmp_path):
    rec, _, _ = _record(tmp_path, [READ, LIST], AllowWorld())
    assert rec["first_edit_cut"]["cut_status"] == "NO_CUT"
    validate_record(json.loads(json.dumps(rec)))
    rec["allowed_base_changed_turns"] = 1
    with pytest.raises(SchemaError, match="needs a first_edit_cut"):
        validate_record(rec)


def test_no_negative_counts_with_a_list(tmp_path):
    rec, _, _ = _record(tmp_path, [READ, EDIT], AllowWorld())
    rec["verification"]["patch_lines_added"] = -1
    with pytest.raises(SchemaError, match="non-negative"):
        validate_record(rec)


# -- #264: a failed sweep with a list still fails the cleanup closed ---------------------------
class SweepFails(AllowWorld):
    """Every sweep exits non-zero; with read-only turns the cleanup's is the only one."""

    def handle(self, action, args, stdin):
        if action == "sweep":
            return 1, b"LOCAL_WORKER_SWEEP failed\n"
        return super().handle(action, args, stdin)


def test_a_failed_sweep_with_a_list_fails_the_cleanup_and_nothing_else(tmp_path):
    plain, _, _ = _record(tmp_path / "a", [READ, LIST], AllowWorld())
    world = SweepFails()
    rec, out, _ = _record(tmp_path / "b", [READ, LIST], world)
    calls = [c[0] for c in world.calls]
    assert calls[-4:] == ["sweep", "numstat", "status", "digest"]
    assert calls.count("sweep") == 1  # the cleanup's sweep is the one that failed
    assert plain["termination"]["cleanup"] == "ok"
    assert out.cleanup == "failed"
    assert rec["termination"]["cleanup"] == "failed"
    assert rec["verification"]["exit_reason"] == plain["verification"]["exit_reason"]
    assert rec["termination"]["abort_cause"] == plain["termination"]["abort_cause"]
    assert rec["termination"]["limit_hit"] is None
    assert not rec["termination"]["interrupted"]
    # the patch comes from numstat alone: a failed sweep only fails the cleanup (#264)
    for key in ("patch_lines_added", "patch_lines_removed"):
        assert rec["verification"][key] == plain["verification"][key]
    assert rec["allowed_base_changed_turns"] == plain["allowed_base_changed_turns"] == 0
    assert rec["spec_feedback"] == plain["spec_feedback"]
    assert [t["action_type"] for t in rec["turns"]] == [t["action_type"] for t in plain["turns"]]
