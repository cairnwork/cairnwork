"""Every experimental capability must carry a readable EXPIRES date that is not long past (#1249).

README and AGENTS.md give experimental capabilities a 14-day expiry, but nothing read the files,
so an expired capability stayed in the tree. This test only adds a check: no verdict logic changes.

An expired capability gets ``GRACE_DAYS`` of warning (a WARNING in the test output) so its owner can
renew, promote or delete it; after that the suite is red. A capability awaiting a decision may be
listed in ``PENDING_DECISION`` with an Issue and a deadline; once the deadline passes it is red too.
"""

import datetime
import warnings
from pathlib import Path

import pytest

EXPERIMENTAL = Path(__file__).resolve().parent.parent / "experimental"
GRACE_DAYS = 3

# name -> (Issue, deadline). Expired and awaiting the owner's renew / promote / delete decision
# (#1249 option 1). Remove the entry when the decision lands; past its deadline it is red as well.
PENDING_DECISION = {"family_gate": ("#1249", datetime.date(2026, 11, 20))}

# name -> reason. The owner ordered the capability kept: no 14-day limit, still off by default
# (#476). Its EXPIRES file must say ``retained`` (not a date) and its OWNER file must exist.
OWNER_RETAINED = {
    "pause": "#476 James 11/18 不設到期",
    "local_worker": "#476 pause 的依賴",
}


def expiry_check(
    root: Path, today: datetime.date, pending=None, retained=None
) -> tuple[list[str], list[str]]:
    """(problems, warnings) for every capability package under ``root``."""
    pending = pending or {}
    retained = retained or {}
    problems, notes = [], []
    for cap in sorted(p for p in root.iterdir() if p.is_dir() and p.name[0] not in "_."):
        if not (cap / "__init__.py").exists():
            continue
        if cap.name in retained:
            if not (cap / "OWNER").exists():
                problems.append(f"{cap.name}: retained but OWNER is missing")
            elif (cap / "EXPIRES").read_text().strip() != "retained":
                problems.append(f"{cap.name}: retained but EXPIRES is not 'retained'")
            continue
        if cap.name in pending:
            issue, deadline = pending[cap.name]
            if today > deadline:
                problems.append(f"{cap.name}: pending decision {issue} passed {deadline}")
            continue
        try:
            when = datetime.date.fromisoformat((cap / "EXPIRES").read_text().strip())
        except (OSError, ValueError, UnicodeDecodeError):
            problems.append(f"{cap.name}: EXPIRES is missing or not an ISO date")
            continue
        late = (today - when).days
        if late > GRACE_DAYS:
            problems.append(f"{cap.name}: expired on {when} ({late} days ago)")
        elif late > 0:
            notes.append(f"{cap.name}: expired on {when}; red after {GRACE_DAYS} days")
    return problems, notes


def capability(root: Path, name: str, expires: str | None) -> None:
    (root / name).mkdir()
    (root / name / "__init__.py").write_text("")
    if expires is not None:
        (root / name / "EXPIRES").write_text(expires)


TODAY = datetime.date(2026, 11, 16)


@pytest.mark.parametrize(
    "expires, fragment",
    [
        ("2026-11-12\n", "expired on 2026-11-12"),  # one day past the grace
        ("2026-11-11", "expired on 2026-11-11"),
        ("2025-01-01\n", "expired on 2025-01-01"),
        (None, "missing or not an ISO date"),
        ("", "missing or not an ISO date"),
        ("next week\n", "missing or not an ISO date"),
        ("2026-13-40\n", "missing or not an ISO date"),
    ],
)
def test_a_long_expired_or_unreadable_expiry_is_red(tmp_path, expires, fragment):
    capability(tmp_path, "gate", expires)
    problems, _ = expiry_check(tmp_path, TODAY)
    assert len(problems) == 1 and "gate" in problems[0] and fragment in problems[0]


@pytest.mark.parametrize("expires", ["2026-11-13\n", "2026-11-15", "2026-11-16\n"])
def test_a_recently_expired_capability_only_warns(tmp_path, expires):
    capability(tmp_path, "gate", expires)
    problems, notes = expiry_check(tmp_path, TODAY)
    late = (TODAY - datetime.date.fromisoformat(expires.strip())).days
    assert problems == [] and bool(notes) is (late > 0)


