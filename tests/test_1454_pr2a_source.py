"""#1454 step 2a: `pra1353_gate.py --source <dir>` judges a PR tree with the gate of main.

Gate, PINS, witness and pinned tests are read from the working directory (the main checkout);
only `src/` and PRA1353_SOURCE_REPO come from `<dir>` (the PR head). Offline, no network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_1039_1353_witnesses import PINS
from test_1454_b2_lock_trust import GIT_ENV, ROOT, _venv_copy

_WITNESS = "tests/test_1039_1353_witnesses.py"
_SRC = "src/cairnwork/__init__.py"
# Main's witness: judges the tree in PRA1353_SOURCE_REPO and checks that is what was sealed.
MAIN_WITNESS = """
import os
from pathlib import Path

def test_the_sealed_src_is_the_source_trees_src_and_is_not_broken():
    seen = (Path(os.environ["PRA1353_SOURCE_REPO"]) / "src/cairnwork/__init__.py").read_text()
    sealed = Path(__file__).resolve().parents[1] / "src/cairnwork/__init__.py"
    assert "BROKEN" not in seen
    assert sealed.read_text() == seen
"""


def _git(root: Path, *args: str) -> None:
    command = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args]
    subprocess.run(command, check=True, env=GIT_ENV, capture_output=True)


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")


def _write_witness(root: Path, body: str, pins: dict[str, str]) -> None:
    (root / _WITNESS).write_text(f"PINS = {pins!r}\n{body}")


def _main_repo(root: Path) -> None:
    for name in [*PINS, "tests/pra1353_gate.py"]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, root / name)
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "uv.lock", root / "uv.lock")
    _write_witness(root, MAIN_WITNESS, {k: v for k, v in PINS.items() if k != _WITNESS})
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=GIT_ENV)
    _commit(root)


def _head_repo(main: Path, head: Path) -> None:
    shutil.copytree(main, head)


def _append(root: Path, name: str, text: str) -> None:
    with (root / name).open("a") as handle:
        handle.write(text)


def _tamper_head(head: Path) -> None:
    """A self-consistent tamper: broken src, a witness that passes, a gate that says 0, new PINS."""
    _append(head, _SRC, "\nBROKEN = True\n")
    gate = head / "tests/pra1353_gate.py"
    gate.write_text("raise SystemExit(0)\n")
    pins = {k: v for k, v in PINS.items() if k != _WITNESS}
    pins["tests/pra1353_gate.py"] = hashlib.sha256(gate.read_bytes()).hexdigest()
    _write_witness(head, "def test_always_passes():\n    assert True\n", pins)
    _append(head, "uv.lock", "# head edited its own lock\n")
    _commit(head)


def _run(
    python: Path | str, cwd: Path, *args: str, lock: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    env["PRA1353_BASE_LOCK"] = str(lock or cwd / "uv.lock")
    command = [str(python), "-I", "-S", "-B", "tests/pra1353_gate.py", *args]
    return subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=900, check=False
    )


@pytest.fixture(scope="module")
def honest_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _venv_copy(tmp_path_factory.mktemp("honest-venv"), tamper=False)


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    main, head = tmp_path / "main", tmp_path / "head"
    _main_repo(main)
    _head_repo(main, head)
    return main, head


def test_c_honest_head_with_a_src_change_passes(trees, honest_python):
    main, head = trees
    _append(head, _SRC, "\n# an honest src change\n")
    _commit(head)
    result = _run(honest_python, main, "--source", str(head))
    assert result.returncode == 0, result.stderr[-2000:]


def test_c_docs_only_head_passes(trees, honest_python):
    main, head = trees
    (head / "README.md").write_text("docs only\n")
    _commit(head)
    result = _run(honest_python, main, "--source", str(head))
    assert result.returncode == 0, result.stderr[-2000:]


def test_c_unrelated_pins_edit_in_head_is_ignored_by_the_gate(trees, honest_python):
    main, head = trees
    pins = {k: v for k, v in PINS.items() if k != _WITNESS}
    pins["tests/xdist_groups.py"] = "0" * 64
    _write_witness(head, MAIN_WITNESS, pins)
    _commit(head)
    result = _run(honest_python, main, "--source", str(head))
    assert result.returncode == 0, result.stderr[-2000:]


def test_b_tampered_head_is_judged_by_mains_witness_on_heads_src(trees, honest_python):
    main, head = trees
    _tamper_head(head)
    # Control: with no --source the head judges itself with its own gate, pins and witness.
    assert _run(honest_python, head).returncode == 0
    result = _run(honest_python, main, "--source", str(head))
    assert result.returncode == 1, result.stderr[-2000:]
    assert "BROKEN" in result.stdout + result.stderr


def test_b_head_lock_and_fake_venv_are_refused_with_the_main_lock(trees, tmp_path):
    main, head = trees
    _tamper_head(head)
    fake = _venv_copy(tmp_path / "fake", tamper=True)
    result = _run(fake, main, "--source", str(head), lock=main / "uv.lock")
    assert result.returncode == 3, result.stderr[-2000:]
    assert "UNTRUSTED_DEPENDENCY" in result.stderr


def test_control_without_source_is_unchanged(trees, honest_python):
    main, _ = trees
    assert _run(honest_python, main).returncode == 0
    _append(main, _SRC, "\nBROKEN = True\n")
    _commit(main)
    assert _run(honest_python, main).returncode == 1


def _args_error(trees, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    main, _ = trees
    result = _run(sys.executable, cwd or main, *args)
    assert result.returncode != 0, result.stdout
    return result


def test_source_without_a_directory_is_refused(trees, tmp_path):
    for args in (("--source",), ("--source", str(tmp_path / "missing")), ("--source=head",)):
        assert "unexpected gate arguments" in _args_error(trees, *args).stderr
    (tmp_path / "file").write_text("x")
    assert (
        "unexpected gate arguments" in _args_error(trees, "--source", str(tmp_path / "file")).stderr
    )


def test_extra_arguments_are_still_refused(trees):
    _, head = trees
    for args in (("--other",), ("--source", str(head), "extra"), ("extra", "--source", str(head))):
        assert "unexpected gate arguments" in _args_error(trees, *args).stderr


def test_source_may_not_be_the_cwd_inside_it_or_around_it(trees):
    main, _ = trees
    for source in (main, main / "src", main.parent, Path(str(main) + "/./tests/..")):
        assert "must not overlap" in _args_error(trees, "--source", str(source)).stderr


def test_source_through_a_symlink_to_the_cwd_is_refused(trees, tmp_path):
    main, _ = trees
    (tmp_path / "alias").symlink_to(main)
    assert "must not overlap" in _args_error(trees, "--source", str(tmp_path / "alias")).stderr


def test_source_must_be_the_root_of_a_git_checkout(trees, tmp_path):
    (tmp_path / "plain").mkdir()
    assert (
        "root of a Git checkout" in _args_error(trees, "--source", str(tmp_path / "plain")).stderr
    )
    _, head = trees
    assert "root of a Git checkout" in _args_error(trees, "--source", str(head / "src")).stderr


def _load_gate(main: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pra1353_gate_src", main / "tests/pra1353_gate.py"
    )
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def test_main_reads_gate_inputs_from_cwd_and_only_src_from_source(trees, monkeypatch):
    main, head = trees
    gate = _load_gate(main)
    seen: dict[str, object] = {}
    real_run = subprocess.run

    def record(key: str, value: object):
        def fake(*args: object, **kwargs: object) -> object:
            seen[key] = args
            return value

        return fake

    def seal(repo: Path, root: Path, pins: dict[str, str], source: Path) -> Path:
        seen["seal"] = (repo, source)
        (root / "sealed").mkdir()
        return root / "sealed"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return real_run(command, **kwargs)  # type: ignore[call-overload]
        seen["child"] = command[-2:]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.chdir(main)
    monkeypatch.setattr(sys, "argv", ["gate", "--source", str(head)])
    monkeypatch.setattr(gate, "check_base_install", record("trees", None))
    monkeypatch.setattr(gate, "_pins", record("pins", {"tests/pra1353_seal.py": "0" * 64}))
    monkeypatch.setattr(gate, "_base_lock", record("lock", b""))
    monkeypatch.setattr(gate, "_seal", seal)
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == 0
    assert seen["pins"] == (main.resolve(),) and seen["lock"] == (main.resolve(),)
    assert seen["seal"] == (main.resolve(), head.resolve())
    assert seen["trees"][2:] == (main.resolve(), head.resolve())  # type: ignore[index]
    assert seen["child"] == [str(head.resolve()), str(main.resolve())]


def test_sealed_child_names_both_trees_to_the_base_install_check(trees, monkeypatch):
    main, head = trees
    gate = _load_gate(main)
    monkeypatch.setattr(sys, "argv", ["gate", "--sealed", str(head), str(main)])
    called: list[tuple[Path, ...]] = []

    def check(stdlib: str, platstdlib: str, *given: Path) -> None:
        called.append(given)
        raise RuntimeError("stop")

    monkeypatch.setattr(gate, "check_base_install", check)
    sealed = main.parent / "sealed"
    sealed.mkdir()
    monkeypatch.chdir(sealed)
    with pytest.raises(RuntimeError, match="stop"):
        gate.main()
    assert called == [(head, sealed.resolve(), main)]
