"""The two macOS CI shards run every test file exactly once, with balanced load (ci.yml)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "ci"))

import ci_macos_shard as shards  # noqa: E402
import pin_guard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TABLE = json.loads(shards.WEIGHTS.read_text())
GROUPS = shards.xdist_group_of(ROOT)


def test_every_test_file_is_in_exactly_one_shard_and_the_witness_file_in_none():
    everything = shards.test_files(ROOT)
    per_shard = shards.assign(everything, TABLE, GROUPS)
    joined = sorted(path for files in per_shard.values() for path in files)
    assert joined == everything, "a test file is lost or in two shards"
    assert shards.WITNESS_FILE not in joined
    assert (ROOT / shards.WITNESS_FILE).is_file()


def test_the_predicted_loads_of_the_two_shards_differ_by_at_most_ten_percent():
    load = shards.loads(shards.assign(shards.test_files(ROOT), TABLE, GROUPS), TABLE)
    low, high = sorted(load.values())
    assert (high - low) / high <= 0.10, load


def test_a_new_test_file_the_table_does_not_know_lands_in_exactly_one_shard():
    paths = [*shards.test_files(ROOT), "tests/test_brand_new_file.py"]
    per_shard = shards.assign(paths, TABLE, GROUPS)
    assert sum("tests/test_brand_new_file.py" in files for files in per_shard.values()) == 1


def test_a_shard_is_its_assigned_files_in_sorted_order():
    chosen = shards.files_for("1", ROOT)
    assert chosen and chosen == sorted(chosen)
    assert chosen == shards.assign(shards.test_files(ROOT), TABLE, GROUPS)["1"]


def test_files_pytest_collects_by_default_are_all_found_including_the_star_test_suffix(tmp_path):
    for name in (
        "test_a.py",
        "sub/test_b.py",
        "regression_test.py",
        "sub/other_test.py",
        "helper.py",
    ):
        (tmp_path / "tests" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / name).write_text("")
    assert shards.test_files(tmp_path) == [
        "tests/regression_test.py",
        "tests/sub/other_test.py",
        "tests/sub/test_b.py",
        "tests/test_a.py",
    ]


def test_the_files_of_one_xdist_group_are_on_one_shard():
    per_shard = shards.assign(shards.test_files(ROOT), TABLE, GROUPS)
    where = {Path(p).name: name for name, files in per_shard.items() for p in files}
    for group in set(GROUPS.values()):
        members = {where[n] for n, g in GROUPS.items() if g == group and n in where}
        assert len(members) == 1, f"xdist group {group} is split over shards {members}"


def test_the_shards_cover_exactly_the_files_pytest_collects():
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         f"--ignore={shards.WITNESS_FILE}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    out = done.stdout
    collected = {line.split("::")[0] for line in out.splitlines() if "::" in line}
    per_shard = shards.assign(shards.test_files(ROOT), TABLE, GROUPS)
    assert collected == {p for files in per_shard.values() for p in files}


def test_the_shard_script_is_a_pin_guarded_path_like_ci_yml():
    """It now decides which test files run, so a PR must not change it unapproved."""
    assert ".github/workflows/ci.yml" in pin_guard.FIXED_PATHS
    assert "scripts/ci_macos_shard.py" in pin_guard.FIXED_PATHS
