"""The read-only search tool (#88 robustness, A2; on by default per ADDENDUM_05): the wrapper's
grep under a profile that may write nothing, the tools call, the loop and the manifest."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from local_worker_fakes import META, TAG, TASK, WRAPPER, cli_harness, lines, make_loop, make_runs
from local_worker_fakes import run_wrapper as run
from test_local_worker_tools import action_of, make

from experimental.local_worker import episode, redact, tools
from experimental.local_worker.agent import initial_messages
from experimental.local_worker.episode_schema import validate_record


@pytest.fixture
def runs(tmp_path):
    runs = make_runs(tmp_path)
    co = runs / TAG / "checkout"
    (co / "sub" / "b.py").write_text("def hello():\n    return 'Hello'\n")
    (co / ".git" / "config").write_text("hello in git\n")
    (co / "bin.dat").write_bytes(b"hello\0binary\n")
    (tmp_path / "outside.txt").write_text("hello outside\n")
    (co / "link.txt").symlink_to(tmp_path / "outside.txt")
    (co / "linkdir").symlink_to(tmp_path)
    return runs


def search(runs, *args):
    return run(runs, "search", str(runs / TAG), *args)


# -- the wrapper -----------------------------------------------------------------------------------
@pytest.mark.sandbox
def test_search_finds_fixed_strings_without_git_binaries_or_symlinks(runs):
    proc = search(runs, "fixed", "hello", ".")
    assert proc.returncode == 0, proc.stderr
    assert sorted(proc.stdout.decode().splitlines()) == [
        "./a.txt:1:hello",
        "./sub/b.py:1:def hello():",
    ]
    assert search(runs, "fixed", "hello", "sub").stdout == b"sub/b.py:1:def hello():\n"
    assert search(runs, "fixed", "hello", "a.txt").stdout == b"a.txt:1:hello\n"
    assert search(runs, "fixed", "h.llo", ".").returncode == 1  # fixed: the dot is literal


@pytest.mark.sandbox
def test_search_regex_and_no_match(runs):
    proc = search(runs, "regex", "^def h[a-z]+", "sub")
    assert proc.returncode == 0 and proc.stdout == b"sub/b.py:1:def hello():\n"
    proc = search(runs, "fixed", "nowhere-to-be-found", ".")
    assert proc.returncode == 1 and proc.stdout == b""


@pytest.mark.sandbox
@pytest.mark.parametrize(
    "args",
    [
        ("fixed", "hello", "link.txt"),  # a symlink is never searched
        ("fixed", "hello", "linkdir"),
        ("fixed", "hello", "../" + TAG),
        ("fixed", "hello", "sub/../sub"),  # a ".." is refused even when it lands inside
        ("fixed", "hello", "./sub"),
        ("fixed", "hello", "/etc"),
        ("fixed", "hello", ".git"),
        ("fixed", "hello", "sub/.GIT"),
        ("fixed", "hello", "missing"),
        ("fixed", "", "."),
        ("fixed", "a\nb", "."),
        ("perl", "hello", "."),
        ("fixed", "hello"),
        ("fixed", "hello", ".", "extra"),
    ],
)
def test_search_refuses(runs, args):
    proc = search(runs, *args)
    assert proc.returncode == 64, proc.stderr
    assert b"refusing" in proc.stderr and proc.stdout == b""


@pytest.mark.sandbox
@pytest.mark.parametrize(
    ("link", "target", "path", "why"),
    [
        ("gd", ".git", "gd/config", b"resolves into a .git directory"),
        ("td", "../tmp", "td/x.txt", b"resolves outside the checkout"),  # the run's tmp
    ],
)
def test_a_symlinked_directory_on_the_way_cannot_leave_the_checkout(runs, link, target, path, why):
    """Review of PR 251 (1): the directory checks refuse these before grep starts. Both targets
    are readable under the tool profile; the read-only profile denies them as well (the second
    layer, which a symlink swapped in after the checks meets: test_local_worker_readonly)."""
    co = runs / TAG / "checkout"
    (runs / TAG / "tmp" / "x.txt").write_text("hello from tmp\n")
    (co / link).symlink_to(target)
    proc = search(runs, "fixed", "hello", path)
    assert proc.returncode == 64 and why in proc.stderr, proc.stderr
    assert proc.stdout == b""


@pytest.mark.sandbox
def test_a_fifo_in_the_tree_does_not_block_search(runs):
    """Review of PR 251 (3): BSD grep -r opens a FIFO and blocks on it unless -D skip."""
    co = runs / TAG / "checkout"
    os.mkfifo(co / "sub" / "pipe")
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(runs),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    argv = ["/bin/sh", str(WRAPPER), "search", str(runs / TAG), "fixed", "hello", "."]
    try:
        proc = subprocess.run(argv, capture_output=True, env=env, cwd="/", timeout=30, check=False)
    except subprocess.TimeoutExpired:  # release the grep blocked on the FIFO: a writer's open
        os.close(os.open(co / "sub" / "pipe", os.O_WRONLY | os.O_NONBLOCK))
        raise
    assert proc.returncode == 0 and b"./a.txt:1:hello" in proc.stdout, proc.stderr


@pytest.mark.sandbox
def test_search_probe_shows_the_search_profile_denies_writes(runs):
    """The canary's search-probe (review of PR 251, 7) tries to write the checkout, tmp and
    /dev/null under the profile search runs under: it starts, fails, and nothing is created."""
    root = runs / TAG
    proc = run(runs, "search-probe", str(root))
    assert proc.returncode not in (0, 64) and b"Operation not permitted" in proc.stdout
    assert not (root / "checkout" / "search-probe").exists()
    assert not (root / "tmp" / "search-probe").exists()
    assert run(runs, "search-probe", str(root), "x").returncode == 64


def test_the_canary_requires_the_search_probes():
    from experimental.local_worker import canary_check

    assert "search a checkout file" in canary_check.REQUIRED_PROBES["positive_controls"]
    assert "search profile denies writes" in canary_check.REQUIRED_PROBES["b_write_denied"]
    for path in ("links/link_dir/secret.txt", "links/link_private"):  # Codex review, should-fix 1
        assert f"wrapper search {path}" in canary_check.REQUIRED_PROBES["a_read_denied"]


@pytest.mark.sandbox
def test_search_profile_may_write_nothing(runs, tmp_path):
    """A copy of the wrapper whose search runs touch instead of grep: the profile denies it."""
    text = WRAPPER.read_text()
    grep = '/usr/bin/grep -r -n -I -D skip "$GREP_MODE" --exclude-dir=.git \\\n'
    grep += '      -e "$PATTERN" -- "$TARGET"'
    assert text.count(grep) == 1
    copy = tmp_path / "local-worker-tool"
    copy.write_text(text.replace(grep, '/usr/bin/touch -- "$PATTERN"'))
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(runs),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    root = runs / TAG
    for target in (root / "checkout" / "planted.txt", root / "tmp" / "planted.txt"):
        proc = subprocess.run(
            ["/bin/sh", str(copy), "search", str(root), "fixed", str(target), "."],
            capture_output=True,
            env=env,
            cwd="/",
            check=False,
        )
        assert proc.returncode != 0 and not target.exists(), proc.stderr


# -- the tools call ------------------------------------------------------------------------------
def test_tools_search_passes_mode_pattern_and_path(tmp_path):
    t, inv = make(tmp_path, (0, b"a.py:1:x\n", b""), (1, b"", b""))
    call = t.search("x", "src", regex=True)
    argv = inv.calls[0][0]
    assert action_of(argv) == "search" and argv[-3:] == ["regex", "x", "src"]
    assert call.action_type == "search" and call.error_category == "NONE"
    call = t.search("-v")
    assert inv.calls[1][0][-3:] == ["fixed", "-v", "."] and call.error_category == "NO_MATCH"


def test_search_output_is_truncated(tmp_path):
    big = b"x.py:1:" + b"y" * 10_000
    t, _ = make(tmp_path, (0, big, b""), max_output_bytes=1000)
    call = t.search("y")
    assert call.stdout_truncated and call.stdout_bytes == len(big) and "truncated" in call.output


@pytest.mark.parametrize(("rc", "category"), [(0, "NONE"), (1, "NO_MATCH"), (2, "TOOL_ERROR")])
def test_search_categories(rc, category):
    assert tools.error_category("search", rc, b"") == category
    if rc == 1:
        assert tools.error_category("read_file", 1, b"") == "TOOL_ERROR"


# -- the loop ------------------------------------------------------------------------------------
SEARCH = {"name": "search", "pattern": "x = 1", "path": "src"}


def loop_run(tmp_path, replies, search=True):
    from local_worker_fakes import reply

    loop, world, endpoint, _ = make_loop(
        tmp_path, [reply(**r) for r in replies], search_tool=search
    )
    out = loop.run(initial_messages(TASK, search))
    rec = episode.build_record(META, out)
    validate_record(rec)
    return rec, world, endpoint


def test_the_loop_offers_and_runs_search_by_default(tmp_path):
    rec, world, endpoint = loop_run(
        tmp_path, [SEARCH, {**SEARCH, "pattern": "zzz", "regex": False}]
    )
    names = [t["function"]["name"] for t in endpoint.payloads[0]["tools"]]
    assert "search" in names and "search" in endpoint.payloads[0]["messages"][0]["content"]
    assert [c[:2] for c in world.calls if c[0] == "search"][0] == (
        "search",
        ["fixed", "x = 1", "src"],
    )
    first, second = rec["turns"][:2]
    assert first["action_type"] == "search" and first["error_category"] == "NONE"
    assert second["error_category"] == "NO_MATCH"
    assert endpoint.payloads[1]["messages"][-1]["content"] == "[exit 0]\nsrc/app.py:1:x = 1\n"
    assert set(first["tool_args"]) == {"pattern", "path"} and "x = 1" not in json.dumps(rec)
    assert second["tool_args"]["regex"] is False


def test_with_search_off_a_search_call_is_an_invalid_call(tmp_path):
    rec, world, endpoint = loop_run(tmp_path, [SEARCH], search=False)
    names = [t["function"]["name"] for t in endpoint.payloads[0]["tools"]]
    assert "search" not in names
    assert rec["turns"][0]["error_category"] == "INVALID_CALL"
    assert "search" not in [c[0] for c in world.calls]


def test_search_arguments_are_published_as_digests():
    args = {"pattern": "SECRET-PATTERN", "path": "src", "regex": True, "junk": 1}
    pub = redact.public_args("search", args, 0, frozenset({"src"}))
    assert pub == {"pattern": redact.digest("SECRET-PATTERN"), "path": "src", "regex": True}
    assert redact.public_args("search", args, 1, frozenset({"src"}))["path"] != "src"


# -- the manifest --------------------------------------------------------------------------------
@pytest.fixture
def harness(tmp_path, monkeypatch):
    return cli_harness(tmp_path, monkeypatch)


def manifest(out):
    return json.loads((out / "manifest.json").read_text())


def test_the_manifest_freezes_the_search_flag_and_the_guards(harness):
    run_cli, out = harness
    assert run_cli([], "run") == 0
    m = manifest(out)
    assert m["search_tool"] is True and "search" in m["tools"] and m["tools_absent"] == []
    lim = m["limits"]
    assert (lim["repeat_hint_at"], lim["repeat_stop_at"]) == (None, 5)
    assert lim["repeat_requires_unchanged_tree"] is True
    assert (lim["invalid_call_limit"], lim["context_window"]) == (6, 65536)
    assert m["worker_prompt_sha256"] != m["tool_schemas_sha256"]
    [rec] = lines(out)
    assert rec["manifest_sha256"]


def test_other_values_are_frozen_too(tmp_path, monkeypatch):
    run_cli, out = cli_harness(tmp_path, monkeypatch)
    extra = ["--search-tool", "off", "--repeat-hint-at", "3", "--repeat-stop-at", "6"]
    extra += ["--repeat-requires-unchanged-tree", "false", "--invalid-call-limit", "4"]
    extra += ["--context-window", "32768"]
    assert run_cli([], "run", *extra) == 0
    m = manifest(out)
    assert m["search_tool"] is False and "search" not in m["tools"]
    assert m["tools_absent"] == ["search"]
    lim = m["limits"]
    assert (lim["repeat_hint_at"], lim["repeat_stop_at"], lim["invalid_call_limit"]) == (3, 6, 4)
    assert lim["repeat_requires_unchanged_tree"] is False and lim["context_window"] == 32768


def test_a_corpus_frozen_with_search_on_refuses_a_run_with_it_off(harness):
    run_cli, out = harness
    assert run_cli([], "run") == 0
    assert run_cli([], "run", "--search-tool", "off") == 2  # the manifest differs: a new corpus


@pytest.mark.parametrize(
    ("name", "path", "expected"),
    [("search", "sub/", "sub"), ("list_dir", "src//", "src"), ("read_file", "a.py/", "a.py")],
)
def test_a_trailing_slash_is_dropped_before_the_wrapper(name, path, expected):
    """Review of PR 251 (8b): the wrapper refuses an empty component, so "sub/" would be refused."""
    from experimental.local_worker.agent_protocol import decode_call

    args = {"path": path, **({"pattern": "x"} if name == "search" else {})}
    call = {"id": "c", "function": {"name": name, "arguments": json.dumps(args)}}
    assert decode_call(call, 1, (name,))[1]["path"] == expected


def test_a_root_slash_is_left_for_the_wrapper_to_refuse():
    from experimental.local_worker.agent_protocol import decode_call

    call = {"id": "c", "function": {"name": "list_dir", "arguments": '{"path": "/"}'}}
    assert decode_call(call, 1)[1]["path"] == "/"


@pytest.mark.sandbox
def test_the_wrapper_refuses_a_trailing_slash_so_python_drops_it(runs):
    assert search(runs, "fixed", "hello", "sub/").returncode == 64
    assert search(runs, "fixed", "hello", "sub").returncode == 0
