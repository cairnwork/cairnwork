"""#1450: the base Python install is recorded before PR code and re-checked at the gate."""

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import cast

import pra1353_base_record as rec
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "pra1353_base_record.py"


def _fake_install(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    (root / "pkg").mkdir(parents=True)
    (root / "os.py").write_text("print('os')\n")
    (root / "pkg" / "mod.py").write_text("X = 1\n")
    (root / "link.py").symlink_to("os.py")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "os.pyc").write_bytes(b"cache")
    (root / "site-packages").mkdir()
    (root / "site-packages" / "extra.py").write_text("ignored\n")
    return root


def _swap_dir_for_symlink(root: Path, *, target_inside: bool = False) -> None:
    """Replace ``pkg`` (same names inside) by a symlink to a copy; the walk does not enter it."""
    import shutil

    copy = (root / "pkg_copy") if target_inside else (root.parent / "elsewhere")
    shutil.copytree(root / "pkg", copy)
    shutil.rmtree(root / "pkg")
    (root / "pkg").symlink_to(copy, target_is_directory=True)


def _digest(root: Path) -> str:
    return rec.tree_digest([str(root)])[0]


def _fake_base_sys_path(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "base"
    base.mkdir()
    zip_path = base / "lib" / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base))
    monkeypatch.setattr(sys, "path", [str(zip_path)])
    return base, zip_path


def _write_zip_module(path: Path, contents: str = "ZIP_SENTINEL = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("shutil.py", contents)


def _snapshot_fake_install(monkeypatch, tmp_path: Path) -> tuple[Path, Path, dict]:
    base, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    install = _fake_install(tmp_path)
    return base, zip_path, rec.snapshot([str(install)])


def test_verify_detects_a_sys_path_zip_created_after_record(monkeypatch, tmp_path):
    _, zip_path, recorded = _snapshot_fake_install(monkeypatch, tmp_path)
    _write_zip_module(zip_path)
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )


def test_verify_detects_a_directory_planted_at_the_sys_path_zip_name(monkeypatch, tmp_path):
    """A directory (with a module in it) at the zip's name imports as an ordinary path entry."""
    _, zip_path, recorded = _snapshot_fake_install(monkeypatch, tmp_path)
    zip_path.mkdir(parents=True)
    (zip_path / "json.py").write_text("DIR_SENTINEL = 1\n")
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )


def test_verify_detects_a_sys_path_zip_modified_after_record(monkeypatch, tmp_path):
    _, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    _write_zip_module(zip_path, "ZIP_SENTINEL = 1\n")
    install = _fake_install(tmp_path)
    recorded = rec.snapshot([str(install)])
    _write_zip_module(zip_path, "ZIP_SENTINEL = 2\n")
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )


def test_verify_detects_a_sys_path_zip_removed_after_record(monkeypatch, tmp_path):
    _, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    _write_zip_module(zip_path)
    install = _fake_install(tmp_path)
    recorded = rec.snapshot([str(install)])
    zip_path.unlink()
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )


def test_unchanged_sys_path_zip_matches_record(monkeypatch, tmp_path):
    _, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    _write_zip_module(zip_path)
    install = _fake_install(tmp_path)
    recorded = rec.snapshot([str(install)])
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert rec.compare(recorded, current) is None


def test_sys_path_regular_file_mode_changes_record(monkeypatch, tmp_path):
    _, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    _write_zip_module(zip_path)
    install = _fake_install(tmp_path)
    recorded = rec.snapshot([str(install)])
    zip_path.chmod(0o755)
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )


def test_sys_path_special_file_fails_closed(monkeypatch, tmp_path):
    _, zip_path = _fake_base_sys_path(monkeypatch, tmp_path)
    zip_path.parent.mkdir(parents=True)
    os.mkfifo(zip_path)
    install = _fake_install(tmp_path)
    with pytest.raises(OSError, match="not a regular file"):
        rec.snapshot([str(install)])


def test_sys_path_outside_base_prefixes_is_not_recorded(monkeypatch, tmp_path):
    _, outside = _fake_base_sys_path(monkeypatch, tmp_path)
    outside_path = tmp_path / "cwd-like"
    monkeypatch.setattr(sys, "path", [str(outside), str(outside_path)])
    install = _fake_install(tmp_path)
    current = rec.snapshot([str(install)])
    assert current["path_extras"] == [[str(outside), "absent"]]


