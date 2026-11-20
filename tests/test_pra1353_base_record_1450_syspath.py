"""#1450 follow-up: the base install record also covers sys.path entries under the base prefixes."""

import os
import sys
import zipfile
from pathlib import Path
from typing import cast

import pra1353_base_record as rec
import pytest
from test_pra1353_base_record_1450 import _fake_install


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


def test_verify_detects_a_zip_spelled_through_a_symlinked_base_prefix(monkeypatch, tmp_path):
    """base_prefix is a symlink but sys.path spells the real path: the entry is still recorded."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    zip_path = real / "lib" / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    monkeypatch.setattr(sys, "base_prefix", str(link))
    monkeypatch.setattr(sys, "base_exec_prefix", str(link))
    monkeypatch.setattr(sys, "path", [str(zip_path)])
    install = _fake_install(tmp_path)
    recorded = rec.snapshot([str(install)])
    _write_zip_module(zip_path)
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
    extras = cast(list[list[str]], recorded["path_extras"])
    # teeth: the entry really is in the record, with a content digest (not "absent"), so this
    # test cannot pass when the feature is removed
    assert [path for path, _ in extras] == [str(zip_path)]
    assert extras[0][1] != "absent" and len(extras[0][1]) == 64
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


def test_a_symlink_outside_the_roots_into_them_is_recorded_and_retargeting_is_seen(
    monkeypatch, tmp_path
):
    """A sys.path symlink that stands outside the roots but points into them must not be skipped:
    the roots' digest hashes the tree it points at, not the link, so retargeting it between two
    directories of the same root would otherwise go unseen."""
    base = tmp_path / "base"
    base.mkdir()
    install = _fake_install(tmp_path)
    sub_a, sub_b = install / "sub_a", install / "sub_b"
    sub_a.mkdir()
    sub_b.mkdir()
    link = base / "extra"
    link.symlink_to(sub_a)
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base))
    monkeypatch.setattr(sys, "path", [str(link)])
    recorded = rec.snapshot([str(install)])
    assert [path for path, _ in cast(list[list[str]], recorded["path_extras"])] == [str(link)]
    link.unlink()
    link.symlink_to(sub_b)
    current = rec.snapshot(cast(list[str], recorded["roots"]))
    assert (
        rec.compare(recorded, current)
        == "path_extras differs from the recorded base Python install"
    )
