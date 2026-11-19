"""Pin a base commit in the intake mirror without moving FETCH_HEAD."""

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.intake_request import IntakeRefused


def mirror_base(mirror: Path, source: Path, base: str) -> None:
    """Fetch and pin base without FETCH_HEAD, locking parallel fetches out of gc."""
    try:
        run_git(
            mirror, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(source),
            f"{base}:refs/cairnwork-bases/{base}",  # outside the guarded refs/cairnwork/
        )  # fmt: skip
    except GitCommandError as exc:
        if not isinstance(exc.__cause__, subprocess.CalledProcessError):
            raise OSError(f"Cairnwork could not run git: {exc}") from exc
        raise IntakeRefused("BAD_REQUEST", f"cannot fetch {base} from {source}") from exc
