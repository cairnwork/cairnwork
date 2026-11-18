"""Regression probes for the sealed #1353 witness gate."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pra1353_consume as consume
import pytest
from pra1353_seal import read_module_report, tracked_py_blobs
from test_1039_1353_witnesses import PINS

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


def _repo(root: Path, entries: dict[str, tuple[str, bytes]]) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=GIT_ENV)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "core.precomposeunicode", "false"], check=True
    )
    for name, (mode, data) in entries.items():
        oid = (
            subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input=data,
                check=True,
                capture_output=True,
                env=GIT_ENV,
            )
            .stdout.strip()
            .decode()
        )
        subprocess.run(
            ["git", "-C", str(root), "update-index", "--add", "--cacheinfo", mode, oid, name],
            check=True,
            env=GIT_ENV,
        )
    return root


def _base() -> dict[str, tuple[str, bytes]]:
    return {
        "src/cairnwork/__init__.py": ("100644", b"VALUE = 1\n"),
        "src/cairnwork/module.py": ("100644", b"VALUE = 2\n"),
    }


def test_variant_1_sitecustomize_and_non_product_module_are_rejected(tmp_path: Path):
    repo = _repo(tmp_path / "repo", {**_base(), "src/sitecustomize.py": ("100644", b"pass\n")})
    with pytest.raises(ValueError, match="invalid source path"):
        tracked_py_blobs(repo, prefix="src/")


def test_variant_2_missing_child_module_report_fails_closed():
    with pytest.raises(ValueError, match="child module-origin reports missing"):
        consume._validate_child_reports([], {}, {}, ROOT, [], [])


def test_variant_2_os_exit_without_finally_report_fails_closed(tmp_path: Path):
    report = tmp_path / "child.json"
    code = (
        "import os\n"
        "try:\n    os._exit(0)\n"
        f"finally:\n    open({str(report)!r}, 'w').write('should not happen')\n"
    )
    subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], check=True)
    assert not report.exists()
    with pytest.raises(ValueError, match="missing or unreadable"):
        read_module_report(report)


def test_variant_2_forged_child_module_path_is_rejected():
    fake = [
        {
            "name": "rogue",
            "file": "/tmp/rogue.py",
            "origin": "/tmp/rogue.py",
            "loader": "SourceFileLoader",
        }
    ]
    with pytest.raises(ValueError, match="outside allowed roots"):
        consume._validate_module_origins(fake, {}, {}, ROOT / "sealed", [], [])


def _child_roots(shadow: Path, *, stdlib_first: bool) -> str:
    paths = sysconfig.get_paths()
    stdlib = [paths["stdlib"], paths["platstdlib"], str(Path(paths["platstdlib"]) / "lib-dynload")]
    roots = [*stdlib, str(shadow), str(ROOT / "tests"), paths["purelib"], paths["platlib"]]
    driver = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
        "import pra1353_runner\n"
        "try:\n    pra1353_runner._bootstrap(json.loads(sys.argv[1]))\n"
        "except Exception:\n    pass\n"
        "print(os.environ['PRA1353_IMPORT_ROOTS'])\n"
    )
    if not stdlib_first:
        return json.dumps([str(shadow), *roots])
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", driver, json.dumps(roots)],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip().splitlines()[-1]


_STUB_RUNNER = (
    "import csv\n"
    "MODULE = 'pra1353_stub_runner'\n"
    "_RUNNER_SOURCE = 'def _call(f, a):\\n    return f(*a[\\'args\\'])\\n'\n"
)


@pytest.mark.parametrize("child", ["verify", "c10"])
@pytest.mark.parametrize("stdlib_first", [True, False])
def test_variant_3_real_children_load_stdlib_before_sealed_source(
    child: str, stdlib_first: bool, tmp_path: Path
):
    import pra1353_c10
    import pra1353_probe

    shadow = tmp_path / "src"
    (shadow / "cairnwork").mkdir(parents=True)
    (shadow / "cairnwork/__init__.py").write_text("")
    (shadow / "cairnwork/verify_cli.py").write_text("import csv\n")
    (shadow / "cairnwork/blackbox_runner.py").write_text(_STUB_RUNNER)
    marker = tmp_path / "shadow-loaded"
    (shadow / "csv.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('shadow')\n"
    )
    report = tmp_path / "child.json"
    roots = _child_roots(shadow, stdlib_first=stdlib_first)
    paths = sysconfig.get_paths()
    if stdlib_first:
        assert json.loads(roots).index(str(shadow)) == 3
        assert json.loads(roots)[0] == paths["stdlib"]
    launch = [sys.executable, "-I", "-S", "-B", "-c", pra1353_probe._LAUNCH, roots, str(report)]
    if child == "c10":
        launch[5:6] = [pra1353_c10._child_source()]
        launch.append("2")
    done = subprocess.run(launch, capture_output=True, text=True, cwd=tmp_path, check=False)
    assert done.returncode == 0, done.stderr
    modules = {item["name"]: item["file"] for item in read_module_report(report)["modules"]}
    if stdlib_first:
        assert not marker.exists()
        assert Path(modules["csv"]).parent == Path(paths["stdlib"])
    else:
        assert marker.exists()


@pytest.mark.parametrize("target", [b"../../outside.py", b"/tmp/outside.py"])
def test_variant_4_tracked_symlink_source_is_rejected(target: bytes, tmp_path: Path):
    repo = _repo(tmp_path / "repo", {**_base(), "src/cairnwork/link.py": ("120000", target)})
    with pytest.raises(ValueError, match="invalid tracked source entry"):
        tracked_py_blobs(repo, prefix="src/")
    with pytest.raises(ValueError, match="unapproved source mode"):
        _load_gate()._source_blobs(repo)


@pytest.mark.parametrize(
    "names",
    [
        ("src/cairnwork/zz_case.py", "src/cairnwork/ZZ_case.py"),
        ("src/cairnwork/ß.py", "src/cairnwork/ss.py"),
        ("src/cairnwork/é.py", "src/cairnwork/é.py"),
    ],
)
def test_variant_5_casefold_or_unicode_path_collisions_fail(names: tuple[str, str], tmp_path: Path):
    entries = {**_base(), **{name: ("100644", b"VALUE=3\n") for name in names}}
    repo = _repo(tmp_path / "repo", entries)
    with pytest.raises(ValueError, match="colliding tracked source paths"):
        tracked_py_blobs(repo, prefix="src/")


@pytest.mark.parametrize(
    "name",
    [
        "src/runpy.py",
        "src/product/evil.py",
        "src/cairnwork_newns/module.py",
        "src/cairnwork/no_init/module.py",
    ],
)
def test_variant_6_only_regular_product_packages_are_sealed(name: str, tmp_path: Path):
    repo = _repo(tmp_path / "repo", {**_base(), name: ("100644", b"VALUE=4\n")})
    with pytest.raises(ValueError):
        tracked_py_blobs(repo, prefix="src/")


def test_variant_7_ci_gate_command_is_isolated_from_repo_pytest_code():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    match = re.search(r"run: (uv run --no-sync python -I -S -B tests/pra1353_gate\.py)", workflow)
    assert match is not None
    command = match.group(1)
    assert command.endswith("tests/pra1353_gate.py")
    gate = (ROOT / "tests/pra1353_gate.py").read_text()
    assert "-c','/dev/null" in gate
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in gate
    assert "cwd=sealed" in gate


def test_root_conftest_and_ini_are_not_copied_into_gate_tree():
    source = (ROOT / "tests/pra1353_gate.py").read_text()
    assert "'--noconftest'" in source
    assert "--rootdir=" in source
    assert "confcutdir=" in source


def test_sitecustomize_in_checkout_is_not_part_of_child_sys_path():
    source_paths = tracked_py_blobs(ROOT, prefix="src/")
    assert "src/sitecustomize.py" not in source_paths


def test_cwd_module_does_not_enter_explicit_child_import_roots():
    source = (ROOT / "tests/pra1353_runner.py").read_text()
    assert "sys.path[:] = [stdlib, platstdlib, dynload, src, tests, purelib, platlib]" in source
    assert 'os.environ["PRA1353_IMPORT_ROOTS"]' in source


def _gate_repo(root: Path, stub: bool = True) -> None:
    shutil.copytree(
        ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (root / "tests").mkdir()
    for name in PINS:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    gate_data = (ROOT / "tests/pra1353_gate.py").read_bytes()
    (root / "tests/pra1353_gate.py").write_bytes(gate_data)
    if stub:
        pins = {
            name: digest
            for name, digest in PINS.items()
            if name != "tests/test_1039_1353_witnesses.py"
        }
        witness = "PINS = " + repr(pins) + "\ndef test_stub_witness():\n    assert True\n"
        (root / "tests/test_1039_1353_witnesses.py").write_text(witness)
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=GIT_ENV)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=GIT_ENV)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=gate-test",
            "-c",
            "user.email=gate@test.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
        env=GIT_ENV,
    )


def _run_gate(root: Path) -> subprocess.CompletedProcess[str]:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    match = re.search(r"run: (uv run --no-sync python -I -S -B tests/pra1353_gate\.py)", workflow)
    assert match is not None
    command = shlex.split(match.group(1))
    args = [sys.executable, *command[4:]]
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=300, check=False)


def test_gate_ignores_root_pytest_hooks_and_unpinned_cwd_modules(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _gate_repo(root)
    marker = root / "marker"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n"
    (root / "conftest.py").write_text(code)
    (root / "pytest.ini").write_text("[pytest]\naddopts = -p evil\n")
    (root / "setup.cfg").write_text("[tool:pytest]\naddopts = -p evil\n")
    (root / "json.py").write_text(code)
    unpinned = root / "tests/test_zz_unpinned.py"
    unpinned.write_text(code)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=GIT_ENV)
    result = _run_gate(root)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_gate_rejects_sitecustomize_before_it_can_write_marker(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _gate_repo(root)
    marker = root / "site-marker"
    path = root / "src/sitecustomize.py"
    path.write_text(f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=GIT_ENV)
    result = _run_gate(root)
    assert result.returncode != 0
    assert not marker.exists()


def _load_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pra1353_gate_probe", ROOT / "tests/pra1353_gate.py"
    )
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


@pytest.mark.parametrize(
    "extra",
    [
        "tests/test_1039_1353_witneßes.py",
        "tests/TEST_1039_1353_witnesses.py",
        "tests/PRA1353_gate.py",
        "tests/PRA1353_SEAL.py",
        "tests/pra1353_se\u0061\u0301l.py",
    ],
)
def test_gate_rejects_pin_paths_that_collide_after_normalisation(extra: str):
    pins = {
        "tests/test_1039_1353_witnesses.py": "0" * 64,
        "tests/pra1353_seal.py": "1" * 64,
        "tests/pra1353_se\u00e1l.py": "3" * 64,
        extra: "2" * 64,
    }
    with pytest.raises(ValueError, match="colliding pin paths"):
        _load_gate()._validate_pins(pins)


@pytest.mark.parametrize("name", ["purelib", "platlib"])
def test_no_site_gate_and_consumer_find_the_venv_site_packages(name: str):
    expected = sysconfig.get_paths()[name]
    gate = str(ROOT / "tests/pra1353_gate.py")
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('g', {gate!r})\n"
        "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)\n"
        f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
        "from pra1353_seal import interpreter_paths\n"
        f"print(g.sysconfig_path({name!r})); print(interpreter_paths()[{name!r}])\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True, check=True
    )
    assert done.stdout.split() == [expected, expected]


def test_gate_git_env_disables_repo_fsmonitor():
    gate = _load_gate()
    assert gate._GIT_ENV["GIT_CONFIG_COUNT"] == "1"
    assert gate._GIT_ENV["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert gate._GIT_ENV["GIT_CONFIG_VALUE_0"] == "false"
    assert gate._GIT_ENV["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_consumer_passes_seven_roots_when_stdlib_equals_platstdlib(monkeypatch, tmp_path: Path):
    paths = dict(consume.interpreter_paths())
    paths["platstdlib"] = paths["stdlib"]
    monkeypatch.setattr(consume, "interpreter_paths", lambda: paths)
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        raise RuntimeError("stop after argv")

    with pytest.raises(RuntimeError, match="stop after argv"):
        consume.launch(PINS, run=fake_run)
    assert len(seen[0][5:]) == 7
