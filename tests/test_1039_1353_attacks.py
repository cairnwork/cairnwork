"""Fast repetitions of pinned timestamp-bytecode shadow attacks."""

from __future__ import annotations

import importlib._bootstrap_external as bootstrap_external
import json
import py_compile
import subprocess
from pathlib import Path
from typing import Any

import pra1353_consume as consume
import pytest
from test_1039_1353_runner import _report, _runner
from test_1039_1353_witnesses import PINS


def test_launch_seals_sources_and_rebuilds_a_clean_environment(monkeypatch, tmp_path):
    import importlib._bootstrap_external as bootstrap_external

    monkeypatch.setenv("CW1353_CANARY", "visible-only-to-caller")
    py_compile.compile(__file__, doraise=True)
    assert Path(bootstrap_external.cache_from_source(__file__)).exists()
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "json.py").write_text("raise RuntimeError('PYTHONPATH shadow loaded')\n")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    observed = {}

    def fake_run(args: Any, **kwargs: Any) -> Any:
        observed.update(kwargs)
        observed["args"] = args
        sealed_runner = Path(args[4])
        observed["runner_bytes"] = sealed_runner.read_bytes()
        observed["sealed_root"] = sealed_runner.parents[1]
        return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

    report = consume.launch(PINS, run=fake_run)
    assert len(report["rows"]) == 56
    assert observed["runner_bytes"] == consume.RUNNER.read_bytes()
    assert "CW1353_CANARY" not in observed["env"]
    assert "PYTHONPATH" not in observed["env"]
    assert observed["cwd"].name == "empty"
    assert observed["args"][1:4] == ["-I", "-S", "-B"]
    assert str(consume.ROOT) not in observed["args"]


@pytest.mark.parametrize(
    "target",
    ["pra1353_derive.py", "pra1353_probe.py", "pra1353_extra.py", "pra1353_recipes.py"],
)
def test_repo_and_src_shadow_files_are_outside_the_sealed_snapshot(target):
    targets = [consume.ROOT / target, consume.ROOT / "src" / target]
    made = []
    try:
        for path in targets:
            path.write_text("FORGED = True\n")
            made.append(path)

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / target).exists()
            assert not (sealed / "src" / target).exists()
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        result = consume.launch(PINS, run=fake_run)
        assert len(result["rows"]) == 56
    finally:
        for path in made:
            path.unlink()


def test_package_and_pyc_shadows_are_not_copied_into_the_sealed_run(monkeypatch):
    import importlib._bootstrap_external as bootstrap_external

    package = consume.ROOT / "tests/pra1353_derive"
    package.mkdir()
    init = package / "__init__.py"
    init.write_text("FORGED = True\n")
    source = consume.ROOT / "tests/pra1353_derive.py"
    stat = source.stat()
    code = compile("FORGED = True\n", str(source), "exec")
    cache = Path(bootstrap_external.cache_from_source(str(source)))
    cache.parent.mkdir(exist_ok=True)
    cache_data = bootstrap_external._code_to_timestamp_pyc(code, int(stat.st_mtime), stat.st_size)
    previous = cache.read_bytes() if cache.exists() else None
    cache.write_bytes(cache_data)
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / "tests/pra1353_derive/__init__.py").exists()
            assert not list((sealed / "tests").rglob("*.pyc"))
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        init.unlink()
        package.rmdir()
        if previous is None:
            cache.unlink()
        else:
            cache.write_bytes(previous)


@pytest.mark.parametrize("name", ["pra1353_probe", "pra1353_extra", "pra1353_recipes"])
def test_pinned_package_directory_shadow_is_not_copied(name):
    package = consume.ROOT / "tests" / name
    package.mkdir()
    init = package / "__init__.py"
    init.write_text("FORGED = True\n")
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / "tests" / name / "__init__.py").exists()
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        init.unlink()
        package.rmdir()


@pytest.mark.parametrize("name", ["pra1353_derive", "pra1353_probe"])
def test_pinned_package_directory_shadow_for_core_helpers_is_not_copied(name):
    package = consume.ROOT / "tests" / name
    package.mkdir()
    init = package / "__init__.py"
    init.write_text("FORGED = True\n")
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / "tests" / name / "__init__.py").exists()
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        init.unlink()
        package.rmdir()