@pytest.mark.parametrize("expires", ["2026-11-17", "2027-01-01\n"])
def test_a_current_expiry_is_clean(tmp_path, expires):
    capability(tmp_path, "gate", expires)
    assert expiry_check(tmp_path, TODAY) == ([], [])


def test_the_grace_boundary_is_exact(tmp_path):
    capability(tmp_path, "gate", "2026-11-13\n")  # 3 days: warns
    assert expiry_check(tmp_path, TODAY)[0] == []
    assert expiry_check(tmp_path, TODAY + datetime.timedelta(days=1))[0] != []


def test_a_pending_capability_may_be_expired_until_its_deadline(tmp_path):
    capability(tmp_path, "gate", "2026-11-01\n")
    capability(tmp_path, "other", "2026-11-01\n")
    pending = {"gate": ("#1", datetime.date(2026, 11, 20))}
    problems, _ = expiry_check(tmp_path, TODAY, pending)
    assert [line.split(":")[0] for line in problems] == ["other"]
    late, _ = expiry_check(tmp_path, datetime.date(2026, 11, 21), pending)
    assert "gate: pending decision #1 passed 2026-11-20" in late


def retained_capability(root: Path, name: str, expires: str, owner: bool = True) -> None:
    capability(root, name, expires)
    if owner:
        (root / name / "OWNER").write_text("someone")


def test_a_retained_capability_is_never_red_for_age(tmp_path):
    retained_capability(tmp_path, "keep", "retained\n")
    far = datetime.date(2030, 1, 1)
    assert expiry_check(tmp_path, far, retained={"keep": "r"}) == ([], [])
    problems, _ = expiry_check(tmp_path, far)  # not listed: "retained" is not an ISO date -> red
    assert len(problems) == 1 and "keep" in problems[0]


def test_an_unlisted_capability_with_an_old_date_stays_red(tmp_path):
    retained_capability(tmp_path, "keep", "retained")
    retained_capability(tmp_path, "other", "2026-01-01\n")
    problems, _ = expiry_check(tmp_path, TODAY, retained={"keep": "r"})
    assert len(problems) == 1 and problems[0].startswith("other: expired")


def test_removing_the_retained_listing_turns_it_red(tmp_path):
    retained_capability(tmp_path, "keep", "2026-01-01\n")
    problems, _ = expiry_check(tmp_path, TODAY, retained={"keep": "r"})
    assert problems == ["keep: retained but EXPIRES is not 'retained'"]
    assert expiry_check(tmp_path, TODAY)[0] != []


def test_a_retained_capability_still_needs_its_owner_file(tmp_path):
    retained_capability(tmp_path, "keep", "retained", owner=False)
    problems, _ = expiry_check(tmp_path, TODAY, retained={"keep": "r"})
    assert problems == ["keep: retained but OWNER is missing"]


def test_a_retained_capability_must_say_retained(tmp_path):
    retained_capability(tmp_path, "keep", "2026-12-01")
    problems, _ = expiry_check(tmp_path, TODAY, retained={"keep": "r"})
    assert problems == ["keep: retained but EXPIRES is not 'retained'"]


def test_a_directory_that_is_not_a_package_is_ignored(tmp_path):
    (tmp_path / "notes").mkdir()
    assert expiry_check(tmp_path, TODAY) == ([], [])


def test_no_experimental_capability_in_the_tree_is_long_expired():
    problems, notes = expiry_check(
        EXPERIMENTAL, datetime.date.today(), PENDING_DECISION, OWNER_RETAINED
    )
    for note in notes:
        warnings.warn(f"WARNING {note}", stacklevel=1)
    assert problems == []


def test_the_pending_list_names_only_real_capabilities():
    assert set(PENDING_DECISION) <= {p.name for p in EXPERIMENTAL.iterdir() if p.is_dir()}


def test_the_retained_list_names_real_capabilities_with_the_marker():
    assert set(OWNER_RETAINED) == {"pause", "local_worker"}
    for name in OWNER_RETAINED:
        assert (EXPERIMENTAL / name / "EXPIRES").read_text().strip() == "retained"
