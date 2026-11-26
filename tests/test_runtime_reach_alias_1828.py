"""#1828: an unresolvable base entry is judged by where it really leads, not by its spelling.

An entry written through a symlinked ancestor that leads into the checkout is inside it, as main's
non-strict realpath found. Same harness as test_runtime_reach_1823: check_with_base with the real
resolve_record run inside the probe.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import resolve_record

OTHER = "tests/test_other.py"
ARCHIVE = "data/lib.zip"


@pytest.fixture
def co(tmp_path):
    root = tmp_path.resolve() / "co"
    (root / "data").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "lib.py").write_text("")
    (root / ARCHIVE).write_bytes(b"PK")
    return root


def _alias(kind: str, co: Path, tmp_path: Path) -> Path:
    """A spelling of the checkout through a symlinked ancestor outside it."""
    out = tmp_path.resolve() / "out"
    out.mkdir()
    (out / "a").symlink_to(co)
    if kind == "direct":
        return out / "a"
    (out / "b").symlink_to(out / "a")  # a chain of links
    return out / "b"


def _run(monkeypatch: Any, root: Path, entry: str, change: str) -> None:
    record = CollectedRecord(frozenset(), (), (entry,), ())

    def fake_verifiers(ctx, base, specs, run_id):
        return [SimpleNamespace(collect_record=resolve_record(record, root))]

    monkeypatch.setattr(bfn, "collect", lambda *a: None)
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: None)
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_base_paths", lambda task: None)
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: (OTHER,))
    monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: ())
    monkeypatch.setattr(bfn, "pick_dir_targets", lambda *a: ())
    monkeypatch.setattr(bfn, "run_verifiers", fake_verifiers)
    task = SimpleNamespace(repo=root, base_commit="b", test_commit="t", test_roots=("tests",))
    bfn.check_with_base(SimpleNamespace(account=None), task, (change,), "run")


def _fail(kind: str, monkeypatch: Any, co: Path, base: Path, tail: str) -> str:
    """An entry below ``base`` (a spelling of the checkout) that cannot be resolved."""
    if kind == "loop":
        (co / "loop").symlink_to(co / "loop")
        return str(base / "loop")
    if kind == "name":
        return str(base / ("n" * 300) / ".." / tail)
    real = os.path.realpath

    def realpath(path, *, strict=False):
        if "closed" in str(path) and strict:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, strict=strict)

    monkeypatch.setattr(os.path, "realpath", realpath)
    return str(base / "closed" / "inner")


@pytest.mark.parametrize("alias", ["direct", "chain"])
@pytest.mark.parametrize("kind", ["loop", "closed", "name"])
def test_an_unresolvable_entry_spelled_through_an_alias_into_the_checkout_refuses(
    co, tmp_path, monkeypatch, alias, kind
):
    base = _alias(alias, co, tmp_path)
    entry = _fail(kind, monkeypatch, co, base / "data", "lib.zip")
    rel = {"loop": "loop", "name": ARCHIVE, "closed": "data/closed/inner"}[kind]
    with pytest.raises(TestCommitRejected):
        _run(monkeypatch, co, entry if kind != "loop" else str(base / "loop"), rel)


@pytest.mark.parametrize("alias", ["direct", "chain"])
@pytest.mark.parametrize("tail", ["pkg/lib.py", "pkg/gone.py", f"{ARCHIVE}/inner"])
def test_an_alias_entry_that_resolves_or_is_absent_passes(co, tmp_path, monkeypatch, alias, tail):
    base = _alias(alias, co, tmp_path)
    _run(monkeypatch, co, str(base / tail), OTHER)


def test_an_alias_to_a_target_outside_the_checkout_passes(co, tmp_path, monkeypatch):
    elsewhere = tmp_path.resolve() / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "loop").symlink_to(elsewhere / "loop")
    (tmp_path.resolve() / "link").symlink_to(elsewhere)
    entry = str(tmp_path.resolve() / "link" / "loop")
    _run(monkeypatch, co, entry, OTHER)
