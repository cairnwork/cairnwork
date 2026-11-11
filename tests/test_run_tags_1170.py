"""#1170: every run-id tag under src/cairnwork is a shared letter recovery and proc_cwd match."""

from __future__ import annotations

import re
from pathlib import Path

import cairnwork
from cairnwork import proc_cwd, recovery, run_tags

_RUN_ID = re.compile(r'attempt_id\}-([a-z]+)"')  # f"{result.attempt_id}-b"
_TAG_ARG = re.compile(r'\btag="([a-z]+)"')  # tag="p" as a keyword or a default
_TAG_ELSE = re.compile(r'\btag="[a-z]+" if [^\n]* else "([a-z]+)"')  # tag="p" if x else "m"
_POSITIONAL = re.compile(r'\b(?:_checked|_side)\([^()]*,\s*"([a-z]+)"\)')  # _checked(..., "q")
# Names built the same way that are not verifier run directories.
_NOT_RUN_DIRS = {"cf", "tested"}  # deferred overlay index/ref name; the attempt's tested ref


def _run_tags_in_src() -> set[str]:
    tags: set[str] = set()
    for path in Path(cairnwork.__file__).parent.glob("*.py"):
        text = path.read_text()
        for pattern in (_RUN_ID, _TAG_ARG, _TAG_ELSE, _POSITIONAL):
            tags.update(pattern.findall(text))
    return tags - _NOT_RUN_DIRS


def test_the_conftest_probe_and_collect_tags_are_found():
    assert {"o", "c", "b", "v", "p", "q", "r", "m"} <= _run_tags_in_src()


def test_every_run_tag_in_src_is_a_shared_single_letter():
    tags = _run_tags_in_src()
    assert all(len(tag) == 1 for tag in tags), tags
    assert tags <= set(run_tags.RUN_TAGS), tags - set(run_tags.RUN_TAGS)


def test_every_run_tag_leftover_is_found_by_recovery_and_proc_cwd(tmp_path):
    attempt = "a0123456789abcdef"
    for tag in sorted(_run_tags_in_src()):
        root = tmp_path / tag
        work = root / f"{attempt}-{tag}-v0.work"
        work.mkdir(parents=True)
        works, _, _ = recovery._run_dirs(root, attempt)
        assert work in works, tag
        assert work.resolve() in proc_cwd.attempt_dirs([root], attempt), tag


def test_a_two_letter_tag_is_not_matched(tmp_path):
    attempt = "a0123456789abcdef"
    work = tmp_path / f"{attempt}-co-v0.work"
    work.mkdir()
    assert work not in recovery._run_dirs(tmp_path, attempt)[0]
    assert work.resolve() not in proc_cwd.attempt_dirs([tmp_path], attempt)
