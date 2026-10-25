"""The start allow-list (#261): what passes, what still refuses, and that the cut and numstat
measure the worker's own edits only."""

from dataclasses import replace

import pytest
from local_worker_allow_fakes import HARNESS, AllowWorld
from local_worker_fakes import META, TASK, World, make_loop, reply

from experimental.local_worker import episode
from experimental.local_worker.agent import RefusedStart, initial_messages
from experimental.local_worker.episode_schema import validate_record
from experimental.local_worker.untracked_allow import UntrackedAllow, build

ALLOW = build(["data", "gen/fixture.json"], None)
READ = reply("read_file", path="src/app.py")
EDIT = reply("edit_file", path="src/app.py", target="x = 1", replacement="x = 2")


def _loop(tmp_path, replies, world=None, allow=ALLOW):
    world = world if world is not None else AllowWorld()
    loop, world, _, _ = make_loop(tmp_path, replies, world=world)
    loop.settings = replace(loop.settings, untracked_allow=allow)
    return loop, world


def _record(tmp_path, replies, world=None, allow=ALLOW):
    loop, world = _loop(tmp_path, replies, world, allow)
    out = loop.run(initial_messages(TASK))
    rec = episode.build_record(META, out)
    validate_record(rec)
    return rec, out, world


def _refused(tmp_path, world, allow=ALLOW, match="not a clean git tree"):
    loop, _ = _loop(tmp_path, [], world, allow)
    with pytest.raises(RefusedStart, match=match):
        loop.run(initial_messages(TASK))


# -- without a list: exactly as before ----------------------------------------------------------
def test_without_a_list_an_untracked_file_refuses_and_no_digest_runs(tmp_path):
    world = AllowWorld({"gen/fixture.json": "{}\n"})
    world.ignored = ()
    loop, _ = _loop(tmp_path, [], world, UntrackedAllow())
    with pytest.raises(RefusedStart) as exc:
        loop.run(initial_messages(TASK))
    assert str(exc.value) == "checkout is not a clean git tree at its base"  # the old message
    assert [c[0] for c in world.calls] == ["status"]


def test_without_a_list_the_queries_are_the_old_ones(tmp_path):
    loop, world, _, _ = make_loop(tmp_path, [READ, EDIT])
    out = loop.run(initial_messages(TASK))
    assert "digest" not in [c[0] for c in world.calls]
    assert out.patch == (1, 1, 0) and out.cleanup == "ok"


# -- what passes --------------------------------------------------------------------------------
def test_allowed_harness_files_start_and_never_count_as_an_edit(tmp_path):
    rec, out, world = _record(tmp_path, [READ, reply("list_dir", path="data")])
    assert {e[:1] for e in out.base.entries} == {"!", "?", "r"}  # "! data/", one hash per root
    assert len(out.base.digest) == 3  # the three harness files, in memory only
    assert rec["first_edit_cut"]["cut_status"] == "NO_CUT"
    assert rec["first_source_edit"]["cut_status"] == "NO_CUT"
    assert rec["verification"]["patch_lines_added"] == 0  # the harness's lines are subtracted
    assert [t["tree_differs_from_base"] for t in rec["turns"][:2]] == [False, False]
    assert out.cleanup == "ok"


def test_an_empty_directory_inside_the_list_passes(tmp_path):
    world = AllowWorld()
    world.empty_dirs = {"data/stub"}
    rec, _, _ = _record(tmp_path, [READ], world)
    assert rec["first_edit_cut"]["cut_status"] == "NO_CUT"


def test_a_glob_entry_allows_matching_prefixes(tmp_path):
    world = AllowWorld({"src/pkg.egg-info/PKG-INFO": "m\n"})
    world.ignored = ()
    rec, _, _ = _record(tmp_path, [READ], world, build(None, ["src/*.egg-info"]))
    assert rec["first_edit_cut"]["cut_status"] == "NO_CUT"


# -- what still refuses -------------------------------------------------------------------------
def test_an_untracked_file_outside_the_list_refuses(tmp_path):
    _refused(tmp_path, AllowWorld({**HARNESS, "stray.py": "s\n"}), match="'stray.py'")


