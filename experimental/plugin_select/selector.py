"""Which installed table plugins a verifier run loads (#851 step 2).

Default is the main behaviour: every installed plugin loads. A table plugin (the PLUGIN_NAMES
rows, plus the asyncio row below) is left out only when the scan of what the run executes is
fully classified and uses none of its names, and the base config sets none of its ini keys.
Plugins outside the table always load. The asyncio row is not in PLUGIN_NAMES (a change there
would change attribution text, a step-0 concern), so its names live here with the same rule.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError
from cairnwork.plugin_names import selection_names
from cairnwork.pytest_config import PLUGINS, PytestConfigError, base_settings
from experimental.plugin_select.scanner import Scan, scan_run

ASYNCIO_MODULE = "pytest_asyncio.plugin"
# Names a table plugin's use can show up as beyond selection_names(): markers and the asyncio row.
# Conservative: a name missing here can only exclude a plugin a run needs, so each is listed.
_EXTRA_NAMES: dict[str, tuple[str, ...]] = {
    ASYNCIO_MODULE: ("asyncio", "event_loop", "pytest_asyncio"),
    "pytest_django.plugin": ("django_db",),
    "anyio.pytest_plugin": ("anyio",),
}
_CONFIG_KEYS = {module: frozenset(keys) for _, module, keys in PLUGINS}
_FIXTURE_NOT_FOUND = re.compile(r"fixture '([A-Za-z_]\w*)' not found")
_UNKNOWN_MARK = re.compile(r"'([A-Za-z_]\w*)' not found in `markers`|Unknown pytest\.mark\.(\w+)")
_ASYNC_ERROR = "async def functions are not natively supported"


def name_modules() -> dict[str, str]:
    """Every name that marks a use of a selectable plugin -> its module."""
    found = dict(selection_names())
    for module, names in _EXTRA_NAMES.items():
        found.update({name: module for name in names})
    return found


def selectable_modules() -> frozenset[str]:
    return frozenset(name_modules().values())


def plugins_to_load(
    installed: Iterable[str], scan: Scan, config_keys: frozenset[str]
) -> tuple[str, ...]:
    """The installed plugins to pass with -p. Unknown scan or any pytest_plugins in the config
    means every installed plugin loads; otherwise a table plugin loads when a name or an ini key
    of it is used."""
    installed = tuple(installed)
    if not scan.classified or "pytest_plugins" in config_keys:
        return installed
    names = name_modules()
    used = {names[n] for n in scan.names if n in names}
    used |= {m for m, keys in _CONFIG_KEYS.items() if keys & config_keys}
    table = selectable_modules()
    return tuple(m for m in installed if m not in table or m in used)


def plan(
    repo: Path, base: str, targets: Iterable[str], installed: Iterable[str],
    candidate: Path | None = None,
) -> tuple[str, ...]:  # fmt: skip
    """The plugins for one verifier run. Any failure to read the base config or scan falls back
    to every installed plugin (never a smaller set)."""
    installed = tuple(installed)
    try:
        settings = base_settings(repo, base)
        keys = {k for k, _ in settings.values} | set(settings.dropped)
    except (PytestConfigError, GitCommandError, OSError, subprocess.SubprocessError):
        return installed
    scan = scan_run(repo, base, list(targets), candidate)
    return plugins_to_load(installed, scan, frozenset(keys))


def needs_full_rerun(output: str, excluded: Iterable[str]) -> bool:
    """True when a run without the excluded plugins failed for a reason a missing plugin causes
    (AC11). The caller then reruns with every installed plugin and uses that result."""
    excluded = set(excluded)
    excluded_names = {n for n, m in name_modules().items() if m in excluded}
    if _ASYNC_ERROR in output and ASYNCIO_MODULE in excluded:
        return True
    for match in _FIXTURE_NOT_FOUND.finditer(output):
        if match.group(1) in excluded_names:
            return True
    for match in _UNKNOWN_MARK.finditer(output):
        if (match.group(1) or match.group(2)) in excluded_names:
            return True
    return False