def test_digest_ignores_only_a_roots_own_site_packages(tmp_path):
    root = _fake_install(tmp_path)
    before = _digest(root)
    (root / "site-packages" / "later.py").write_text("not read by the -I -S gate\n")
    assert _digest(root) == before


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: (r / "__pycache__" / "os.pyc").write_bytes(b"unchecked-hash pyc swapped in"),
        lambda r: (r / "__pycache__" / "new.pyc").write_bytes(b"added"),
        lambda r: (r / "pkg" / "site-packages").mkdir(),
        lambda r: (
            (r / "pkg" / "site-packages").mkdir(),
            (r / "pkg" / "site-packages" / "evil.py").write_text("x"),
        ),
    ],
    ids=["pyc-swapped", "pyc-added", "nested-site-packages-dir", "nested-site-packages-file"],
)
def test_bytecode_caches_and_nested_site_packages_are_hashed(tmp_path, tamper):
    """A .pyc with an unchecked hash runs without its .py changing, so caches count."""
    root = _fake_install(tmp_path)
    before = _digest(root)
    tamper(root)
    assert _digest(root) != before


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: (r / "os.py").write_text("print('evil')\n"),
        lambda r: (r / "pkg" / "evil.py").write_text("X = 2\n"),
        lambda r: (r / "pkg" / "mod.py").unlink(),
        lambda r: ((r / "link.py").unlink(), (r / "link.py").symlink_to("pkg/mod.py")),
        lambda r: (r / "os.py").chmod(0o755),
        lambda r: (r / "pkg" / "mod.py").rename(r / "pkg" / "other.py"),
        lambda r: (r / "pkg").chmod(0o700),  # a directory's mode
        lambda r: (r / "newdir").mkdir(),  # an empty directory added
        lambda r: _swap_dir_for_symlink(r, target_inside=True),
    ],
    ids=[
        "edit",
        "add",
        "delete",
        "retarget-link",
        "mode",
        "rename",
        "dir-mode",
        "empty-dir",
        "dir-to-symlink-inside",
    ],  # fmt: skip
)
def test_any_change_to_the_install_changes_the_digest(tmp_path, tamper):
    root = _fake_install(tmp_path)
    before = _digest(root)
    tamper(root)
    assert _digest(root) != before


def test_special_files_fail_closed(tmp_path):
    root = _fake_install(tmp_path)
    os.mkfifo(root / "fifo")
    with pytest.raises(OSError, match="not a regular file"):
        _digest(root)


def test_compare_names_the_differing_field_and_rejects_bad_records(tmp_path):
    current = rec.snapshot([str(_fake_install(tmp_path))])
    assert rec.compare(dict(current), current) is None
    assert "digest" in str(rec.compare({**current, "digest": "0" * 64}, current))
    assert "base_prefix" in str(rec.compare({**current, "base_prefix": "/elsewhere"}, current))
    old_format = {key: value for key, value in current.items() if key != "path_extras"}
    assert rec.compare(old_format, current) == "record is malformed"
    assert rec.compare({**current, "extra": 1}, current) == "record is malformed"
    assert rec.compare(["not", "a", "dict"], current) == "record is malformed"


def _run(*args):
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_record_then_verify_on_the_running_interpreter(tmp_path):
    out = tmp_path / "base.json"
    assert _run("record", str(out)).returncode == 0
    assert _run("verify", str(out)).returncode == 0
    assert _run("record", str(out)).returncode == 1  # never overwrites a record


def test_verify_fails_closed_without_or_with_a_tampered_record(tmp_path):
    assert _run("verify", str(tmp_path / "missing.json")).returncode == 1
    out = tmp_path / "base.json"
    _run("record", str(out))
    data = json.loads(out.read_text())
    data["digest"] = "0" * 64
    out.write_text(json.dumps(data))
    assert _run("verify", str(out)).returncode == 1
    out.write_text("not json")
    assert _run("verify", str(out)).returncode == 1
    assert _run("verify").returncode == 2


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n  (?=[a-z][a-z0-9_-]*:\n)", "\n" + body)
    return {p.split(":", 1)[0].strip(): p for p in parts if p.strip()}


