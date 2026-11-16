"""Every experimental capability must carry a readable, unexpired EXPIRES date (#1249).

README and AGENTS.md give experimental capabilities a 14-day expiry, but nothing read the files,
so an expired capability stayed in the tree. This test only adds a check: no verdict logic changes.
"""

import datetime
from pathlib import Path

import pytest

EXPERIMENTAL = Path(__file__).resolve().parent.parent / "experimental"

# Expired and awaiting the owner's renew / promote / delete decision (#1249 option 1). Remove the
# entry when that decision lands; an entry here is the only way past the check.
PENDING_DECISION = {"family_gate": "owner decision pending (#1249)"}


def expiry_problems(root: Path, today: datetime.date, pending=()) -> list[str]:
    """One line per capability under ``root`` whose EXPIRES is missing, unreadable or past."""
    problems = []
    for cap in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    ):
        if not (cap / "__init__.py").exists() or cap.name in pending:
            continue
        try:
            when = datetime.date.fromisoformat((cap / "EXPIRES").read_text().strip())
        except (OSError, ValueError, UnicodeDecodeError):
            problems.append(f"{cap.name}: EXPIRES is missing or not an ISO date")
            continue
        if when < today:
            problems.append(f"{cap.name}: expired on {when.isoformat()}")
    return problems


def capability(root: Path, name: str, expires: str | None) -> None:
    (root / name).mkdir()
    (root / name / "__init__.py").write_text("")
    if expires is not None:
        (root / name / "EXPIRES").write_text(expires)


TODAY = datetime.date(2026, 11, 16)


@pytest.mark.parametrize(
    "expires, fragment",
    [
        ("2026-11-15\n", "expired on 2026-11-15"),
        ("2026-11-11", "expired on 2026-11-11"),
        (None, "missing or not an ISO date"),
        ("", "missing or not an ISO date"),
        ("next week\n", "missing or not an ISO date"),
        ("2026-13-40\n", "missing or not an ISO date"),
    ],
)
def test_an_expired_or_unreadable_expiry_is_reported(tmp_path, expires, fragment):
    capability(tmp_path, "gate", expires)
    found = expiry_problems(tmp_path, TODAY)
    assert len(found) == 1 and "gate" in found[0] and fragment in found[0]


@pytest.mark.parametrize("expires", ["2026-11-16\n", "2026-11-17", "2027-01-01\n"])
def test_a_current_expiry_passes(tmp_path, expires):
    capability(tmp_path, "gate", expires)
    assert expiry_problems(tmp_path, TODAY) == []


def test_only_a_named_pending_capability_may_be_expired(tmp_path):
    capability(tmp_path, "gate", "2026-11-01\n")
    capability(tmp_path, "other", "2026-11-01\n")
    found = expiry_problems(tmp_path, TODAY, pending={"gate"})
    assert [line.split(":")[0] for line in found] == ["other"]


def test_a_directory_that_is_not_a_package_is_ignored(tmp_path):
    (tmp_path / "notes").mkdir()
    assert expiry_problems(tmp_path, TODAY) == []


def test_no_experimental_capability_in_the_tree_is_expired():
    assert expiry_problems(EXPERIMENTAL, datetime.date.today(), pending=PENDING_DECISION) == []


def test_the_pending_list_names_only_real_capabilities():
    assert set(PENDING_DECISION) <= {p.name for p in EXPERIMENTAL.iterdir() if p.is_dir()}
