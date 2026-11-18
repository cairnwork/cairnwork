"""Focused regression probes for the sealed module audit."""

from __future__ import annotations

import json
import py_compile
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pra1353_consume as consume
import pytest
from test_1039_1353_runner import _report
from test_1039_1353_witnesses import PINS


def _module(name: str, path: Path, *, loader: str = "SourceFileLoader") -> dict[str, str]:
    return {"name": name, "file": str(path), "origin": str(path), "loader": loader}


def test_sealed_src_rejects_non_product_top_level_name(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    path = sealed / "src" / "vendorish" / "mod.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 1\n")
    digest = consume.hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="unapproved sealed product module"):
        consume._validate_module_origins(
            [_module("vendorish.mod", path)], {}, {"src/vendorish/mod.py": digest}, sealed, [], []
        )


def test_sealed_src_bytes_are_rehashed_after_run(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    path = sealed / "src" / "cairnwork" / "changed.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 2\n")
    expected = consume.hashlib.sha256(b"VALUE = 1\n").hexdigest()
    with pytest.raises(ValueError, match="sealed module bytes changed"):
        consume._validate_module_origins(
            [_module("cairnwork.changed", path)],
            {},
            {"src/cairnwork/changed.py": expected},
            sealed,
            [],
            [],
        )


def test_post_run_bytecode_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(consume, "check_pins", lambda _pins: None)

    def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        sealed = Path(args[4]).parents[1]
        consume._restore_permissions(sealed)
        cache = sealed / "src" / "cairnwork" / "__pycache__" / "changed.pyc"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"leftover")
        return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

    with pytest.raises(ValueError, match="sealed runner wrote bytecode"):
        consume.launch(PINS, run=fake_run)


def test_module_name_cannot_be_served_from_another_sealed_file(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    path = sealed / "tests" / "pra1353_runner.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="module name served from wrong sealed file"):
        consume._validate_module_origins(
            [_module("pra1353_probe", path)],
            {"tests/pra1353_probe.py": "0" * 64, "tests/pra1353_runner.py": "1" * 64},
            {},
            sealed,
            [],
            [],
        )


@pytest.mark.parametrize("loader", ["ExtensionFileLoader", "SourcelessFileLoader"])
def test_sealed_source_requires_source_file_loader(tmp_path: Path, loader: str) -> None:
    sealed = tmp_path / "sealed"
    path = sealed / "src" / "cairnwork" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 1\n")
    digest = consume.hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="sealed module is not source loaded"):
        consume._validate_module_origins(
            [_module("cairnwork.sample", path, loader=loader)],
            {},
            {"src/cairnwork/sample.py": digest},
            sealed,
            [],
            [],
        )


def test_unlisted_purelib_top_level_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "six.py"
    path.write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="unapproved dependency module"):
        consume._validate_module_origins(
            [_module("six", path)], {}, {}, tmp_path / "sealed", [], [tmp_path]
        )


def test_stdlib_origin_must_match_reported_file(tmp_path: Path) -> None:
    path = tmp_path / "stdlib" / "sample.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n")
    report = _module("sample", path)
    report["origin"] = str(tmp_path / "stdlib" / "other.py")
    with pytest.raises(ValueError, match="stdlib module origin disagrees with file"):
        consume._validate_module_origins([report], {}, {}, tmp_path / "sealed", [path.parent], [])


def test_normal_tracked_src_tree_seals_only_python_sources() -> None:
    sources = consume._tracked_source_bytes()
    assert sources
    assert all(Path(name).suffix == ".py" for name in sources)


@pytest.mark.parametrize(
    ("relative", "mode"),
    [
        ("unchecked", py_compile.PycInvalidationMode.UNCHECKED_HASH),
        ("checked", py_compile.PycInvalidationMode.CHECKED_HASH),
        ("timestamp", py_compile.PycInvalidationMode.TIMESTAMP),
        ("extension", None),
    ],
)
def test_tracked_importable_src_code_fails_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, mode: Any
) -> None:
    monkeypatch.setattr(consume, "check_pins", lambda _pins: None)
    root = tmp_path / "repo"
    root.mkdir()
    for name in PINS:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(consume.ROOT / name, target)
    source = root / "src" / "cairnwork" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n")
    if mode is None:
        bad = source.parent / "native.cpython-314-darwin.so"
        bad.write_bytes(b"not a real extension")
    else:
        cache = source.parent / "__pycache__" / "__init__.cpython-314.pyc"
        py_compile.compile(str(source), cfile=str(cache), doraise=True, invalidation_mode=mode)
        bad = cache
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    monkeypatch.setattr(consume, "ROOT", root)
    calls = []

    def never_run(*args: Any, **kwargs: Any) -> None:
        calls.append(args)
        raise AssertionError("runner started despite tracked importable code")

    with pytest.raises(ValueError, match="tracked source path must end in .py"):
        consume.launch(PINS, run=never_run)
    assert calls == []
    assert bad.exists()


@pytest.mark.parametrize("module", ["pra1353_gate", "pra1353_seal"])
def test_base_install_that_cannot_be_checked_is_refused(module, monkeypatch, tmp_path):
    import importlib
    import os
    import sys

    import pra1353_consume as consume

    check = importlib.import_module(module).check_base_install
    paths = consume.interpreter_paths()
    blind, real_stat = Path(os.path.realpath(sys.base_prefix)).parent, os.stat

    def stat(path, *args, **kwargs):
        if Path(path) == blind:
            raise PermissionError(13, "denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    with pytest.raises(ValueError, match="cannot be checked"):
        check(paths["stdlib"], paths["platstdlib"], tmp_path)