@pytest.mark.parametrize("job", ["test", "test-macos"])
def test_ci_records_in_the_trusted_venv_step_and_verifies_in_the_gate_step(job):
    steps = _jobs((ROOT / ".github/workflows/ci.yml").read_text())[job]
    build = steps.find("- name: Trusted #1353 gate venv")
    record = steps.find("pra1353_base_record.py", build)
    sync = steps.find("uv sync --locked")
    gate_step = steps.find("- name: Sealed #1353 witness gate")
    verify = steps.find("pra1353_base_record.py", gate_step)
    gate = steps.find("tests/pra1353_gate.py", gate_step)
    assert 0 < build < record < gate_step < verify < gate < sync
    assert (
        "record" in steps[record : record + 200] and "base-python.json" in steps[record:gate_step]
    )
    assert (
        "verify" in steps[verify : verify + 120]
        and "$RUNNER_TEMP/base-python.json" in steps[verify:gate]
    )
    assert steps[verify - 160 : verify].count("pra1353-trusted/.venv/bin/python") == 1


def test_a_symlink_to_a_directory_inside_the_trees_is_part_of_the_digest(tmp_path):
    """os.walk lists such a link among the directories without entering it: retargeting it, or
    swapping an empty directory for one, must still change the digest."""
    root = _fake_install(tmp_path)
    (root / "other").mkdir()
    (root / "empty").mkdir()
    (root / "alias").symlink_to(root / "pkg", target_is_directory=True)
    before = _digest(root)
    (root / "alias").unlink()
    (root / "alias").symlink_to(root / "other", target_is_directory=True)  # retargeted
    retargeted = _digest(root)
    (root / "empty").rmdir()
    (root / "empty").symlink_to(root / "pkg", target_is_directory=True)  # empty dir -> link
    assert len({before, retargeted, _digest(root)}) == 3


def test_the_root_directory_itself_is_part_of_the_digest(tmp_path):
    root = _fake_install(tmp_path)
    before = _digest(root)
    root.chmod(0o700)
    assert _digest(root) != before


def test_a_symlink_to_a_file_outside_the_tree_counts_with_that_files_content(tmp_path):
    """A symlink to a regular file includes the target's content in the tree digest."""
    root = _fake_install(tmp_path)
    outside = tmp_path / "outside-python"
    outside.write_text("v1")
    (root / "python").symlink_to(outside)
    before = _digest(root)
    outside.write_text("v2")
    assert _digest(root) != before


def test_a_dangling_symlink_is_stable(tmp_path):
    root = _fake_install(tmp_path)
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    first = _digest(root)
    assert _digest(root) == first
    (root / "dangling").unlink()
    (root / "dangling").symlink_to(tmp_path / "elsewhere")
    assert _digest(root) != first


def test_a_symlink_to_a_directory_outside_the_trees_fails_closed(tmp_path):
    """Its content would go unseen, so recording (and verifying) refuses it."""
    root = _fake_install(tmp_path)
    (tmp_path / "dir-a").mkdir()
    (root / "outlink").symlink_to(tmp_path / "dir-a", target_is_directory=True)
    with pytest.raises(OSError, match="outside the trees"):
        _digest(root)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_an_unreadable_subdirectory_fails_instead_of_being_skipped(tmp_path):
    root = _fake_install(tmp_path)
    (root / "pkg").chmod(0o000)
    try:
        with pytest.raises(OSError, match="Permission"):
            _digest(root)
    finally:
        (root / "pkg").chmod(0o755)


def _gate_script() -> str:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    block = text.split("- name: Sealed #1353 witness gate\n", 1)[1].split("\n      - ", 1)[0]
    run = block.split("run: |\n", 1)[1]
    return "\n".join(line[10:] for line in run.splitlines())


@pytest.mark.parametrize("verify_rc, gate_rc", [(0, 0), (0, 3), (1, 0), (2, 0)])
def test_ci_gate_step_skips_the_gate_when_verify_fails(tmp_path, verify_rc, gate_rc):
    """Run the real step text with stub interpreters: verify first, the gate only if it passed."""
    bin_dir = tmp_path / "pra1353-trusted" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    log = tmp_path / "calls.log"
    stub = bin_dir / "python"
    stub.write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        f"  *base_record.py*verify*) echo VERIFY >> {log}; exit {verify_rc} ;;\n"
        f"  *pra1353_gate.py*) echo GATE >> {log}; exit {gate_rc} ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    done = subprocess.run(  # noqa: S603 - the CI step text with stubbed interpreters
        ["bash", "-c", _gate_script()],
        env={"PATH": "/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().split()
    assert calls[0] == "VERIFY"
    assert (calls[1:] == ["GATE"]) == (verify_rc == 0)
    expected = verify_rc or gate_rc
    assert (done.returncode != 0) == (expected != 0)
