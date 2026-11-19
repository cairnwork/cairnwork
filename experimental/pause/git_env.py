"""Environment isolation shared by PAUSE's Git subprocesses."""

from __future__ import annotations

import os


def hardened_git_env() -> dict[str, str]:
    """Return the caller environment without inherited Git controls."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_NO_REPLACE_OBJECTS="1",
    )
    return env