def test_an_ignored_file_outside_the_list_refuses(tmp_path):
    _refused(tmp_path, AllowWorld({**HARNESS, "junk.log": "j\n"}), match="! 'junk.log'")


def test_an_empty_directory_outside_the_list_refuses(tmp_path):
    world = AllowWorld()
    world.empty_dirs = {"pkg/stub"}
    _refused(tmp_path, world, match="x d 'pkg/stub'")


def test_a_fifo_inside_the_list_refuses(tmp_path):
    world = AllowWorld()
    world.fifos = {"data/pipe"}
    _refused(tmp_path, world, match="x o 'data/pipe'")


def test_a_modified_tracked_file_inside_the_list_refuses(tmp_path):
    world = AllowWorld()
    world.files["src/app.py"] = "x = 9\n"
    _refused(tmp_path, world, build(["data", "gen", "src"], None), match="1 'src/app.py'")


@pytest.mark.parametrize("where", ["data/link", "gen/fixture.json"])
def test_a_symlink_inside_the_list_refuses(tmp_path, where):
    """Wherever it points (here: outside the checkout); in an ignored directory or not."""
    world = AllowWorld()
    world.files[where] = "/etc/hosts"
    world.symlinks = {where}
    _refused(tmp_path, world, match=f"h '{where}'")


def test_an_untracked_ignore_file_inside_the_list_refuses(tmp_path):
    """The base's ignore rules come from HEAD and .git/info/exclude, never a planted file."""
    world = AllowWorld({**HARNESS, "gen/.gitignore": "*\n"})
    _refused(tmp_path, world, build(["data", "gen"], None), match="gen/.gitignore")


def test_a_head_other_than_the_pinned_base_refuses(tmp_path):
    loop, _ = _loop(tmp_path, [])
    with pytest.raises(RefusedStart, match="is not the pinned base"):
        loop.run(initial_messages(TASK), expected_base="c" * 40)


def test_an_unobserved_digest_refuses(tmp_path):
    class NoDigest(AllowWorld):
        def digest(self) -> bytes:
            return b"not a raw diff"

    _refused(tmp_path, NoDigest())


def test_a_resume_base_must_have_the_parents_entries(tmp_path):
    loop, _ = _loop(tmp_path, [])
    parent = loop._check_base(loop.observe(), None)
    other = AllowWorld({**HARNESS, "data/x.dat": "changed\n"})
    _refused(tmp_path / "b", other, replace(ALLOW, expected=parent.entries), match="parent")
    rec, _, _ = _record(
        tmp_path / "c", [READ], AllowWorld(), replace(ALLOW, expected=parent.entries)
    )
    assert rec["first_edit_cut"]["cut_status"] == "NO_CUT"


# -- the cut: the worker's own edits, also to an allowed path -----------------------------------
def test_a_write_to_an_allowed_untracked_file_is_the_cut(tmp_path):
    write = reply("write_file", path="gen/fixture.json", content='{"a": 1}\n')
    rec, out, _ = _record(tmp_path, [READ, write, EDIT])
    cut = rec["first_edit_cut"]
    assert (cut["cut_status"], cut["cut_turn_index"], cut["cut_cause"]) == ("CUT", 1, "write_file")
    assert cut["changed_paths_total"] == 1 and cut["cut_ambiguous"] is False
    assert rec["first_source_edit"]["turn_index"] == 1  # not ignored: a source edit
    assert out.patch is None and out.cleanup == "failed"  # an allowed base file changed


def test_a_write_into_an_allowed_ignored_directory_is_the_cut(tmp_path):
    write = reply("write_file", path="data/x.dat", content="one\nTWO\n")
    rec, _, _ = _record(tmp_path, [READ, write, EDIT])
    assert rec["first_edit_cut"]["cut_turn_index"] == 1
    assert rec["first_edit_cut"]["cut_cause"] == "write_file"
    assert rec["first_source_edit"]["turn_index"] == 2  # data/ is ignored: no source edit