@pytest.mark.parametrize(
    "relative",
    [
        "tests/pra1353_probe.py",
        "tests/pra1353_extra.py",
        "tests/pra1353_recipes.py",
        "tests/pra1353_c10.py",
        "tests/pra1353_receipt.py",
        "tests/attest/binding_helpers.py",
    ],
)
def test_forged_timestamp_bytecode_is_not_copied(relative):
    import importlib._bootstrap_external as bootstrap_external

    source = consume.ROOT / relative
    stat = source.stat()
    cache = Path(bootstrap_external.cache_from_source(str(source)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    code = compile("FORGED = True\n", str(source), "exec")
    forged = bootstrap_external._code_to_timestamp_pyc(code, int(stat.st_mtime), stat.st_size)
    previous = cache.read_bytes() if cache.exists() else None
    cache.write_bytes(forged)
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not list((sealed / "tests").rglob("*.pyc"))
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        if previous is None:
            cache.unlink()
        else:
            cache.write_bytes(previous)


@pytest.mark.parametrize(
    "relative",
    [
        "base64.py",
        "tempfile.py",
        "subprocess.py",
        "hashlib.py",
        "re.py",
        "dataclasses.py",
        "ast.py",
        "src/tests/__init__.py",
        "src/tests/attest/binding_helpers.py",
    ],
)
def test_stdlib_and_namespace_shadows_are_outside_snapshot(relative):
    path = consume.ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("FORGED = True\n")
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / relative).exists()
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        path.unlink()


def test_unknown_loaded_module_is_rejected():
    report = _report()
    report["modules"] = [
        {
            "name": "evil",
            "file": str(consume.ROOT / "tests/evil.py"),
            "origin": str(consume.ROOT / "tests/evil.py"),
            "loader": "SourceFileLoader",
        }
    ]
    with pytest.raises(ValueError, match="outside allowed roots"):
        consume.launch(PINS, run=_runner(report))


def test_clean_run_ignores_unrelated_untracked_files():
    note = consume.ROOT / "notes.txt"
    helper = consume.ROOT / "tests/other_helper.py"
    note.write_text("irrelevant")
    helper.write_text("UNIMPORTED = True\n")
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not (sealed / "notes.txt").exists()
            assert not (sealed / "tests/other_helper.py").exists()
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        note.unlink()
        helper.unlink()


def test_a2_package_shadow_cannot_change_real_runner_rows():
    package = consume.ROOT / "tests/pra1353_derive"
    package.mkdir()
    (package / "__init__.py").write_text(
        "FORGED = True\ndef derive(*args): return {'observed': 'FORGED'}\n"
    )
    try:
        report = consume.launch(PINS)
        assert len(report["rows"]) == 56
        assert all(row["observed"] != "FORGED" for row in report["rows"])
        paths = consume.sysconfig.get_paths()
        dependencies = [Path(paths["purelib"]), Path(paths["platlib"])]
        assert all(
            not consume._is_under(Path(item["file"]), consume.ROOT)
            or any(consume._is_under(Path(item["file"]), root) for root in dependencies)
            for item in report["modules"]
            if item.get("file")
        )
    finally:
        (package / "__init__.py").unlink(missing_ok=True)
        if package.exists():
            package.rmdir()


CASES = (
    "tests/pra1353_probe.py",
    "tests/pra1353_extra.py",
    "tests/pra1353_recipes.py",
    "tests/pra1353_c10.py",
    "tests/pra1353_receipt.py",
    "tests/attest/binding_helpers.py",
)


@pytest.mark.parametrize("relative", CASES)
def test_forged_timestamp_pyc_is_not_copied(relative: str) -> None:
    source = consume.ROOT / relative
    stat = source.stat()
    cache = Path(bootstrap_external.cache_from_source(str(source)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    forged = bootstrap_external._code_to_timestamp_pyc(
        compile("FORGED = True\n", str(source), "exec"), int(stat.st_mtime), stat.st_size
    )
    previous = cache.read_bytes() if cache.exists() else None
    cache.write_bytes(forged)
    try:

        def fake_run(args: Any, **kwargs: Any) -> Any:
            sealed = Path(args[4]).parents[1]
            assert not list((sealed / "tests").rglob("*.pyc"))
            return subprocess.CompletedProcess(args, 0, json.dumps(_report()), "")

        assert len(consume.launch(PINS, run=fake_run)["rows"]) == 56
    finally:
        if previous is None:
            cache.unlink()
        else:
            cache.write_bytes(previous)