def test_a_new_file_in_an_allowed_ignored_directory_is_the_cut(tmp_path):
    write = reply("write_file", path="data/new.dat", content="n\n")
    rec, out, _ = _record(tmp_path, [READ, write])
    assert rec["first_edit_cut"]["cut_turn_index"] == 1
    assert out.patch == (1, 0, 0)  # the harness files are unchanged: only the worker's line


def test_run_tests_rewriting_an_allowed_file_is_the_cut(tmp_path):
    world = AllowWorld()
    world.pytest_writes = {"data/sub/y.dat": "regenerated\n"}
    rec, _, _ = _record(tmp_path, [READ, reply("run_tests"), EDIT], world)
    cut = rec["first_edit_cut"]
    assert (cut["cut_turn_index"], cut["cut_cause"]) == (1, "run_tests_side_effect")


def test_rewriting_an_allowed_file_unchanged_is_no_cut(tmp_path):
    same = reply("write_file", path="data/x.dat", content=HARNESS["data/x.dat"])
    rec, out, _ = _record(tmp_path, [READ, same, EDIT])
    cut = rec["first_edit_cut"]
    assert (cut["cut_turn_index"], cut["changed_paths_total"]) == (2, 1)  # only src/app.py
    assert out.patch == (1, 1, 0) and out.cleanup == "ok"


def test_an_edit_elsewhere_lists_only_its_own_path(tmp_path):
    rec, out, _ = _record(tmp_path, [READ, EDIT])
    cut = rec["first_edit_cut"]
    assert (cut["cut_turn_index"], cut["cut_cause"], cut["cut_ambiguous"]) == (
        1,
        "edit_file",
        False,
    )
    assert cut["changed_paths_total"] == 1
    assert out.patch == (1, 1, 0)  # as without harness files


def test_a_root_hash_stands_for_its_files_and_stays_small(tmp_path):
    world = AllowWorld({f"data/f{i}.dat": f"{i}\n" for i in range(500)})
    rec, out, _ = _record(
        tmp_path, [READ, reply("write_file", path="data/f7.dat", content="x\n")], world
    )
    assert out.base.entries == ("! data/", next(e for e in out.base.entries if e[:2] == "r "))
    assert rec["first_edit_cut"]["changed_paths_total"] == 1  # data/f7.dat, not all 500
    assert rec["first_edit_cut"]["cut_cause"] == "write_file"


# -- an installed wrapper from before the digest query -----------------------------------------
class OldWrapper(AllowWorld):
    """The pre-#261 wrapper: no digest action (exit 64, "unknown action")."""

    def handle(self, action, args, stdin):
        if action == "digest":
            return 64, b"local-worker-tool: refusing: unknown action 'digest'\n"
        return super().handle(action, args, stdin)


def test_the_old_wrapper_serves_a_run_without_a_list(tmp_path):
    world = OldWrapper({})
    rec, out, _ = _record(tmp_path, [READ, EDIT], world, UntrackedAllow())
    assert "digest" not in [c[0] for c in world.calls]
    assert rec["first_edit_cut"]["cut_turn_index"] == 1 and out.patch == (1, 1, 0)


def test_the_old_wrapper_refuses_a_run_with_a_list(tmp_path):
    _refused(tmp_path, OldWrapper())


def test_the_clean_base_cut_is_unchanged_without_a_list(tmp_path):
    rec, _, _ = _record(tmp_path, [READ, EDIT], World(), UntrackedAllow())
    cut = rec["first_edit_cut"]
    assert (cut["cut_turn_index"], cut["changed_paths_total"]) == (1, 1)


def test_a_sibling_name_is_not_under_an_allowed_root(tmp_path):
    """gen/fixture.jsonx is no file of the root gen/fixture.json: only it changed."""
    write = reply("write_file", path="gen/fixture.jsonx", content="n\n")
    rec, out, _ = _record(tmp_path, [READ, write])
    assert rec["first_edit_cut"]["changed_paths_total"] == 1
    assert out.patch == (1, 0, 0)
